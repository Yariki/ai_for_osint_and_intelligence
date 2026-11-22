"""The scanner worker's side of the protocol: findings, completion, cancel,
hello and heartbeat, and the host-preparation checks."""

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from aio_pika.abc import AbstractChannel

from ndm.core import broker, messages
from ndm.core.messages import (
    Cancel,
    ChunkDone,
    ChunkPhase,
    ChunkRejected,
    Findings,
    Heartbeat,
    Hello,
    Technique,
)
from ndm.core.settings import Settings
from ndm.scanner import main as scanner_main
from ndm.scanner.hostprep import check_host
from ndm.scanner.main import Worker
from tests.integration.scanner.conftest import (
    Listeners,
    Published,
    chunk,
    closed_ports,
    next_message,
)

# --- A chunk from start to final result ---


async def test_port_scan_chunk_publishes_findings_and_then_its_completion(
    worker: Worker, published: Published, listeners: Listeners
) -> None:
    refused = closed_ports(5)

    await worker.handle_chunk(chunk(ports=[*listeners.ports, *refused]))

    *findings, done = published.messages
    assert findings and all(isinstance(message, Findings) for message in findings)
    open_ports = sorted(port for f in findings for host in f.hosts for port in host.open_ports)  # type: ignore[attr-defined]
    assert open_ports == sorted(listeners.ports)
    assert isinstance(done, ChunkDone)
    assert (done.scan_id, done.chunk_seq, done.probes_sent) == (7, 1, 15)
    assert [(str(c.address), c.closed, c.filtered) for c in done.counts] == [("127.0.0.1", 5, 0)]
    # Every open port has its evidence: the time of the reply and the round trip.
    evidence = {probe for f in findings for host in f.hosts for probe in host.evidence}  # type: ignore[attr-defined]
    assert evidence == {f"tcp:{port}" for port in listeners.ports}


async def test_connections_carry_no_data(
    worker: Worker, published: Published, listeners: Listeners
) -> None:
    await worker.handle_chunk(chunk(ports=listeners.ports))
    for _ in range(50):
        if len(listeners.connections) == 10:
            break
        await asyncio.sleep(0.01)

    # Each listener saw one connection that ended without a byte (FR-SCN-3).
    assert listeners.connections == [b""] * 10


async def test_findings_are_flushed_while_the_chunk_runs(
    worker: Worker, published: Published, listeners: Listeners, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scanner_main, "FLUSH_SECONDS", 0.05)
    ports = [*listeners.ports, *closed_ports(30)]

    await worker.handle_chunk(chunk(ports=ports, pps=50))  # 40 probes take most of a second

    kinds = published.kinds()
    assert kinds[-1] == "chunk_done"
    assert kinds.count("findings") >= 3
    # The first findings were out well before the chunk ended.
    assert published.times[-1] - published.times[0] > 0.3
    # Together the flushes hold each open port exactly once.
    found = [port for m in published.messages[:-1] for host in m.hosts for port in host.open_ports]  # type: ignore[attr-defined]
    assert sorted(found) == sorted(listeners.ports)


async def test_completion_is_the_last_message_so_the_chunk_is_acknowledged_after_it(
    worker: Worker, published: Published, listeners: Listeners
) -> None:
    """The caller acknowledges when handle_chunk returns. By then the final
    result is published, and nothing is published after it."""
    await worker.handle_chunk(chunk(ports=listeners.ports))
    count = len(published.messages)
    assert isinstance(published.messages[-1], ChunkDone)

    await asyncio.sleep(0.7)  # longer than a flush interval

    assert len(published.messages) == count


async def test_discovery_chunk_reports_evidence_and_no_counts(
    worker: Worker, published: Published, listeners: Listeners
) -> None:
    (refused,) = closed_ports(1)
    discovery = chunk(phase=ChunkPhase.DISCOVERY, ports=[listeners.ports[0], refused])

    await worker.handle_chunk(discovery)

    findings, done = published.messages
    assert isinstance(findings, Findings) and isinstance(done, ChunkDone)
    (host,) = findings.hosts
    # An accepted and a refused connection both show that the host is there.
    assert sorted(host.evidence) == sorted([f"tcp:{listeners.ports[0]}", f"tcp:{refused}"])
    assert host.open_ports == []
    assert done.counts == []
    # No ICMP capability here: echo was skipped, the TCP probes were made.
    assert done.probes_sent == 2


async def test_chunk_with_nothing_open_publishes_only_its_completion(
    worker: Worker, published: Published
) -> None:
    await worker.handle_chunk(chunk(ports=closed_ports(3)))

    (done,) = published.messages
    assert isinstance(done, ChunkDone)
    assert [(c.closed, c.filtered) for c in done.counts] == [(3, 0)]


# --- Technique and rate ---


async def test_syn_chunk_is_rejected_when_the_scanner_has_no_syn_capability(
    worker: Worker, published: Published, listeners: Listeners
) -> None:
    await worker.handle_chunk(chunk(ports=listeners.ports, technique=Technique.SYN))

    (rejection,) = published.messages
    assert isinstance(rejection, ChunkRejected) and rejection.reason == "syn_unavailable"
    assert listeners.connections == []


async def test_rate_in_the_chunk_is_clamped_to_the_scanners_own_ceiling(
    make_worker: Callable[..., Worker], published: Published
) -> None:
    worker = make_worker(NDM_ALLOW_NON_PUBLIC_TARGETS="true", NDM_GLOBAL_RATE_PPS="50")
    started = asyncio.get_running_loop().time()

    # The chunk asks for 100,000 packets per second.
    await worker.handle_chunk(chunk(ports=closed_ports(30), pps=100_000))

    assert asyncio.get_running_loop().time() - started >= 0.5


# --- Cancel ---


async def test_cancel_stops_sending_and_returns_what_it_has(
    worker: Worker, published: Published, listeners: Listeners
) -> None:
    ports = [*listeners.ports, *closed_ports(190)]
    task = asyncio.create_task(worker.handle_chunk(chunk(ports=ports, pps=100)))  # 2 seconds
    await asyncio.sleep(0.3)

    worker.cancel(7)
    await asyncio.wait_for(task, timeout=1)

    done = published.messages[-1]
    assert isinstance(done, ChunkDone)
    assert 0 < done.probes_sent < 100

    # A chunk of the cancelled scan that was still in the queue is not probed.
    seen = len(listeners.connections)
    await worker.handle_chunk(chunk(chunk_seq=2, ports=listeners.ports))
    await asyncio.sleep(0.05)
    assert len(listeners.connections) == seen
    assert isinstance(published.messages[-1], ChunkDone) and published.messages[-1].chunk_seq == 1

    # Another scan is not affected.
    await worker.handle_chunk(chunk(scan_id=8, ports=listeners.ports[:1]))
    last = published.messages[-1]
    assert isinstance(last, ChunkDone) and last.scan_id == 8


# --- Hello and heartbeat ---


async def test_without_the_capability_the_hello_reports_no_syn(
    make_worker: Callable[..., Worker],
) -> None:
    worker = make_worker(NDM_EGRESS_ADDRESS="8.8.4.4")

    hello = worker.status(Hello)

    assert isinstance(hello, Hello)
    assert (hello.syn_capable, hello.icmp_capable) == (False, False)
    assert (hello.vantage_point, str(hello.egress_address)) == ("test", "8.8.4.4")
    assert hello.allow_non_public_targets is False


async def test_hello_reports_the_scanners_value_of_the_test_switch(worker: Worker) -> None:
    assert worker.status(Hello).allow_non_public_targets is True


async def test_heartbeat_carries_the_counters(
    worker: Worker, published: Published, listeners: Listeners
) -> None:
    worker.status(Heartbeat)  # starts the rate measurement
    await worker.handle_chunk(chunk(ports=listeners.ports))

    heartbeat = worker.status(Heartbeat)

    assert isinstance(heartbeat, Heartbeat)
    assert heartbeat.probes_sent == 10
    assert heartbeat.current_rate_pps > 0
    assert heartbeat.receive_drops == 0
    # The rate is that of the last interval: with nothing sent since, it is zero.
    await asyncio.sleep(0.01)
    assert worker.status(Heartbeat).current_rate_pps == 0


# --- Host preparation ---


def proc(tmp_path: Path, ephemeral: str | None, rmem_max: str | None) -> str:
    (tmp_path / "net/ipv4").mkdir(parents=True)
    (tmp_path / "net/core").mkdir(parents=True)
    if ephemeral is not None:
        (tmp_path / "net/ipv4/ip_local_port_range").write_text(ephemeral)
    if rmem_max is not None:
        (tmp_path / "net/core/rmem_max").write_text(rmem_max)
    return str(tmp_path)


def test_prepared_host_passes(tmp_path: Path) -> None:
    result = check_host((61000, 61999), proc(tmp_path, "32768\t60999\n", "16777216\n"))

    assert result.syn_allowed is True
    assert result.warnings == []


@pytest.mark.parametrize(
    "ephemeral", ["32768\t61000\n", "61999\t65535\n", "1024\t65535\n", "61500\t61600\n"]
)
def test_overlap_with_the_ephemeral_range_withdraws_the_syn_capability(
    tmp_path: Path, ephemeral: str
) -> None:
    result = check_host((61000, 61999), proc(tmp_path, ephemeral, "16777216\n"))

    assert result.syn_allowed is False
    assert "overlaps net.ipv4.ip_local_port_range" in result.warnings[0]


def test_range_that_cannot_be_read_is_not_assumed_to_be_fine(tmp_path: Path) -> None:
    assert check_host((61000, 61999), proc(tmp_path, None, "16777216\n")).syn_allowed is False
    other = tmp_path / "other"
    assert check_host((61000, 61999), proc(other, "not numbers\n", None)).syn_allowed is False


def test_low_receive_buffer_limit_is_a_warning_only(tmp_path: Path) -> None:
    result = check_host((61000, 61999), proc(tmp_path, "32768\t60999\n", "212992\n"))

    assert result.syn_allowed is True
    assert len(result.warnings) == 1 and "net.core.rmem_max is 212992" in result.warnings[0]


# --- Through a real broker ---


async def test_worker_on_a_broker_acknowledges_after_the_final_result_and_obeys_cancel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    broker_url: str,
    topology: AbstractChannel,
    listeners: Listeners,
) -> None:
    monkeypatch.setenv("NDM_BROKER_URL", broker_url)
    monkeypatch.setenv("NDM_VANTAGE_POINT", "test")
    monkeypatch.setenv("NDM_ALLOW_NON_PUBLIC_TARGETS", "true")
    heartbeat_file = tmp_path / "heartbeat"
    monkeypatch.setattr(scanner_main, "HEARTBEAT_FILE", heartbeat_file)
    scanner = asyncio.create_task(scanner_main.run(Settings()))
    results = await topology.get_queue(broker.QUEUE_RESULTS)
    chunks = await topology.get_queue(broker.chunk_queue("test"))

    async def next_result() -> object:
        delivery = await next_message(results)
        assert delivery is not None
        await delivery.ack()
        return messages.RESULTS.validate_json(delivery.body)

    try:
        assert isinstance(await next_result(), Hello)
        assert heartbeat_file.exists()

        # A slow chunk: while it runs it stays unacknowledged in its queue.
        slow = chunk(ports=[*listeners.ports, *closed_ports(190)], pps=100)
        await broker.publish(topology, broker.EXCHANGE_SCANNER, broker.chunk_queue("test"), slow)
        await asyncio.sleep(0.5)
        state = await topology.declare_queue(broker.chunk_queue("test"), passive=True)
        assert state.declaration_result.message_count == 0  # taken, not yet acknowledged

        # Cancel: the scanner stops and returns what it has.
        await broker.publish(
            topology, broker.EXCHANGE_CONTROL, "", Cancel(scan_id=7), persistent=False
        )
        received: list[object] = []
        while not received or not isinstance(received[-1], ChunkDone):
            received.append(await next_result())
        done = received[-1]
        assert isinstance(done, ChunkDone) and 0 < done.probes_sent < 200
    finally:
        scanner.cancel()
        await asyncio.gather(scanner, return_exceptions=True)

    # Acknowledged after its final result: with the scanner gone it is not delivered again.
    assert await chunks.get(fail=False, timeout=1) is None
