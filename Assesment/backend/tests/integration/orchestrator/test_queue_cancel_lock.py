"""Check 1B.9: queue, cancel, single instance. Also the lifecycle of a scan end to end."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from ndm.core.messages import ChunkPhase, ScanProgress, Technique
from ndm.db.models import Scan, ScanChunk, ScanState
from ndm.orchestrator.main import SingleInstanceLock
from ndm.orchestrator.tick import Orchestrator, tick
from tests.integration.conftest import DatabaseServer
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


async def states(db: AsyncSession) -> list[str]:
    await db.commit()
    return [state.value for state in await db.scalars(select(Scan.state).order_by(Scan.id))]


# --- A scan from the queue to completion ---


async def test_queued_scan_runs_to_completion(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan = await make_scan(state=ScanState.QUEUED)
    await scanner_reports(orchestrator)

    await run_to_end(orchestrator, outbox, WORLD)

    stored = await snapshot(db_session, scan)
    state, phase, partial, reason, counters = stored["scan"]
    assert (state, phase, partial, reason) == (ScanState.COMPLETED, None, False, None)
    # Discovery of all four targets, then the port scan of the three that answered.
    assert [(c.chunk_seq, c.phase, len(c.targets)) for c in outbox.chunks] == [
        (0, ChunkPhase.DISCOVERY, 4),
        (1, ChunkPhase.PORT_SCAN, 3),
    ]
    assert stored["ports"] == [("8.8.8.1", 22), ("8.8.8.1", 443), ("8.8.8.2", 443)]
    assert {a: row[0] for a, row in stored["observations"].items()} == {
        "8.8.8.1": True,
        "8.8.8.2": True,
        "8.8.8.3": True,
        "8.8.8.4": False,
    }
    assert {a: row[2:] for a, row in stored["observations"].items()} == {
        "8.8.8.1": (2, 0),
        "8.8.8.2": (0, 3),
        "8.8.8.3": (4, 0),
        "8.8.8.4": (0, 0),
    }
    # The chunk rows are gone; the totals stay on the scan.
    assert stored["chunks"] == {}
    assert counters == {"chunks_total": 2, "chunks_done": 2, "probes_sent": 16, "receive_drops": 0}
    # The technique was resolved from the scanner's capabilities and recorded.
    finished = await db_session.get_one(Scan, scan.id, populate_existing=True)
    assert (finished.technique_requested, finished.technique_used) == ("automatic", "syn")
    assert finished.started_at is not None and finished.finished_at is not None
    # Every target is known to the project, also the one that never answered.
    assert sorted(stored["project_hosts"]) == ["8.8.8.1", "8.8.8.2", "8.8.8.3", "8.8.8.4"]
    assert stored["project_hosts"]["8.8.8.4"] == (None, None, True)
    # The browser was told: the last progress event says "completed".
    progress = [event for event in outbox.events if isinstance(event, ScanProgress)]
    assert progress[0].state == "running" and progress[-1].state == "completed"
    assert (progress[-1].chunks_done, progress[-1].chunks_total) == (2, 2)


async def test_one_chunk_per_scan_is_in_flight(
    make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    await make_scan(state=ScanState.QUEUED)
    await scanner_reports(orchestrator)

    for _ in range(3):
        await tick(orchestrator)

    # The discovery chunk is out and unanswered: nothing else is sent.
    assert [chunk.chunk_seq for chunk in outbox.chunks] == [0]


async def test_chunk_carries_what_the_scanner_needs(
    make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan = await make_scan(state=ScanState.QUEUED)
    await scanner_reports(orchestrator)
    await tick(orchestrator)

    (chunk,) = outbox.chunks

    assert (chunk.scan_id, chunk.technique) == (scan.id, Technique.SYN)
    assert (chunk.pps, chunk.timeout_ms, chunk.retries, chunk.seed) == (1000, 1500, 1, 91823)
    assert chunk.discovery is not None
    assert (chunk.discovery.icmp_echo, chunk.discovery.tcp_ports) == (True, [80, 443])
    # The scope the scanner checks before it sends anything, with its attestation.
    assert [(str(low), str(high)) for low, high in chunk.scope] == [("8.8.8.0", "8.8.8.255")]
    assert chunk.attestation_id == scan.attestation_id


async def test_all_targets_are_port_scanned_when_asked(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    parameters = {
        "discovery": {"icmp_echo": True, "tcp_ports": []},
        "scan_all_targets": True,
        "pps": 1000,
        "timeout_ms": 1500,
        "retries": 0,
    }
    scan = await make_scan(state=ScanState.QUEUED, parameters=parameters)
    await scanner_reports(orchestrator)

    await run_to_end(orchestrator, outbox, WORLD)

    assert [len(chunk.targets) for chunk in outbox.chunks] == [4, 4]
    # 8.8.8.2 does not answer ping, and is found by its open port all the same.
    stored = await snapshot(db_session, scan)
    assert stored["ports"] == [("8.8.8.1", 22), ("8.8.8.1", 443), ("8.8.8.2", 443)]
    assert stored["observations"]["8.8.8.4"][2:] == (0, 4)


async def test_discovery_can_be_skipped(
    make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    parameters = {"discovery": None, "pps": 1000, "timeout_ms": 1500, "retries": 0}
    await make_scan(state=ScanState.QUEUED, parameters=parameters)
    await scanner_reports(orchestrator)

    await run_to_end(orchestrator, outbox, WORLD)

    assert [(c.chunk_seq, c.phase, len(c.targets)) for c in outbox.chunks] == [
        (0, ChunkPhase.PORT_SCAN, 4)
    ]


async def test_scan_with_no_reachable_host_completes_after_discovery(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    await make_scan(state=ScanState.QUEUED)
    await scanner_reports(orchestrator)

    await run_to_end(orchestrator, outbox, world={})

    assert await states(db_session) == ["completed"]
    assert len(outbox.chunks) == 1


# --- Technique ---


async def test_without_the_syn_capability_the_scan_uses_connect(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan = await make_scan(state=ScanState.QUEUED)
    await scanner_reports(orchestrator, syn=False)

    await run_to_end(orchestrator, outbox, WORLD)

    assert {chunk.technique for chunk in outbox.chunks} == {Technique.CONNECT}
    finished = await db_session.get_one(Scan, scan.id, populate_existing=True)
    assert (finished.state, finished.technique_used) == (ScanState.COMPLETED, "connect")


async def test_forced_syn_without_the_capability_fails_instead_of_falling_back(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan = await make_scan(state=ScanState.QUEUED, technique_requested="syn")
    await scanner_reports(orchestrator, syn=False)

    await tick(orchestrator)

    failed = await db_session.get_one(Scan, scan.id, populate_existing=True)
    assert (failed.state, failed.failure_reason) == (ScanState.FAILED, "syn_unavailable")
    assert outbox.chunks == []


# --- ICMP discovery unavailable (TASK-079, check 1B.11) ---

ICMP_AND_TCP = {"icmp_echo": True, "tcp_ports": [80, 443]}
TCP_ONLY = {"icmp_echo": False, "tcp_ports": [80, 443]}


@pytest.mark.parametrize(
    ("icmp_capable", "discovery", "unavailable"),
    [
        (False, ICMP_AND_TCP, True),
        (True, ICMP_AND_TCP, False),
        # Not asked for: nothing is missing.
        (False, TCP_ONLY, False),
        (False, None, False),
    ],
)
async def test_a_scan_records_that_icmp_discovery_was_unavailable(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    orchestrator: Orchestrator,
    icmp_capable: bool,
    discovery: dict[str, object] | None,
    unavailable: bool,
) -> None:
    parameters = {
        "discovery": discovery,
        "scan_all_targets": discovery is None,
        "pps": 1000,
        "timeout_ms": 1500,
        "retries": 1,
        "hosts_in_parallel": 64,
    }
    scan = await make_scan(state=ScanState.QUEUED, parameters=parameters)
    await scanner_reports(orchestrator, syn=icmp_capable, icmp=icmp_capable)

    await tick(orchestrator)

    started = await db_session.get_one(Scan, scan.id, populate_existing=True)
    assert started.state is ScanState.RUNNING
    assert started.icmp_discovery_unavailable is unavailable


# --- Queue (FR-SCN-6) ---


async def test_scan_above_the_concurrent_ceiling_stays_queued_until_a_slot_is_free(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    for _ in range(5):
        await make_scan(state=ScanState.QUEUED)
    await scanner_reports(orchestrator)
    assert orchestrator.settings.max_concurrent_scans == 3

    await tick(orchestrator)
    await tick(orchestrator)

    # The three oldest run, each with one chunk out; the others wait.
    assert await states(db_session) == ["running", "running", "running", "queued", "queued"]
    assert sorted(chunk.scan_id for chunk in outbox.chunks) == [1, 2, 3]

    # The second scan finishes: one slot is free, and the oldest queued scan takes it.
    second = next(chunk for chunk in outbox.chunks if chunk.scan_id == 2)
    await answer(orchestrator, second, world={})
    await tick(orchestrator)
    await tick(orchestrator)

    assert await states(db_session) == ["running", "completed", "running", "running", "queued"]


async def test_nothing_starts_while_no_scanner_is_alive(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    await make_scan(state=ScanState.QUEUED)

    await tick(orchestrator)
    assert await states(db_session) == ["queued"]

    await scanner_reports(orchestrator)
    await tick(orchestrator)
    assert await states(db_session) == ["running"]


# --- Cancel (FR-SCN-5) ---


async def test_cancelled_scan_keeps_its_results_and_is_marked_partial(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan = await make_scan(state=ScanState.QUEUED)
    await scanner_reports(orchestrator)
    await tick(orchestrator)
    await answer(orchestrator, outbox.chunks[0], WORLD)  # discovery is done
    await tick(orchestrator)  # the port-scan chunk is out

    running = await db_session.get_one(Scan, scan.id, populate_existing=True)
    running.cancel_requested_at = datetime.now(UTC)
    await db_session.commit()
    await tick(orchestrator)

    stored = await snapshot(db_session, scan)
    state, phase, partial, _, counters = stored["scan"]
    assert (state, phase, partial) == (ScanState.CANCELLED, None, True)
    # What discovery found is kept.
    assert [a for a, row in stored["observations"].items() if row[0]] == [
        "8.8.8.1",
        "8.8.8.2",
        "8.8.8.3",
    ]
    assert (counters["chunks_done"], counters["chunks_total"]) == (1, 2)
    # The scanner is told to stop, and nothing more is sent for this scan.
    assert [cancel.scan_id for cancel in outbox.cancels] == [scan.id]
    sent = len(outbox.chunks)
    await answer(orchestrator, outbox.chunks[-1], WORLD)  # a late result changes nothing
    await tick(orchestrator)
    assert len(outbox.chunks) == sent
    assert await snapshot(db_session, scan) == stored


async def test_queued_scan_can_be_cancelled(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    await make_scan(state=ScanState.QUEUED, cancel_requested_at=func.now())
    await scanner_reports(orchestrator)

    await tick(orchestrator)

    assert await states(db_session) == ["cancelled"]
    assert outbox.chunks == []


async def test_deleted_scan_stops(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan = await make_scan(state=ScanState.QUEUED)
    await scanner_reports(orchestrator)
    await tick(orchestrator)

    running = await db_session.get_one(Scan, scan.id, populate_existing=True)
    running.deleted_at = datetime.now(UTC)
    await db_session.commit()
    await tick(orchestrator)
    await tick(orchestrator)

    assert await states(db_session) == ["cancelled"]
    assert [cancel.scan_id for cancel in outbox.cancels] == [scan.id]
    assert len(outbox.chunks) == 1
    assert await db_session.scalar(select(func.count()).select_from(ScanChunk)) == 0


# --- Interrupted ---


async def test_missing_heartbeat_interrupts_and_a_result_resumes(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    await make_scan(state=ScanState.QUEUED)
    await scanner_reports(orchestrator)
    await tick(orchestrator)
    assert await states(db_session) == ["running"]

    # 29 seconds without a heartbeat is not yet an interruption; 31 seconds is.
    now = datetime.now(UTC)
    await tick(orchestrator, now=now + timedelta(seconds=29))
    assert await states(db_session) == ["running"]
    await tick(orchestrator, now=now + timedelta(seconds=31))
    assert await states(db_session) == ["interrupted"]
    # Nothing is sent while the scanner is away.
    assert len(outbox.chunks) == 1

    # The scanner comes back and delivers the chunk it had: the scan goes on by itself.
    await scanner_reports(orchestrator, hello=False)
    await answer(orchestrator, outbox.chunks[0], WORLD)
    assert await states(db_session) == ["running"]
    await run_to_end(orchestrator, outbox, WORLD)
    assert await states(db_session) == ["completed"]


async def test_interrupted_scans_count_towards_the_ceiling(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    for _ in range(3):
        await make_scan(state=ScanState.INTERRUPTED)
    await make_scan(state=ScanState.QUEUED)
    await scanner_reports(orchestrator)

    await tick(orchestrator)

    assert (await states(db_session))[-1] == "queued"


# --- Single instance ---


async def test_second_orchestrator_stays_idle(
    database_server: DatabaseServer, app_engine: AsyncEngine
) -> None:
    other_engine = create_async_engine(database_server.app_url())
    first, second = SingleInstanceLock(app_engine), SingleInstanceLock(other_engine)
    try:
        assert await first.try_acquire() is True
        assert await second.try_acquire() is False
        assert await second.try_acquire() is False

        # When the first instance is gone, the lock is free and the second takes over.
        await first.release()
        assert await second.try_acquire() is True
    finally:
        await first.release()
        await second.release()
        await other_engine.dispose()
