"""Checks 4.5 to 4.7: a due schedule starts its scan within one scheduler tick;
a run whose predecessor is still active is skipped with an alert; after
downtime exactly one run starts. Scheduled and manual scans share the queue,
schedules of an archived project do not run, and the system is the actor."""

import asyncio
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.ports import PortProfile, profile_ports
from ndm.db.models import (
    Alert,
    AuditEvent,
    Project,
    ProjectState,
    Scan,
    ScanState,
    Schedule,
    ScheduleRun,
)
from ndm.orchestrator import scheduler
from ndm.orchestrator.diff import ranges
from ndm.orchestrator.tick import Orchestrator, tick
from tests.integration.orchestrator.conftest import (
    TARGETS,
    ScanFactory,
    scanner_reports,
)
from tests.integration.orchestrator.scheduling import PARAMETERS, make_schedule, project_scan

PLANNED = datetime(2026, 11, 17, 2, 0, tzinfo=UTC)
INTERVAL = timedelta(hours=6)
TICK = timedelta(seconds=scheduler.SCHEDULER_SECONDS)


async def run_due(orchestrator: Orchestrator, now: datetime) -> scheduler.Outcome:
    return await scheduler.run_due(
        orchestrator.sessions, orchestrator.settings, orchestrator.never_scan, now
    )


async def scheduled_scans(db: AsyncSession, schedule: Schedule) -> list[Scan]:
    found = await db.scalars(
        select(Scan)
        .where(Scan.schedule_id == schedule.id)
        .order_by(Scan.id)
        .execution_options(populate_existing=True)
    )
    return list(found)


async def runs_of(db: AsyncSession, schedule: Schedule) -> list[tuple[datetime, str, int | None]]:
    rows = await db.execute(
        select(ScheduleRun.planned_at, ScheduleRun.outcome, ScheduleRun.scan_id)
        .where(ScheduleRun.schedule_id == schedule.id)
        .order_by(ScheduleRun.id)
    )
    return [tuple(row) for row in rows]  # type: ignore[misc]


async def fresh(db: AsyncSession, schedule: Schedule) -> Schedule:
    return await db.get_one(Schedule, schedule.id, populate_existing=True)


# --- Check 4.5 ---


async def test_a_due_schedule_creates_its_scan_within_one_tick(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    project = await project_scan(make_scan)
    schedule = await make_schedule(db_session, project, PLANNED)

    # Not yet due.
    assert (await run_due(orchestrator, PLANNED - timedelta(seconds=1))).started == []
    assert await scheduled_scans(db_session, schedule) == []

    # The first scheduler tick at or after the planned time.
    outcome = await run_due(orchestrator, PLANNED + TICK - timedelta(seconds=1))

    [scan] = await scheduled_scans(db_session, schedule)
    assert outcome.started == [scan.id]
    assert scan.state is ScanState.QUEUED
    assert (scan.project_id, scan.attestation_id) == (project.project_id, project.attestation_id)
    assert scan.targets == TARGETS
    expected_ports = ranges(frozenset(profile_ports(PortProfile.TOP_100)))
    assert scan.probed_ports == [Range(low, high + 1) for low, high in expected_ports]
    assert scan.parameters["port_profile"] == "top-100"
    assert scan.parameters["discovery"] == PARAMETERS["discovery"]
    assert scan.technique_requested == "automatic"

    assert await runs_of(db_session, schedule) == [(PLANNED, "started", scan.id)]
    schedule = await fresh(db_session, schedule)
    assert schedule.next_run_at == PLANNED + INTERVAL
    assert (schedule.last_outcome, schedule.last_run_at) == (
        "started",
        PLANNED + TICK - timedelta(seconds=1),
    )


async def test_the_scheduled_scan_is_started_by_the_tick(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    project = await project_scan(make_scan)
    schedule = await make_schedule(db_session, project, PLANNED)
    await run_due(orchestrator, PLANNED)

    await scanner_reports(orchestrator)
    await tick(orchestrator)

    [scan] = await scheduled_scans(db_session, schedule)
    assert scan.state is ScanState.RUNNING


async def test_scheduled_and_manual_scans_share_the_queue_and_its_ceiling(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    orchestrator.settings = orchestrator.settings.model_copy(update={"max_concurrent_scans": 1})
    manual = await make_scan(state=ScanState.QUEUED)
    schedule = await make_schedule(db_session, manual, PLANNED)
    await run_due(orchestrator, PLANNED)

    await scanner_reports(orchestrator)
    await tick(orchestrator)

    [scheduled] = await scheduled_scans(db_session, schedule)
    states = await db_session.execute(
        select(Scan.id, Scan.state).where(Scan.id.in_([manual.id, scheduled.id]))
    )
    assert {scan_id: state for scan_id, state in states} == {
        manual.id: ScanState.RUNNING,  # queued first, started first
        scheduled.id: ScanState.QUEUED,
    }


async def test_the_actor_of_a_scheduled_launch_is_the_system(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    project = await project_scan(make_scan)
    schedule = await make_schedule(db_session, project, PLANNED)
    await run_due(orchestrator, PLANNED)

    [scan] = await scheduled_scans(db_session, schedule)
    entry = await db_session.scalar(select(AuditEvent).where(AuditEvent.action == "scan.launch"))
    assert entry is not None
    assert (entry.actor, entry.object_type, entry.object_id) == ("system", "scan", str(scan.id))
    assert entry.details["schedule_id"] == schedule.id
    assert entry.details["targets"] == len(TARGETS)


# --- Check 4.6 ---


async def test_a_run_whose_predecessor_is_still_active_is_skipped_with_an_alert(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    project = await project_scan(make_scan)
    schedule = await make_schedule(db_session, project, PLANNED)
    await run_due(orchestrator, PLANNED)
    [first] = await scheduled_scans(db_session, schedule)
    first.state = ScanState.RUNNING  # still probing when the next run is due
    await db_session.commit()

    outcome = await run_due(orchestrator, PLANNED + INTERVAL)

    assert outcome.started == []
    assert [s.id for s in await scheduled_scans(db_session, schedule)] == [first.id]
    assert await runs_of(db_session, schedule) == [
        (PLANNED, "started", first.id),
        (PLANNED + INTERVAL, "skipped", None),
    ]
    alert = await db_session.scalar(select(Alert))
    assert alert is not None
    assert (alert.type, alert.project_id) == ("scheduled_run_skipped", project.project_id)
    assert alert.link == {"schedule_id": schedule.id, "scan_id": first.id}
    assert [event.alert_id for event in outcome.alerts] == [alert.id]
    schedule = await fresh(db_session, schedule)
    assert (schedule.last_outcome, schedule.next_run_at) == ("skipped", PLANNED + 2 * INTERVAL)


async def test_the_run_after_the_predecessor_ended_starts(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    project = await project_scan(make_scan)
    schedule = await make_schedule(db_session, project, PLANNED)
    await run_due(orchestrator, PLANNED)
    [first] = await scheduled_scans(db_session, schedule)
    first.state = ScanState.FAILED
    await db_session.commit()

    outcome = await run_due(orchestrator, PLANNED + INTERVAL)

    assert len(outcome.started) == 1
    assert len(await scheduled_scans(db_session, schedule)) == 2


# --- Check 4.7 ---


async def test_after_downtime_exactly_one_run_starts(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    project = await project_scan(make_scan)
    schedule = await make_schedule(db_session, project, PLANNED)
    # Down for five and a half intervals.
    back = PLANNED + 5 * INTERVAL + INTERVAL / 2

    first = await run_due(orchestrator, back)
    again = await run_due(orchestrator, back + TICK)

    assert len(first.started) == 1 and again.started == []
    assert [outcome for _, outcome, _ in await runs_of(db_session, schedule)] == ["started"]
    # The next run keeps to the schedule's own times.
    assert (await fresh(db_session, schedule)).next_run_at == PLANNED + 6 * INTERVAL


async def test_after_downtime_a_cron_schedule_runs_once_and_keeps_its_wall_clock(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    kyiv = ZoneInfo("Europe/Kyiv")
    planned = datetime(2026, 11, 10, 2, 0, tzinfo=kyiv)
    project = await project_scan(make_scan)
    schedule = await make_schedule(
        db_session,
        project,
        planned,
        interval_seconds=None,
        cron="0 2 * * *",
        time_zone="Europe/Kyiv",
    )
    back = datetime(2026, 11, 17, 9, 30, tzinfo=kyiv)  # a week later

    assert len((await run_due(orchestrator, back)).started) == 1
    assert (await run_due(orchestrator, back + TICK)).started == []
    assert (await fresh(db_session, schedule)).next_run_at == datetime(
        2026, 11, 18, 2, 0, tzinfo=kyiv
    )


async def test_two_scheduler_passes_at_once_start_one_run(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    project = await project_scan(make_scan)
    schedule = await make_schedule(db_session, project, PLANNED)

    outcomes = await asyncio.gather(*(run_due(orchestrator, PLANNED) for _ in range(4)))

    assert sum(len(outcome.started) for outcome in outcomes) == 1
    assert len(await runs_of(db_session, schedule)) == 1


# --- Schedules that do not run ---


async def test_disabled_flagged_and_archived_schedules_do_not_run(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    disabled = await make_schedule(
        db_session, await project_scan(make_scan), PLANNED, enabled=False
    )
    flagged = await make_schedule(
        db_session, await project_scan(make_scan), PLANNED, flag_reason="targets_out_of_scope"
    )
    archived_project = await project_scan(make_scan)
    archived = await make_schedule(db_session, archived_project, PLANNED)
    project = await db_session.get_one(Project, archived_project.project_id)
    project.state = ProjectState.ARCHIVED
    await db_session.commit()

    outcome = await run_due(orchestrator, PLANNED + INTERVAL)

    assert outcome.started == [] and outcome.alerts == []
    assert await db_session.scalar(select(func.count()).select_from(ScheduleRun)) == 0
    for schedule in (disabled, flagged, archived):
        assert (await fresh(db_session, schedule)).next_run_at == PLANNED


async def test_parameters_this_version_cannot_read_reject_the_run(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    project = await project_scan(make_scan)
    schedule = await make_schedule(
        db_session, project, PLANNED, parameters=PARAMETERS | {"port_profile": "top-5"}
    )

    outcome = await run_due(orchestrator, PLANNED)

    assert outcome.started == []
    assert await runs_of(db_session, schedule) == [(PLANNED, "rejected", None)]
    assert (await fresh(db_session, schedule)).flag_reason == "invalid_parameters"
    assert [event.alert_type for event in outcome.alerts] == ["scheduled_run_rejected"]


async def test_the_pps_of_a_scheduled_scan_keeps_to_the_current_ceiling(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    orchestrator.settings = orchestrator.settings.model_copy(update={"global_rate_pps": 500})
    project = await project_scan(make_scan)
    schedule = await make_schedule(db_session, project, PLANNED)

    await run_due(orchestrator, PLANNED)

    [scan] = await scheduled_scans(db_session, schedule)
    assert scan.parameters["pps"] == 500
    assert scan.targets[0] == IPv4Address("8.8.8.1")
