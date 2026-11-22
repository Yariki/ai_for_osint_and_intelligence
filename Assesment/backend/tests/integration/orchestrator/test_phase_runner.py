"""The phases after probing (architecture 6.8; mechanism of check 2.5).

The runner takes its phases from a registry. These tests register fake ones.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ndm.db.models import Scan, ScanChunk, ScanPhase, ScanState
from ndm.orchestrator import lifecycle, phases
from ndm.orchestrator.tick import Orchestrator, tick
from tests.integration.orchestrator.conftest import (
    Outbox,
    ScanFactory,
    run_to_end,
    scanner_reports,
    schedule_for,
    snapshot,
)
from tests.tools.stub_scanner import load_recorded_scan

WORLD = load_recorded_scan().world
Sessions = async_sessionmaker[AsyncSession]


class FakePhase:
    """A phase that records its calls, and fails while ``broken`` is set."""

    def __init__(self) -> None:
        self.calls: list[int] = []
        self.broken = False

    async def __call__(self, sessions: Sessions, scan_id: int) -> None:
        self.calls.append(scan_id)
        if self.broken:
            raise RuntimeError("the phase broke")


@pytest.fixture(autouse=True)
def no_phases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(phases, "PHASES", {})


@pytest.fixture
def enrichment() -> Iterator[FakePhase]:
    phase = FakePhase()
    phases.register(ScanPhase.ENRICHMENT, phase)
    yield phase


async def probe_to_the_end(orchestrator: Orchestrator, outbox: Outbox, scan: Scan) -> None:
    """Run the probing of a queued scan: tick and answer until no chunk is left."""
    await scanner_reports(orchestrator)
    await run_to_end(orchestrator, outbox, WORLD)


async def run_phases(orchestrator: Orchestrator) -> bool:
    return await phases.run_pending(orchestrator.sessions, orchestrator.publish_events)


async def stored(db: AsyncSession, scan: Scan) -> Scan:
    return await db.get_one(Scan, scan.id, populate_existing=True)


async def observed(db: AsyncSession, scan: Scan) -> dict[str, Any]:
    """What probing stored for the scan: observations, open ports and hosts."""
    everything = await snapshot(db, scan)
    return {key: everything[key] for key in ("observations", "ports", "hosts", "project_hosts")}


async def chunk_rows(db: AsyncSession, scan: Scan) -> int:
    return (
        await db.scalar(
            select(func.count()).select_from(ScanChunk).where(ScanChunk.scan_id == scan.id)
        )
        or 0
    )


def progress(outbox: Outbox) -> list[tuple[str, str | None]]:
    return [(event.state, event.phase) for event in outbox.events if event.type == "scan_progress"]


# --- No phase registered: as in Phase 1B ---


async def test_with_no_phase_registered_a_scan_completes_after_probing(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan = await make_scan(state=ScanState.QUEUED)

    await probe_to_the_end(orchestrator, outbox, scan)

    scan = await stored(db_session, scan)
    assert (scan.state, scan.phase, scan.partial) == (ScanState.COMPLETED, None, False)
    assert await run_phases(orchestrator) is False


# --- The phases run in order, and the scan completes after the last ---


async def test_scan_goes_through_the_phase_and_then_completes(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    orchestrator: Orchestrator,
    outbox: Outbox,
    enrichment: FakePhase,
) -> None:
    scan = await make_scan(state=ScanState.QUEUED)
    await probe_to_the_end(orchestrator, outbox, scan)

    # Probing is over: the scan waits in the phase, running, with its totals kept.
    scan = await stored(db_session, scan)
    assert (scan.state, scan.phase) == (ScanState.RUNNING, ScanPhase.ENRICHMENT)
    assert await chunk_rows(db_session, scan) == 0
    totals = dict(scan.counters)
    assert totals["chunks_total"] == totals["chunks_done"] > 0
    assert enrichment.calls == []
    before = await observed(db_session, scan)

    assert await run_phases(orchestrator) is True

    scan = await stored(db_session, scan)
    assert (scan.state, scan.phase, scan.partial) == (ScanState.COMPLETED, None, False)
    assert (scan.failed_phase, scan.failure_reason) == (None, None)
    assert scan.finished_at is not None
    assert enrichment.calls == [scan.id]
    # Ending the scan a second time did not reset what probing had counted.
    assert dict(scan.counters) == totals
    assert await observed(db_session, scan) == before
    assert progress(outbox)[-2:] == [("running", "enrichment"), ("completed", None)]


async def test_registered_phases_run_in_their_fixed_order(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    order: list[str] = []

    def phase(name: str) -> Any:
        async def run(sessions: Sessions, scan_id: int) -> None:
            order.append(name)

        return run

    # Registered out of order on purpose.
    phases.register(ScanPhase.DIFF, phase("diff"))
    phases.register(ScanPhase.ENRICHMENT, phase("enrichment"))
    scan = await make_scan(state=ScanState.QUEUED)
    await schedule_for(db_session, scan)  # the diff phase is for scheduled scans
    await probe_to_the_end(orchestrator, outbox, scan)

    while await run_phases(orchestrator):
        pass

    assert order == ["enrichment", "diff"]
    assert (await stored(db_session, scan)).state is ScanState.COMPLETED
    assert progress(outbox)[-3:] == [
        ("running", "enrichment"),
        ("running", "diff"),
        ("completed", None),
    ]


async def test_a_manual_scan_skips_the_diff_phase(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    order: list[str] = []

    def phase(name: str) -> Any:
        async def run(sessions: Sessions, scan_id: int) -> None:
            order.append(name)

        return run

    for member in (ScanPhase.ENRICHMENT, ScanPhase.CLASSIFICATION, ScanPhase.DIFF):
        phases.register(member, phase(member.value))
    scan = await make_scan(state=ScanState.QUEUED)
    await probe_to_the_end(orchestrator, outbox, scan)

    while await run_phases(orchestrator):
        pass

    assert order == ["enrichment", "classification"]
    assert (await stored(db_session, scan)).state is ScanState.COMPLETED
    assert progress(outbox)[-3:] == [
        ("running", "enrichment"),
        ("running", "classification"),
        ("completed", None),
    ]


async def test_the_tick_leaves_a_scan_in_a_phase_alone(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    orchestrator: Orchestrator,
    outbox: Outbox,
    enrichment: FakePhase,
) -> None:
    scan = await make_scan(state=ScanState.QUEUED)
    await probe_to_the_end(orchestrator, outbox, scan)
    published = len(outbox.chunks)

    for _ in range(3):
        await tick(orchestrator)

    scan = await stored(db_session, scan)
    assert (scan.state, scan.phase) == (ScanState.RUNNING, ScanPhase.ENRICHMENT)
    assert len(outbox.chunks) == published


# --- A phase that fails (NFR-REL-3) ---


async def test_failing_phase_fails_the_scan_and_leaves_the_observations_intact(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    orchestrator: Orchestrator,
    outbox: Outbox,
    enrichment: FakePhase,
) -> None:
    scan = await make_scan(state=ScanState.QUEUED)
    await probe_to_the_end(orchestrator, outbox, scan)
    before = await observed(db_session, scan)
    enrichment.broken = True

    await run_phases(orchestrator)

    scan = await stored(db_session, scan)
    assert (scan.state, scan.phase) == (ScanState.FAILED, None)
    assert (scan.failed_phase, scan.failure_reason) == (ScanPhase.ENRICHMENT, "enrichment_failed")
    assert await observed(db_session, scan) == before
    assert before["ports"]  # there was something to keep
    assert progress(outbox)[-1] == ("failed", None)
    # It is not tried again by itself.
    assert await run_phases(orchestrator) is False
    assert enrichment.calls == [scan.id]


async def test_retrying_the_failed_phase_completes_the_scan(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    orchestrator: Orchestrator,
    outbox: Outbox,
    enrichment: FakePhase,
) -> None:
    scan = await make_scan(state=ScanState.QUEUED)
    await probe_to_the_end(orchestrator, outbox, scan)
    before = await observed(db_session, scan)
    probed = len(outbox.chunks)
    enrichment.broken = True
    await run_phases(orchestrator)
    enrichment.broken = False

    # The analyst asks for the retry: the API sets the request on the scan.
    scan = await stored(db_session, scan)
    scan.retry_requested_at = scan.finished_at
    await db_session.commit()
    while await run_phases(orchestrator):
        pass

    scan = await stored(db_session, scan)
    assert (scan.state, scan.partial) == (ScanState.COMPLETED, False)
    assert (scan.failed_phase, scan.failure_reason, scan.retry_requested_at) == (None, None, None)
    # That phase alone ran again: nothing was probed a second time.
    assert enrichment.calls == [scan.id, scan.id]
    assert len(outbox.chunks) == probed
    assert await observed(db_session, scan) == before
    assert progress(outbox)[-2:] == [("running", "enrichment"), ("completed", None)]


async def test_retry_starts_at_the_failed_phase_and_goes_on_from_there(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    ran: list[str] = []
    broken = {"classification"}

    def phase(name: str) -> Any:
        async def run(sessions: Sessions, scan_id: int) -> None:
            ran.append(name)
            if name in broken:
                raise RuntimeError(name)

        return run

    for member in (ScanPhase.ENRICHMENT, ScanPhase.CLASSIFICATION, ScanPhase.DIFF):
        phases.register(member, phase(member.value))
    scan = await make_scan(state=ScanState.QUEUED)
    await schedule_for(db_session, scan)
    await probe_to_the_end(orchestrator, outbox, scan)
    while await run_phases(orchestrator):
        pass
    assert ran == ["enrichment", "classification"]
    assert (await stored(db_session, scan)).failed_phase is ScanPhase.CLASSIFICATION

    broken.clear()
    scan = await stored(db_session, scan)
    scan.retry_requested_at = scan.finished_at
    await db_session.commit()
    while await run_phases(orchestrator):
        pass

    assert ran == ["enrichment", "classification", "classification", "diff"]
    assert (await stored(db_session, scan)).state is ScanState.COMPLETED


async def test_retry_request_for_a_scan_that_did_not_fail_in_a_phase_is_dropped(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    orchestrator: Orchestrator,
    enrichment: FakePhase,
) -> None:
    lost = await make_scan(state=ScanState.FAILED)  # failed while probing
    lost.failure_reason = "chunk_lost"
    lost.retry_requested_at = lost.created_at
    done = await make_scan(state=ScanState.COMPLETED)
    done.retry_requested_at = done.created_at
    await db_session.commit()

    await run_phases(orchestrator)

    lost = await stored(db_session, lost)
    assert (lost.state, lost.failure_reason, lost.retry_requested_at) == (
        ScanState.FAILED,
        "chunk_lost",
        None,
    )
    # A request on a scan that has not failed is not touched, and does nothing.
    assert (await stored(db_session, done)).state is ScanState.COMPLETED
    assert enrichment.calls == []


# --- Restart, interruption, cancellation ---


async def test_orchestrator_restart_in_the_middle_of_a_phase_runs_the_phase_again(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    written: list[int] = []

    async def dies_the_first_time(sessions: Sessions, scan_id: int) -> None:
        written.append(scan_id)
        if len(written) == 1:
            # The process dies here: not an error of the phase, the end of everything.
            raise KeyboardInterrupt

    phases.register(ScanPhase.ENRICHMENT, dies_the_first_time)
    scan = await make_scan(state=ScanState.QUEUED)
    await probe_to_the_end(orchestrator, outbox, scan)
    before = await observed(db_session, scan)

    with pytest.raises(KeyboardInterrupt):
        await run_phases(orchestrator)
    scan = await stored(db_session, scan)
    assert (scan.state, scan.phase) == (ScanState.RUNNING, ScanPhase.ENRICHMENT)

    # The new instance does what every instance does at start-up, then runs.
    await lifecycle.mark_interrupted(db_session)
    await db_session.commit()
    await run_phases(orchestrator)

    scan = await stored(db_session, scan)
    assert (scan.state, scan.phase) == (ScanState.COMPLETED, None)
    assert written == [scan.id, scan.id]
    assert await observed(db_session, scan) == before


async def test_a_scan_in_a_phase_is_not_interrupted_when_the_scanner_goes_silent(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    orchestrator: Orchestrator,
    outbox: Outbox,
    enrichment: FakePhase,
) -> None:
    scan = await make_scan(state=ScanState.QUEUED)
    await probe_to_the_end(orchestrator, outbox, scan)

    interrupted = await lifecycle.mark_interrupted(db_session)
    await db_session.commit()

    assert interrupted == []
    assert (await stored(db_session, scan)).state is ScanState.RUNNING


async def test_scan_cancelled_while_its_phase_runs_stays_cancelled(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    async def cancelled_meanwhile(sessions: Sessions, scan_id: int) -> None:
        async with sessions() as db:
            running = await db.get_one(Scan, scan_id)
            running.cancel_requested_at = running.created_at
            await db.commit()
        await tick(orchestrator)

    phases.register(ScanPhase.ENRICHMENT, cancelled_meanwhile)
    scan = await make_scan(state=ScanState.QUEUED)
    await probe_to_the_end(orchestrator, outbox, scan)
    totals = dict((await stored(db_session, scan)).counters)

    await run_phases(orchestrator)

    scan = await stored(db_session, scan)
    assert (scan.state, scan.partial, scan.failed_phase) == (ScanState.CANCELLED, True, None)
    assert dict(scan.counters) == totals


async def test_phase_that_is_not_registered_any_more_fails_the_scan_instead_of_blocking(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    orchestrator: Orchestrator,
    outbox: Outbox,
    enrichment: FakePhase,
) -> None:
    scan = await make_scan(state=ScanState.QUEUED)
    await probe_to_the_end(orchestrator, outbox, scan)
    phases.PHASES.clear()

    await run_phases(orchestrator)

    scan = await stored(db_session, scan)
    assert (scan.state, scan.failed_phase) == (ScanState.FAILED, ScanPhase.ENRICHMENT)
