"""Check 1B.6: dispatch recovery.

A crash between publish and commit leads to a re-publish; a chunk past its
lease is re-published; after five attempts the scan is failed.
"""

from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.messages import ChunkPhase
from ndm.db.models import ChunkState, Scan, ScanChunk, ScanState, VantagePoint
from ndm.orchestrator.dispatch import MAX_ATTEMPTS, lease
from ndm.orchestrator.planning import ChunkSpec
from ndm.orchestrator.tick import Orchestrator, tick
from tests.integration.orchestrator.conftest import (
    Outbox,
    ScanFactory,
    answer,
    run_to_end,
    scanner_reports,
    snapshot,
)
from tests.tools.stub_scanner import load_recorded_scan

WORLD = load_recorded_scan().world


def facts(stored: dict[str, Any]) -> dict[str, Any]:
    """The observations without the times of the replies, which differ between two runs."""
    return {
        address: (reachable, sorted(evidence), closed, filtered)
        for address, (reachable, evidence, closed, filtered) in stored["observations"].items()
    }


async def chunk_row(db: AsyncSession, scan: Scan, seq: int = 0) -> ScanChunk:
    await db.commit()
    return await db.get_one(ScanChunk, (scan.id, seq), populate_existing=True)


async def beat_and_tick(orchestrator: Orchestrator, now: datetime) -> None:
    """A tick at ``now``, with a scanner that is alive at that time."""
    await scanner_reports(orchestrator, hello=False)
    # The heartbeat is stamped with the real time; the tick sees it as fresh
    # only if its own clock is not ahead by more than the timeout. Stamp it.
    async with orchestrator.sessions() as db:
        point = await db.scalar(select(VantagePoint))
        assert point is not None
        point.last_heartbeat_at = now
        await db.commit()
    await tick(orchestrator, now=now)


# --- Crash between publish and commit ---


async def test_crash_between_publish_and_commit_leads_to_a_republish(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan = await make_scan(state=ScanState.QUEUED)
    await scanner_reports(orchestrator)
    outbox.crash_after_publish = True

    await tick(orchestrator)

    # The chunk went out, but the orchestrator did not get to record that.
    assert [chunk.chunk_seq for chunk in outbox.chunks] == [0]
    row = await chunk_row(db_session, scan)
    assert (row.state, row.attempts, row.dispatched_at) == (ChunkState.PENDING, 0, None)

    await tick(orchestrator)

    # It is published again, the same chunk, and now recorded as dispatched.
    assert [chunk.chunk_seq for chunk in outbox.chunks] == [0, 0]
    assert outbox.chunks[0].targets == outbox.chunks[1].targets
    row = await chunk_row(db_session, scan)
    assert (row.state, row.attempts) == (ChunkState.DISPATCHED, 1)


async def test_scan_completes_after_a_crash_with_the_chunk_answered_twice(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    """At-least-once delivery with no lost and no duplicated work."""
    reference = await make_scan(state=ScanState.QUEUED)
    await scanner_reports(orchestrator)
    await run_to_end(orchestrator, outbox, WORLD)
    expected = await snapshot(db_session, reference)

    scan = await make_scan(state=ScanState.QUEUED)
    outbox.chunks.clear()
    outbox.crash_after_publish = True
    await tick(orchestrator)
    await tick(orchestrator)
    # The scanner got the chunk twice and answers twice.
    for chunk in list(outbox.chunks):
        await answer(orchestrator, chunk, WORLD)
    await run_to_end(orchestrator, outbox, WORLD)

    stored = await snapshot(db_session, scan)
    for part in ("scan", "ports", "chunks"):
        assert stored[part] == expected[part]
    assert facts(stored) == facts(expected)


# --- Lease ---


async def test_chunk_past_its_lease_is_republished(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan = await make_scan(state=ScanState.QUEUED)
    await scanner_reports(orchestrator)
    start = datetime.now(UTC)
    await beat_and_tick(orchestrator, start)
    assert len(outbox.chunks) == 1

    # Within the lease of 5 minutes nothing happens.
    await beat_and_tick(orchestrator, start + timedelta(minutes=4, seconds=59))
    assert len(outbox.chunks) == 1

    # Past the lease the same chunk goes out again.
    await beat_and_tick(orchestrator, start + timedelta(minutes=5, seconds=1))
    assert [chunk.chunk_seq for chunk in outbox.chunks] == [0, 0]
    row = await chunk_row(db_session, scan)
    assert (row.state, row.attempts) == (ChunkState.DISPATCHED, 2)

    # The lease starts again with the second dispatch.
    await beat_and_tick(orchestrator, start + timedelta(minutes=9))
    assert len(outbox.chunks) == 2


async def test_after_five_attempts_the_scan_is_failed(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan = await make_scan(state=ScanState.QUEUED)
    await scanner_reports(orchestrator)
    start = datetime.now(UTC)
    await beat_and_tick(orchestrator, start)
    await answer(orchestrator, outbox.chunks[0], WORLD)  # discovery succeeds
    await beat_and_tick(orchestrator, start)  # the port-scan chunk goes out

    # Its results never arrive. It is sent five times in all.
    moment = start
    for _ in range(MAX_ATTEMPTS - 1):
        moment += timedelta(minutes=5, seconds=1)
        await beat_and_tick(orchestrator, moment)
    assert [chunk.chunk_seq for chunk in outbox.chunks] == [0] + [1] * MAX_ATTEMPTS
    assert (await snapshot(db_session, scan))["scan"][0] is ScanState.RUNNING

    # When the fifth lease is over, the scan fails. It does not loop.
    moment += timedelta(minutes=5, seconds=1)
    await beat_and_tick(orchestrator, moment)
    await beat_and_tick(orchestrator, moment + timedelta(minutes=6))

    stored = await snapshot(db_session, scan)
    state, phase, partial, reason, _ = stored["scan"]
    assert (state, phase, partial, reason) == (ScanState.FAILED, None, True, "chunk_lost")
    assert len(outbox.chunks) == 1 + MAX_ATTEMPTS
    # What discovery found is kept.
    assert [a for a, row in stored["observations"].items() if row[0]] == [
        "8.8.8.1",
        "8.8.8.2",
        "8.8.8.3",
    ]
    assert stored["chunks"] == {}


def test_lease_is_the_larger_of_five_minutes_and_three_times_the_expected_duration() -> None:
    def spec(hosts: int, ports: int) -> ChunkSpec:
        return ChunkSpec(
            chunk_seq=1,
            phase=ChunkPhase.PORT_SCAN,
            targets=[IPv4Address("8.8.8.1")] * hosts,
            ports=list(range(1, ports + 1)),
        )

    # 10,000 probes at 1,000 per second take 10 seconds: the minimum applies.
    assert lease(spec(64, 156), pps=1000) == timedelta(minutes=5)
    # At 10 per second they take about 1,000 seconds: three times that.
    assert lease(spec(100, 100), pps=10) == timedelta(seconds=3000)
    discovery = ChunkSpec(
        chunk_seq=0, phase=ChunkPhase.DISCOVERY, targets=[IPv4Address("8.8.8.1")] * 256
    )
    assert lease(discovery, pps=1000) == timedelta(minutes=5)


# --- A failure in one scan does not hold up another ---


async def test_other_scans_go_on_when_one_cannot_be_dispatched(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    await make_scan(state=ScanState.QUEUED)
    await make_scan(state=ScanState.QUEUED)
    await scanner_reports(orchestrator)
    outbox.crash_after_publish = True  # hits the first scan only

    await tick(orchestrator)

    assert [chunk.scan_id for chunk in outbox.chunks] == [1, 2]
    rows = (await db_session.scalars(select(ScanChunk).order_by(ScanChunk.scan_id))).all()
    assert [row.state for row in rows] == [ChunkState.PENDING, ChunkState.DISPATCHED]
