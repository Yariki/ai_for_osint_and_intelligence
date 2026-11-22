"""The phases after probing (architecture section 6.8).

When the last chunk of a scan is done, the scan goes through the registered
phases in order and is then complete:

    port scan -> enrichment -> classification -> diff -> completed

A phase is a function that reads stored data and writes idempotently. That is
all the runner relies on:

- A phase that raises marks the scan failed, with the phase recorded. Nothing
  the scan observed is touched (NFR-REL-3).
- "Retry failed phase" puts the scan back into that phase. Probing and the
  phases before it are not repeated; the phases after it follow as usual.
- A phase that was interrupted by a crash is simply run again: the scan is
  still running in that phase when the orchestrator comes back.
- The diff phase belongs to scheduled scans only: a manual scan goes from
  classification straight to completed.
- A phase may return UI events, such as an alert it raised; they are
  published after the phase has committed.

One phase function runs at a time, so no two scans are ever in the same phase
at once.
"""

from collections.abc import Awaitable, Callable, Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ndm.db.models import Scan, ScanPhase, ScanState
from ndm.orchestrator.events import Event, scan_progress
from ndm.orchestrator.recording import finish_scan

log = structlog.get_logger(__name__)

Sessions = async_sessionmaker[AsyncSession]
PhaseFunction = Callable[[Sessions, int], Awaitable[Sequence[Event] | None]]
"""Runs one phase for one scan. It opens its own transactions, and returns
the UI events to publish once it has committed, if any."""

# The order in which phases run.
ORDER = (ScanPhase.ENRICHMENT, ScanPhase.CLASSIFICATION, ScanPhase.DIFF)

# The phases this installation has. The task that builds a phase registers it.
PHASES: dict[ScanPhase, PhaseFunction] = {}


def register(phase: ScanPhase, function: PhaseFunction) -> None:
    assert phase in ORDER
    PHASES[phase] = function


def applies(phase: ScanPhase, scan: Scan) -> bool:
    """Whether a registered phase is run for a scan. The diff compares a
    scheduled run with the one before it; a manual scan has none (FR-HIS-6)."""
    return phase in PHASES and (phase is not ScanPhase.DIFF or scan.schedule_id is not None)


def first_phase(scan: Scan) -> ScanPhase | None:
    """The phase that follows probing for this scan, or None if there is none."""
    return next((phase for phase in ORDER if applies(phase, scan)), None)


def phase_after(phase: ScanPhase, scan: Scan) -> ScanPhase | None:
    later = ORDER[ORDER.index(phase) + 1 :]
    return next((candidate for candidate in later if applies(candidate, scan)), None)


async def apply_retries(db: AsyncSession) -> list[Scan]:
    """Put every failed scan whose retry was requested back into its failed phase.

    A request for a scan that did not fail in a phase is dropped. Does not commit.
    """
    scans = await db.scalars(
        select(Scan)
        .where(
            Scan.retry_requested_at.is_not(None),
            Scan.state == ScanState.FAILED,
            Scan.deleted_at.is_(None),
        )
        .order_by(Scan.id)
        .with_for_update()
    )
    retried = []
    for scan in scans:
        scan.retry_requested_at = None
        if scan.failed_phase is None:
            continue
        scan.state = ScanState.RUNNING
        scan.phase = scan.failed_phase
        scan.failed_phase = None
        scan.failure_reason = None
        scan.partial = False
        scan.finished_at = None
        retried.append(scan)
        log.info("phase_retry", scan_id=scan.id, phase=scan.phase.value)
    await db.flush()
    return retried


async def _run_phase(sessions: Sessions, scan_id: int) -> list[Event]:
    """Run the current phase of one scan, and move the scan on. Returns the
    events of the phase and the scan's progress."""
    async with sessions() as db:
        scan = await db.get(Scan, scan_id)
        if scan is None or scan.state is not ScanState.RUNNING or scan.phase not in ORDER:
            return []
        phase = scan.phase

    failed = False
    events: list[Event] = []
    try:
        events += await PHASES[phase](sessions, scan_id) or []
    except Exception:
        # Includes a phase this installation does not have (KeyError).
        log.exception("phase_failed", phase=phase.value)
        failed = True

    async with sessions() as db:
        scan = await db.scalar(select(Scan).where(Scan.id == scan_id).with_for_update())
        # Cancelled or deleted while the phase ran: the tick has ended it.
        if scan is None or scan.state is not ScanState.RUNNING or scan.phase is not phase:
            return events
        following = phase_after(phase, scan)
        if failed:
            await finish_scan(db, scan, ScanState.FAILED, reason=f"{phase.value}_failed")
            scan.failed_phase = phase
        elif following is None:
            await finish_scan(db, scan, ScanState.COMPLETED)
            log.info("scan_completed")
        else:
            scan.phase = following
        await db.flush()
        events.append(await scan_progress(db, scan))
        await db.commit()
    return events


async def run_pending(
    sessions: Sessions, publish_events: Callable[[Sequence[Event]], Awaitable[None]]
) -> bool:
    """One pass: apply the retry requests, then run the current phase of every
    scan that is in one, oldest scan first. Returns True if anything was done."""
    async with sessions() as db:
        retried = await apply_retries(db)
        events: list[Event] = [await scan_progress(db, scan) for scan in retried]
        await db.commit()
        waiting = list(
            await db.scalars(
                select(Scan.id)
                .where(
                    Scan.state == ScanState.RUNNING,
                    Scan.phase.in_(ORDER),
                    Scan.deleted_at.is_(None),
                )
                .order_by(Scan.id)
            )
        )

    for scan_id in waiting:
        structlog.contextvars.bind_contextvars(scan_id=scan_id)
        try:
            events += await _run_phase(sessions, scan_id)
        finally:
            structlog.contextvars.unbind_contextvars("scan_id")

    await publish_events(events)
    return bool(waiting or retried)
