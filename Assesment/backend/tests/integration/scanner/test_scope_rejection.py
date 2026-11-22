"""Check 1B.8, scanner part: the scanner's own scope check.

A chunk with an out-of-scope target is rejected with nothing sent, and the
rejection reaches the orchestrator. A missing scope or one that cannot be read
counts as failure.
"""

import asyncio
from collections.abc import Callable
from ipaddress import IPv4Address
from typing import Any

import pytest
from aio_pika.abc import AbstractChannel

from ndm.core import broker, messages
from ndm.core.messages import ChunkRejected, Hello
from ndm.core.scope import parse_never_scan
from ndm.core.settings import Settings
from ndm.scanner import main as scanner_main
from ndm.scanner.main import Worker
from ndm.scanner.scopecheck import check_chunk
from tests.integration.scanner.conftest import LOCAL, Listeners, Published, chunk, next_message

PUBLIC_SCOPE = [(IPv4Address("8.8.8.0"), IPv4Address("8.8.8.255"))]


def public_chunk(**changes: Any) -> Any:
    return chunk(
        **{"targets": [IPv4Address("8.8.8.1")], "scope": PUBLIC_SCOPE, "ports": [80], **changes}
    )


# --- The check itself ---


def test_target_inside_the_scope_passes() -> None:
    assert check_chunk(public_chunk(), never_scan=[], allow_non_public=False) is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"targets": [IPv4Address("8.8.4.4")]}, "out_of_scope"),
        ({"targets": [IPv4Address("8.8.9.0")]}, "out_of_scope"),
        # One target outside is enough: the whole chunk is rejected.
        ({"targets": [IPv4Address("8.8.8.1"), IPv4Address("9.9.9.9")]}, "out_of_scope"),
        # Failing closed.
        ({"scope": []}, "no_scope"),
        ({"targets": []}, "no_targets"),
        ({"scope": [(IPv4Address("8.8.8.255"), IPv4Address("8.8.8.0"))]}, "invalid_scope"),
        # The scanner's own table of non-public space, whatever the scope says.
        (
            {
                "targets": [IPv4Address("10.0.0.5")],
                "scope": [(IPv4Address("10.0.0.0"), IPv4Address("10.0.0.255"))],
            },
            "non_public",
        ),
        (
            {
                "targets": [IPv4Address("192.168.1.1")],
                "scope": [(IPv4Address("0.0.0.0"), IPv4Address("255.255.255.255"))],
            },
            "non_public",
        ),
    ],
)
def test_chunk_that_must_not_be_probed_is_rejected(changes: dict[str, Any], reason: str) -> None:
    assert check_chunk(public_chunk(**changes), never_scan=[], allow_non_public=False) == reason


def test_scanners_own_never_scan_list_applies() -> None:
    never_scan = parse_never_scan(["8.8.8.1"])

    assert (
        check_chunk(public_chunk(), never_scan=never_scan, allow_non_public=False) == "never_scan"
    )
    other = public_chunk(targets=[IPv4Address("8.8.8.2")])
    assert check_chunk(other, never_scan=never_scan, allow_non_public=False) is None


def test_test_switch_allows_non_public_targets_inside_the_scope_only() -> None:
    lab = chunk()

    assert check_chunk(lab, never_scan=[], allow_non_public=True) is None
    assert check_chunk(lab, never_scan=[], allow_non_public=False) == "non_public"
    outside = chunk(targets=[IPv4Address("127.0.1.1")])
    assert check_chunk(outside, never_scan=[], allow_non_public=True) == "out_of_scope"


# --- The worker: rejected with nothing sent ---


async def test_out_of_scope_chunk_is_rejected_with_nothing_sent(
    worker: Worker, published: Published, listeners: Listeners
) -> None:
    # The second target is a listening loopback address outside the chunk's scope.
    bad = chunk(targets=[LOCAL, IPv4Address("127.0.9.9")], ports=listeners.ports)

    await worker.handle_chunk(bad)
    await asyncio.sleep(0.05)

    (rejection,) = published.messages
    assert isinstance(rejection, ChunkRejected)
    assert (rejection.scan_id, rejection.chunk_seq, rejection.reason) == (7, 1, "out_of_scope")
    # Not one connection was opened, not even to the target that was in scope.
    assert listeners.connections == []
    assert worker.probes_sent == 0


async def test_without_the_test_switch_the_scanner_refuses_non_public_targets(
    make_worker: Callable[..., Worker], published: Published, listeners: Listeners
) -> None:
    worker = make_worker()  # the switch is off in the scanner's environment

    # The chunk says the loopback range is in scope. The scanner's own table says no.
    await worker.handle_chunk(chunk(ports=listeners.ports))
    await asyncio.sleep(0.05)

    assert [m.reason for m in published.messages if isinstance(m, ChunkRejected)] == ["non_public"]
    assert listeners.connections == []


async def test_scanners_own_never_scan_list_rejects_the_chunk(
    make_worker: Callable[..., Worker], published: Published, listeners: Listeners
) -> None:
    worker = make_worker(NDM_ALLOW_NON_PUBLIC_TARGETS="true", NDM_NEVER_SCAN="127.0.0.1")

    await worker.handle_chunk(chunk(ports=listeners.ports))
    await asyncio.sleep(0.05)

    assert [m.reason for m in published.messages if isinstance(m, ChunkRejected)] == ["never_scan"]
    assert listeners.connections == []


async def test_chunk_without_a_scope_is_rejected(
    worker: Worker, published: Published, listeners: Listeners
) -> None:
    await worker.handle_chunk(chunk(ports=listeners.ports, scope=[]))

    assert [m.reason for m in published.messages if isinstance(m, ChunkRejected)] == ["no_scope"]
    assert listeners.connections == []


# --- Through a real broker: the rejection reaches the orchestrator ---


async def test_chunk_placed_on_the_queue_by_hand_is_rejected_and_the_orchestrator_hears_of_it(
    monkeypatch: pytest.MonkeyPatch,
    broker_url: str,
    topology: AbstractChannel,
    listeners: Listeners,
) -> None:
    monkeypatch.setenv("NDM_BROKER_URL", broker_url)
    monkeypatch.setenv("NDM_VANTAGE_POINT", "test")
    monkeypatch.setenv("NDM_ALLOW_NON_PUBLIC_TARGETS", "true")
    monkeypatch.setattr(
        scanner_main, "HEARTBEAT_FILE", scanner_main.HEARTBEAT_FILE.with_suffix(".test")
    )
    scanner = asyncio.create_task(scanner_main.run(Settings()))
    try:
        # Someone with access to the broker puts a chunk there, with a target outside its scope.
        forged = chunk(targets=[LOCAL, IPv4Address("127.0.9.9")], ports=listeners.ports)
        await broker.publish(topology, broker.EXCHANGE_SCANNER, broker.chunk_queue("test"), forged)

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
    assert isinstance(hello, Hello) and hello.syn_capable is False
    assert isinstance(rejection, ChunkRejected)
    assert (rejection.scan_id, rejection.reason) == (7, "out_of_scope")
    assert listeners.connections == []
    # The chunk was acknowledged: it is not delivered again.
    chunks = await topology.get_queue(broker.chunk_queue("test"))
    assert await chunks.get(fail=False, timeout=1) is None
