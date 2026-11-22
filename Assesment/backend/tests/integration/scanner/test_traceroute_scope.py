"""Check 3.6, scanner part: the scanner's own scope check for traceroute jobs.

A job whose target is outside the scope it carries, non-public without the
test switch, or on the scanner's own never-scan list is rejected with nothing
sent. A job that passes is traced and published TTL by TTL, then done.
"""

import asyncio
from collections.abc import Callable
from ipaddress import IPv4Address
from typing import Any

import pytest
from aio_pika.abc import AbstractChannel

from ndm.core import broker, messages
from ndm.core.messages import (
    Hello,
    TracerouteDone,
    TracerouteHop,
    TracerouteJob,
    TracerouteMethod,
    TracerouteRejected,
)
from ndm.core.settings import Settings
from ndm.scanner import main as scanner_main
from ndm.scanner.main import Worker
from ndm.scanner.traceroute import Traceroutes, check_job
from tests.integration.scanner.conftest import Published, next_message
from tests.unit.scanner.test_quoted import Network

TARGET = IPv4Address("8.8.8.8")
SCOPE = [(IPv4Address("8.8.8.0"), IPv4Address("8.8.8.255"))]


def job(**changes: Any) -> TracerouteJob:
    values: dict[str, Any] = {
        "run_id": 21,
        "target": TARGET,
        "method": TracerouteMethod.ICMP,
        "port": None,
        "max_hops": 8,
        "probes_per_hop": 2,
        "timeout_ms": 100,
        "pps": 1000,
        "attestation_id": 1,
        "scope": SCOPE,
    }
    return TracerouteJob(**(values | changes))


# --- The check itself ---


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({}, None),
        ({"target": IPv4Address("9.9.9.9")}, "out_of_scope"),
        ({"scope": []}, "no_scope"),
        ({"scope": [(IPv4Address("8.8.8.255"), IPv4Address("8.8.8.0"))]}, "invalid_scope"),
        (
            {
                "target": IPv4Address("10.0.0.1"),
                "scope": [(IPv4Address("10.0.0.0"), IPv4Address("10.0.0.255"))],
            },
            "non_public",
        ),
        ({"method": TracerouteMethod.TCP, "port": None}, "no_port"),
    ],
)
def test_check_of_a_job(changes: dict[str, Any], reason: str | None) -> None:
    assert check_job(job(**changes), never_scan=[], allow_non_public=False) == reason


def test_never_scan_list_of_the_scanner_applies() -> None:
    from ndm.core.scope import parse_never_scan

    assert check_job(job(), never_scan=parse_never_scan(["8.8.8.8"]), allow_non_public=False) == (
        "never_scan"
    )


# --- In the worker ---


def traced_worker(
    make_worker: Callable[..., Worker], network: Network, **environment: str
) -> Worker:
    """A worker whose sockets are the simulated network."""
    worker = make_worker(**environment)
    worker.echo = object()  # type: ignore[assignment]  # it can receive ICMP
    traces = Traceroutes(0x4242, network.icmp, network.tcp, network.udp)
    network.traces = traces
    worker.traceroutes = traces
    return worker


async def test_out_of_scope_job_is_rejected_with_nothing_sent(
    make_worker: Callable[..., Worker], published: Published
) -> None:
    network = Network(["10.0.0.1", "203.0.113.1"])
    worker = traced_worker(make_worker, network)

    await worker.handle_traceroute(job(target=IPv4Address("9.9.9.9")))

    assert network.sent == []
    assert published.messages == [
        TracerouteRejected(
            run_id=21, reason="out_of_scope", message_id=published.messages[0].message_id
        )
    ]


async def test_job_on_the_scanners_never_scan_list_is_rejected(
    make_worker: Callable[..., Worker], published: Published
) -> None:
    network = Network([])
    worker = traced_worker(make_worker, network, NDM_NEVER_SCAN="8.8.8.0/24")

    await worker.handle_traceroute(job())

    assert network.sent == []
    assert [(m.type, m.reason) for m in published.messages] == [  # type: ignore[attr-defined]
        ("traceroute_rejected", "never_scan")
    ]


async def test_without_raw_sockets_a_job_is_rejected(
    make_worker: Callable[..., Worker], published: Published
) -> None:
    worker = make_worker()  # no raw sockets on this machine, or in a test

    await worker.handle_traceroute(job())

    assert [(m.type, m.reason) for m in published.messages] == [  # type: ignore[attr-defined]
        ("traceroute_rejected", "no_raw_sockets")
    ]


async def test_job_in_scope_is_published_ttl_by_ttl_then_done(
    make_worker: Callable[..., Worker], published: Published
) -> None:
    network = Network(["10.0.0.1", None, "203.0.113.1"])
    worker = traced_worker(make_worker, network)

    await worker.handle_traceroute(job())

    hops = [m for m in published.messages if isinstance(m, TracerouteHop)]
    done = published.messages[-1]
    assert [hop.ttl for hop in hops] == [1, 2, 3, 4]
    assert [str(hop.replies[0].address) for hop in hops] == [
        "10.0.0.1",
        "None",
        "203.0.113.1",
        "8.8.8.8",
    ]
    assert isinstance(done, TracerouteDone)
    assert (done.run_id, done.reached, done.probes_sent) == (21, True, 8)
    # Each hop went out before the next TTL was probed (FR-TRC-6).
    assert published.times == sorted(published.times)
    assert {destination for _, destination, _, _ in network.sent} == {TARGET}
    assert worker.probes_sent == 8


async def test_a_udp_traceroute_gives_its_source_port_back(
    make_worker: Callable[..., Worker], published: Published
) -> None:
    network = Network(["10.0.0.1"])
    worker = traced_worker(make_worker, network)
    free = len(worker._free_ports)

    await worker.handle_traceroute(job(method=TracerouteMethod.UDP, port=33434))

    assert isinstance(published.messages[-1], TracerouteDone)
    assert len(worker._free_ports) == free
    assert {kind for kind, *_ in network.sent} == {"udp"}


# --- On the broker (needs RabbitMQ) ---


async def test_traceroute_job_placed_on_the_queue_by_hand_is_rejected_and_reported(
    monkeypatch: pytest.MonkeyPatch, broker_url: str, topology: AbstractChannel
) -> None:
    monkeypatch.setenv("NDM_BROKER_URL", broker_url)
    monkeypatch.setenv("NDM_VANTAGE_POINT", "test")
    monkeypatch.setattr(
        scanner_main, "HEARTBEAT_FILE", scanner_main.HEARTBEAT_FILE.with_suffix(".test")
    )
    scanner = asyncio.create_task(scanner_main.run(Settings()))
    try:
        forged = job(target=IPv4Address("9.9.9.9"))
        await broker.publish(
            topology, broker.EXCHANGE_SCANNER, broker.traceroute_queue("test"), forged
        )
        results = await topology.get_queue(broker.QUEUE_RESULTS)
        received = []
        for _ in range(2):
            delivery = await next_message(results)
            assert delivery is not None
            received.append(messages.RESULTS.validate_json(delivery.body))
            await delivery.ack()
    finally:
        scanner.cancel()
        await asyncio.gather(scanner, return_exceptions=True)

    hello, rejection = received
    assert isinstance(hello, Hello)
    assert isinstance(rejection, TracerouteRejected)
    assert (rejection.run_id, rejection.reason) == (21, "out_of_scope")
    queue = await topology.get_queue(broker.traceroute_queue("test"))
    assert await queue.get(fail=False, timeout=1) is None
