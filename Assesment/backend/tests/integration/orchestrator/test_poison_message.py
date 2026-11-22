"""Check 1B.7: poison message (NFR-REL-4).

An invalid message goes to the dead-letter queue, the scan is failed with the
reason, and nothing loops.
"""

import asyncio
from typing import Any

import pytest
from aio_pika.abc import AbstractChannel
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core import broker
from ndm.core.messages import ChunkDone
from ndm.db.models import Scan, ScanState
from ndm.orchestrator.deadletter import handle_dead_letter
from ndm.orchestrator.main import handle_dead, handle_result
from ndm.orchestrator.tick import Orchestrator
from tests.integration.core.test_broker import publish_raw
from tests.integration.orchestrator.conftest import ScanFactory, snapshot


def x_death(queue: str, reason: str) -> dict[str, Any]:
    """The header the broker adds to a dead-lettered message."""
    return {"x-death": [{"queue": queue, "reason": reason, "count": 1}]}


# --- What happens to the scan (no broker needed) ---


@pytest.mark.parametrize(
    ("body", "queue", "cause"),
    [
        # A result that failed validation and was rejected.
        (
            '{"type": "findings", "scan_id": %d, "hosts": "nonsense"}',
            "orchestrator.results",
            "rejected",
        ),
        # A result with a schema version this code does not know.
        (
            '{"type": "chunk_done", "scan_id": %d, "schema_version": 99}',
            "orchestrator.results",
            "rejected",
        ),
        # A chunk the scanner took five times and never acknowledged.
        (
            '{"type": "chunk", "scan_id": %d, "chunk_seq": 1}',
            "scanner.test.chunks",
            "delivery_limit",
        ),
    ],
    ids=["invalid-result", "unknown-schema", "chunk-never-acknowledged"],
)
async def test_dead_lettered_message_fails_its_scan_with_the_reason(
    db_session: AsyncSession, make_scan: ScanFactory, body: str, queue: str, cause: str
) -> None:
    scan = await make_scan()

    failed = await handle_dead_letter(db_session, (body % scan.id).encode(), x_death(queue, cause))
    await db_session.commit()

    assert failed is not None and failed.id == scan.id
    state, phase, partial, reason, _ = (await snapshot(db_session, scan))["scan"]
    assert (state, phase, partial) == (ScanState.FAILED, None, True)
    assert reason == f"dead_letter: {cause} in {queue}"


async def test_results_of_the_failed_scan_are_kept(
    db_session: AsyncSession, make_scan: ScanFactory
) -> None:
    scan = await make_scan()
    before = await snapshot(db_session, scan)

    await handle_dead_letter(db_session, b'{"scan_id": %d}' % scan.id, x_death("q", "rejected"))

    after = await snapshot(db_session, scan)
    assert after["observations"] == before["observations"]
    assert after["chunks"] == {}  # the chunk rows go when a scan ends


@pytest.mark.parametrize(
    "body",
    [
        b"not json at all",
        b"[]",
        b"{}",
        b'{"scan_id": "seven"}',
        b'{"scan_id": true}',
        b'{"scan_id": 424242}',
    ],
)
async def test_message_that_names_no_scan_is_dropped_without_an_error(
    db_session: AsyncSession, make_scan: ScanFactory, body: bytes
) -> None:
    scan = await make_scan()

    assert await handle_dead_letter(db_session, body, {}) is None

    assert (await snapshot(db_session, scan))["scan"][0] is ScanState.RUNNING


@pytest.mark.parametrize("state", [ScanState.COMPLETED, ScanState.CANCELLED, ScanState.FAILED])
async def test_scan_that_has_ended_is_not_changed(
    db_session: AsyncSession, make_scan: ScanFactory, state: ScanState
) -> None:
    scan = await make_scan(state=state, phase=None, failure_reason=None)
    before = await snapshot(db_session, scan)

    assert await handle_dead_letter(db_session, b'{"scan_id": %d}' % scan.id, {}) is None

    assert await snapshot(db_session, scan) == before


# --- The whole way through a real broker ---


async def test_invalid_message_ends_in_a_failed_scan_and_nothing_loops(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    orchestrator: Orchestrator,
    topology: AbstractChannel,
) -> None:
    scan = await make_scan()
    wake = asyncio.Event()
    poison = b'{"type": "findings", "scan_id": %d, "chunk_seq": 1, "hosts": "nonsense"}' % scan.id
    await publish_raw(topology, broker.EXCHANGE_RESULTS, broker.QUEUE_RESULTS, poison)
    results = await topology.get_queue(broker.QUEUE_RESULTS)
    dead = await topology.get_queue(broker.QUEUE_DEAD)

    # The orchestrator takes it from the results queue and rejects it...
    delivery = await results.get(timeout=5)
    assert delivery is not None
    await handle_result(delivery, orchestrator, wake)
    assert (await snapshot(db_session, scan))["scan"][0] is ScanState.RUNNING

    # ...the broker moves it to the dead-letter queue, where it is taken once.
    dead_delivery = await dead.get(timeout=5)
    assert dead_delivery is not None and dead_delivery.body == poison
    await handle_dead(dead_delivery, orchestrator, wake)

    state, _, partial, reason, _ = (await snapshot(db_session, scan))["scan"]
    assert (state, partial) == (ScanState.FAILED, True)
    assert reason == "dead_letter: rejected in orchestrator.results"
    # Nothing loops: both queues are empty, and the tick was asked to run.
    assert await results.get(fail=False, timeout=1) is None
    assert await dead.get(fail=False, timeout=1) is None
    assert wake.is_set()


async def test_valid_result_after_a_poison_message_is_still_processed(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    orchestrator: Orchestrator,
    topology: AbstractChannel,
) -> None:
    """A poison message for one scan does not block the queue for another."""
    poisoned, healthy = await make_scan(), await make_scan()
    wake = asyncio.Event()
    await publish_raw(
        topology, broker.EXCHANGE_RESULTS, broker.QUEUE_RESULTS, b'{"scan_id": %d}' % poisoned.id
    )
    done = ChunkDone(scan_id=healthy.id, chunk_seq=0, probes_sent=4, receive_drops=0, counts=[])
    await broker.publish(topology, broker.EXCHANGE_RESULTS, broker.QUEUE_RESULTS, done)
    results = await topology.get_queue(broker.QUEUE_RESULTS)

    for _ in range(2):
        delivery = await results.get(timeout=5)
        assert delivery is not None
        await handle_result(delivery, orchestrator, wake)

    stored = await snapshot(db_session, healthy)
    assert stored["chunks"][0][0].value == "done"
    assert (await db_session.get_one(Scan, healthy.id)).state is ScanState.RUNNING
