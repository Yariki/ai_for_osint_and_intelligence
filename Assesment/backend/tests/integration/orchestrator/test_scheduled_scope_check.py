"""Check 4.8: after the scope is reduced, a scheduled run is rejected, the
schedule is flagged, and an alert and an audit entry exist; nothing reaches
the scanner (FR-SCH-5)."""

from datetime import UTC, datetime
from ipaddress import IPv4Address

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core import scope as scope_rules
from ndm.db.models import (
    Account,
    Alert,
    Attestation,
    AuditEvent,
    Project,
    Scan,
    Schedule,
    ScheduleRun,
    ScopeEntry,
    ScopeEntryKind,
)
from ndm.orchestrator import scheduler
from ndm.orchestrator.tick import Orchestrator, tick
from tests.integration.orchestrator.conftest import Outbox, ScanFactory, scanner_reports
from tests.integration.orchestrator.scheduling import make_schedule, project_scan

PLANNED = datetime(2026, 11, 17, 2, 0, tzinfo=UTC)


async def reduce_scope(db: AsyncSession, project_id: int, start: str, end: str) -> None:
    """The analyst saves a new, smaller scope: a new attestation becomes current."""
    account = await db.scalar(select(Account).limit(1))
    assert account is not None
    attestation = Attestation(project_id=project_id, account_id=account.id)
    db.add(attestation)
    await db.flush()
    db.add(
        ScopeEntry(
            attestation_id=attestation.id,
            kind=ScopeEntryKind.RANGE,
            original_text=f"{start}-{end}",
            start_address=IPv4Address(start),
            end_address=IPv4Address(end),
        )
    )
    project = await db.get_one(Project, project_id)
    project.current_attestation_id = attestation.id
    await db.commit()


async def scans_of(db: AsyncSession, schedule: Schedule) -> int:
    count = await db.scalar(
        select(func.count()).select_from(Scan).where(Scan.schedule_id == schedule.id)
    )
    return count or 0


async def test_a_run_outside_the_reduced_scope_is_rejected_and_the_schedule_flagged(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    project = await project_scan(make_scan)
    schedule = await make_schedule(
        db_session, project, PLANNED, targets=["8.8.8.1-8.8.8.4", "8.8.8.200"]
    )
    await reduce_scope(db_session, project.project_id, "8.8.8.1", "8.8.8.2")

    outcome = await scheduler.run_due(
        orchestrator.sessions, orchestrator.settings, orchestrator.never_scan, PLANNED
    )

    # No scan: nothing can reach the scanner.
    assert outcome.started == []
    assert await scans_of(db_session, schedule) == 0
    await scanner_reports(orchestrator)
    await tick(orchestrator)
    assert outbox.chunks == []

    run = await db_session.scalar(select(ScheduleRun))
    assert run is not None
    assert (run.planned_at, run.outcome, run.reason, run.scan_id) == (
        PLANNED,
        "rejected",
        "targets_out_of_scope",
        None,
    )
    schedule = await db_session.get_one(Schedule, schedule.id, populate_existing=True)
    assert (schedule.flag_reason, schedule.last_outcome) == ("targets_out_of_scope", "rejected")

    alert = await db_session.scalar(select(Alert))
    assert alert is not None
    assert (alert.type, alert.project_id, alert.link) == (
        "scheduled_run_rejected",
        project.project_id,
        {"schedule_id": schedule.id},
    )
    assert "8.8.8.1-8.8.8.4, 8.8.8.200" in alert.summary
    assert [event.alert_id for event in outcome.alerts] == [alert.id]

    entry = await db_session.scalar(select(AuditEvent).where(AuditEvent.action == "scan.rejected"))
    assert entry is not None
    assert (entry.actor, entry.object_type, entry.object_id) == (
        "system",
        "project",
        str(project.project_id),
    )
    assert entry.details == {
        "point": "scheduler",
        "schedule_id": schedule.id,
        "reason": "targets_out_of_scope",
        "rejected": [
            {"target": "8.8.8.1-8.8.8.4", "reason": "out_of_scope"},
            {"target": "8.8.8.200", "reason": "out_of_scope"},
        ],
    }


async def test_a_flagged_schedule_does_not_run_again(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    project = await project_scan(make_scan)
    schedule = await make_schedule(db_session, project, PLANNED)
    await reduce_scope(db_session, project.project_id, "8.8.8.1", "8.8.8.2")
    await scheduler.run_due(
        orchestrator.sessions, orchestrator.settings, orchestrator.never_scan, PLANNED
    )
    later = (await db_session.get_one(Schedule, schedule.id, populate_existing=True)).next_run_at

    outcome = await scheduler.run_due(
        orchestrator.sessions, orchestrator.settings, orchestrator.never_scan, later
    )

    assert outcome.started == [] and outcome.alerts == []
    assert await db_session.scalar(select(func.count()).select_from(ScheduleRun)) == 1


async def test_a_run_inside_the_reduced_scope_still_starts(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    project = await project_scan(make_scan)
    schedule = await make_schedule(db_session, project, PLANNED, targets=["8.8.8.1", "8.8.8.2"])
    await reduce_scope(db_session, project.project_id, "8.8.8.1", "8.8.8.2")

    outcome = await scheduler.run_due(
        orchestrator.sessions, orchestrator.settings, orchestrator.never_scan, PLANNED
    )

    assert len(outcome.started) == 1
    scan = await db_session.get_one(Scan, outcome.started[0])
    # The scan is made under the attestation that is current now.
    project_row = await db_session.get_one(Project, project.project_id, populate_existing=True)
    assert scan.attestation_id == project_row.current_attestation_id != project.attestation_id
    assert await scans_of(db_session, schedule) == 1


@pytest.mark.parametrize("never_scan", ["8.8.8.3", "8.8.8.0/30"])
async def test_a_target_on_the_never_scan_list_is_rejected(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, never_scan: str
) -> None:
    project = await project_scan(make_scan)
    schedule = await make_schedule(db_session, project, PLANNED)

    outcome = await scheduler.run_due(
        orchestrator.sessions,
        orchestrator.settings,
        scope_rules.parse_never_scan([never_scan]),
        PLANNED,
    )

    assert outcome.started == []
    assert await scans_of(db_session, schedule) == 0
    flagged = await db_session.get_one(Schedule, schedule.id, populate_existing=True)
    assert flagged.flag_reason == "targets_out_of_scope"


async def test_a_project_without_a_scope_rejects_every_run(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    project = await project_scan(make_scan)
    schedule = await make_schedule(db_session, project, PLANNED)
    row = await db_session.get_one(Project, project.project_id)
    row.current_attestation_id = None
    await db_session.commit()

    outcome = await scheduler.run_due(
        orchestrator.sessions, orchestrator.settings, orchestrator.never_scan, PLANNED
    )

    assert outcome.started == []
    assert [event.alert_type for event in outcome.alerts] == ["scheduled_run_rejected"]
    assert await scans_of(db_session, schedule) == 0
