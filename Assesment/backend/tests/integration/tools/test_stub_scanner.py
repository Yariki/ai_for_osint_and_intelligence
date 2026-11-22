"""The stub scanner: its answers, and its behaviour on a real broker."""

import asyncio
import time
from collections.abc import AsyncIterator
from ipaddress import IPv4Address
from typing import Any

import pytest
from aio_pika.abc import AbstractChannel

from ndm.core import broker, messages
from ndm.core.messages import Chunk, ChunkDone, ChunkRejected, Findings, Heartbeat, Hello
from tests.tools.stub_scanner import RecordedScan, StubScanner, load_recorded_scan, respond


@pytest.fixture(scope="module")
def recorded() -> RecordedScan:
    return load_recorded_scan()


def comparable(result: Any) -> dict[str, Any]:
    """A message without what differs between two runs: identifiers and times."""
    body: dict[str, Any] = result.model_dump(mode="json", exclude={"message_id"})
    for host in body.get("hosts", []):
        for evidence in host["evidence"].values():
            evidence.pop("at")
    return body


# --- The answers (no broker needed) -------------------------------------------


def test_recorded_scan_is_what_the_stub_answers(recorded: RecordedScan) -> None:
    replayed = [result for chunk in recorded.chunks for result in respond(chunk, recorded.world)]

    assert [comparable(r) for r in replayed] == [comparable(r) for r in recorded.results]


def test_recorded_scan_is_a_complete_small_scan(recorded: RecordedScan) -> None:
    assert [chunk.chunk_seq for chunk in recorded.chunks] == [0, 1, 2]
    # Every chunk ends with its completion, and findings come before it.
    kinds = [(result.type, result.chunk_seq) for result in recorded.results]
    assert kinds == [
        ("findings", 0),
        ("findings", 0),
        ("chunk_done", 0),
        ("findings", 1),
        ("chunk_done", 1),
        ("chunk_done", 2),
    ]
    assert {result.scan_id for result in recorded.for_scan(77)} == {77}
    assert len({result.message_id for result in recorded.results}) == len(recorded.results)


def test_discovery_finds_the_hosts_that_answer(recorded: RecordedScan) -> None:
    findings = [r for r in respond(recorded.chunks[0], recorded.world) if isinstance(r, Findings)]
    evidence = {str(host.address): sorted(host.evidence) for f in findings for host in f.hosts}

    assert evidence == {
        "8.8.8.1": ["icmp", "tcp:443", "tcp:80"],  # answers ping; resets the closed port 80
        "8.8.8.2": ["tcp:443"],  # no ping, drops closed ports: found by its open port only
        "8.8.8.3": ["icmp", "tcp:443", "tcp:80"],  # nothing open, but it resets
    }
    # 8.8.8.4 has no host and is absent; discovery reports no open port.
    assert all(host.open_ports == [] for f in findings for host in f.hosts)


def test_port_scan_reports_open_ports_and_counts_the_rest(recorded: RecordedScan) -> None:
    results = respond(recorded.chunks[1], recorded.world)  # ports 443 and 22

    (findings, done) = results
    assert isinstance(findings, Findings) and isinstance(done, ChunkDone)
    assert {str(host.address): host.open_ports for host in findings.hosts} == {
        "8.8.8.1": [22, 443],
        "8.8.8.2": [443],
    }
    assert {str(c.address): (c.closed, c.filtered) for c in done.counts} == {
        "8.8.8.1": (0, 0),
        "8.8.8.2": (0, 1),  # drops: port 22 is filtered
        "8.8.8.3": (2, 0),  # resets: both closed
    }
    assert done.probes_sent == 6


def test_address_without_a_host_is_all_filtered(recorded: RecordedScan) -> None:
    chunk = recorded.chunks[1].model_copy(update={"targets": [IPv4Address("8.8.8.4")]})

    (done,) = respond(chunk, recorded.world)

    assert isinstance(done, ChunkDone)
    assert [(c.closed, c.filtered) for c in done.counts] == [(0, 2)]


# --- On a real broker ----------------------------------------------------------


@pytest.fixture
async def stub(
    broker_url: str, topology: AbstractChannel, recorded: RecordedScan
) -> AsyncIterator[StubScanner]:
    scanner = StubScanner(url=broker_url, world=recorded.world)
    yield scanner
    await scanner.stop()


async def send(channel: AbstractChannel, chunks: list[Chunk]) -> None:
    for chunk in chunks:
        await broker.publish(channel, broker.EXCHANGE_SCANNER, broker.chunk_queue("test"), chunk)


async def results(channel: AbstractChannel, count: int, timeout: float = 10.0) -> list[Any]:
    """The next ``count`` messages of the results queue, decoded."""
    queue = await channel.get_queue(broker.QUEUE_RESULTS)
    received: list[Any] = []
    deadline = time.monotonic() + timeout
    while len(received) < count and time.monotonic() < deadline:
        delivery = await queue.get(fail=False, timeout=1)
        if delivery is None:
            await asyncio.sleep(0.05)
            continue
        received.append(messages.RESULTS.validate_json(delivery.body))
        await delivery.ack()
    assert len(received) == count, f"got {len(received)} of {count} messages"
    return received


async def nothing_more(channel: AbstractChannel) -> bool:
    queue = await channel.get_queue(broker.QUEUE_RESULTS)
    return await queue.get(fail=False, timeout=1) is None


async def test_stub_plays_the_recorded_scan(
    topology: AbstractChannel, stub: StubScanner, recorded: RecordedScan
) -> None:
    await stub.start()
    await send(topology, recorded.chunks)

    hello, *played = await results(topology, 1 + len(recorded.results))

    assert isinstance(hello, Hello)
    assert (hello.vantage_point, hello.syn_capable, hello.icmp_capable) == ("test", True, True)
    assert [comparable(r) for r in played] == [comparable(r) for r in recorded.results]
    assert await nothing_more(topology)


async def test_capabilities_are_configurable(broker_url: str, topology: AbstractChannel) -> None:
    scanner = StubScanner(
        url=broker_url,
        world={},
        syn_capable=False,
        icmp_capable=False,
        allow_non_public_targets=True,
    )
    await scanner.start()
    await scanner.heartbeat()
    await scanner.stop()

    hello, heartbeat = await results(topology, 2)

    assert isinstance(hello, Hello) and isinstance(heartbeat, Heartbeat)
    for status in (hello, heartbeat):
        assert (status.syn_capable, status.icmp_capable, status.allow_non_public_targets) == (
            False,
            False,
            True,
        )


async def test_duplicate_mode_publishes_every_result_twice(
    topology: AbstractChannel, stub: StubScanner, recorded: RecordedScan
) -> None:
    stub.duplicate = True
    await stub.start()
    await send(topology, recorded.chunks)

    _hello, *played = await results(topology, 1 + 2 * len(recorded.results))

    expected = [comparable(r) for r in recorded.results for _ in range(2)]
    assert [comparable(r) for r in played] == expected
    # A duplicate is the same message, identifier included.
    assert played[0].message_id == played[1].message_id


async def test_reorder_mode_changes_the_order_and_nothing_else(
    topology: AbstractChannel,
    stub: StubScanner,
    recorded: RecordedScan,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Make the "shuffle" a reversal, so the test does not depend on chance.
    monkeypatch.setattr("tests.tools.stub_scanner.random.shuffle", lambda items: items.reverse())
    stub.reorder = True
    await stub.start()
    await send(topology, recorded.chunks[:1])

    _hello, *played = await results(topology, 4)

    in_order = [comparable(r) for r in recorded.results[:3]]
    assert [comparable(r) for r in played] == list(reversed(in_order))
    assert isinstance(played[0], ChunkDone)  # the completion arrives before its findings


async def test_delay_mode_waits_before_each_result(
    topology: AbstractChannel, stub: StubScanner, recorded: RecordedScan
) -> None:
    stub.delay = 0.3
    await stub.start()
    started = time.monotonic()
    await send(topology, recorded.chunks[:1])

    await results(topology, 4)

    assert time.monotonic() - started >= 3 * 0.3


async def test_reject_mode_answers_with_a_rejection_only(
    topology: AbstractChannel, stub: StubScanner, recorded: RecordedScan
) -> None:
    stub.reject_chunks = {1}
    await stub.start()
    await send(topology, recorded.chunks[1:2])

    _hello, rejection = await results(topology, 2)

    assert isinstance(rejection, ChunkRejected)
    assert (rejection.scan_id, rejection.chunk_seq) == (1, 1)
    assert await nothing_more(topology)


async def test_crash_mode_leaves_the_chunk_unacknowledged(
    broker_url: str, topology: AbstractChannel, stub: StubScanner, recorded: RecordedScan
) -> None:
    stub.crash_on_chunk = 1
    await stub.start()
    await send(topology, recorded.chunks[1:2])
    await results(topology, 1)  # the hello
    for _ in range(50):
        if stub.crashed:
            break
        await asyncio.sleep(0.1)

    # The crashed scanner saw the chunk and published nothing for it.
    assert stub.crashed
    assert [chunk.chunk_seq for chunk in stub.chunks_seen] == [1]
    assert await nothing_more(topology)

    # The broker delivers the chunk again to the next scanner, which completes it.
    replacement = StubScanner(url=broker_url, world=recorded.world)
    await replacement.start()
    try:
        _hello, findings, done = await results(topology, 3)
    finally:
        await replacement.stop()
    assert isinstance(findings, Findings) and isinstance(done, ChunkDone)
    assert [chunk.chunk_seq for chunk in replacement.chunks_seen] == [1]
