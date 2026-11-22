"""The diff phase of a scheduled scan (FR-HIS-6, FR-HIS-7; architecture 11.3).

After a scheduled scan has been classified, it is compared with its baseline:
the previous run of the same schedule that completed and is not partial.
Cancelled, failed and partial runs are passed over. The first run of a
schedule has no baseline and nothing to compare.

The diff is stored, because an alert must keep pointing at what was detected
at the time. A diff with changes raises one alert that links to it; an empty
diff raises none. Diff, change events and alert are written in one
transaction, and a scan that already has its diff is not compared again, so
running the phase twice changes nothing.

A manual scan has no diff phase: the phase runner does not enter it.
"""

from collections.abc import Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ndm.db.models import ChangeEvent, Scan, ScanDiff, ScanState, Schedule
from ndm.orchestrator import alerts
from ndm.orchestrator.diff import KINDS, Diff, compute_diff
from ndm.orchestrator.events import Event

log = structlog.get_logger(__name__)


async def baseline(db: AsyncSession, scan: Scan) -> Scan | None:
    """The previous completed, non-partial run of the scan's schedule."""
    return await db.scalar(
        select(Scan)
        .where(
            Scan.schedule_id == scan.schedule_id,
            Scan.id < scan.id,
            Scan.state == ScanState.COMPLETED,
            Scan.partial.is_(False),
            Scan.deleted_at.is_(None),
        )
        .order_by(Scan.id.desc())
        .limit(1)
    )


async def store(db: AsyncSession, diff: Diff) -> ScanDiff:
    """Write a diff and its change events. Does not commit."""
    stored = ScanDiff(
        base_scan_id=diff.base_scan_id,
        target_scan_id=diff.target_scan_id,
        # Inclusive ranges are stored half-open, like a scan's probed ports.
        compared_ports=[Range(low, high + 1) for low, high in diff.compared_ports],
        not_comparable=diff.not_comparable,
        summary=diff.summary,
    )
    db.add(stored)
    await db.flush()
    db.add_all(
        ChangeEvent(
            diff_id=stored.id,
            address=change.address,
            kind=change.kind,
            port=change.port,
            field=change.field,
            before=change.before,
            after=change.after,
            dataset_change=change.dataset_change,
        )
        for change in diff.changes
    )
    await db.flush()
    return stored


def summary_text(schedule_name: str, diff: Diff) -> str:
    counts = diff.summary
    parts = [f"{kind.replace('_', ' ')}: {counts[kind]}" for kind in KINDS if counts[kind]]
    total = len(diff.changes)
    noun = "change" if total == 1 else "changes"
    return f"{schedule_name}: {total} {noun} since scan {diff.base_scan_id} ({', '.join(parts)})."


async def run_phase(sessions: async_sessionmaker[AsyncSession], scan_id: int) -> Sequence[Event]:
    async with sessions() as db:
        scan = await db.get(Scan, scan_id)
        if scan is None or scan.schedule_id is None:
            return []
        if await db.scalar(select(ScanDiff.id).where(ScanDiff.target_scan_id == scan_id)):
            return []  # compared already, before a restart
        base = await baseline(db, scan)
        if base is None:
            log.info("diff_without_baseline", schedule_id=scan.schedule_id)
            return []

        diff = await compute_diff(db, base.id, scan.id)
        stored = await store(db, diff)
        events: list[Event] = []
        if diff.changes:
            schedule = await db.get(Schedule, scan.schedule_id)
            name = schedule.name if schedule is not None else f"Schedule {scan.schedule_id}"
            events.append(
                await alerts.raise_alert(
                    db,
                    alerts.CHANGES_DETECTED,
                    summary_text(name, diff),
                    project_id=scan.project_id,
                    link={"diff_id": stored.id, "scan_id": scan.id, "base_scan_id": base.id},
                )
            )
        await db.commit()
        log.info("diff_stored", diff_id=stored.id, base_scan_id=base.id, changes=len(diff.changes))
    return events
