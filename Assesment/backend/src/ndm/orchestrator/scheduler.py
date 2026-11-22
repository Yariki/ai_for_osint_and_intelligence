"""The scheduler: scans that start without the analyst (FR-SCH-1 to FR-SCH-5).

Every 15 seconds the scheduler takes the enabled schedules whose next run is
due. Each one is handled in a transaction of its own:

1. If the previous run of the schedule is still active, the run is recorded
   as skipped and an alert is raised (FR-SCH-3).
2. Otherwise the scope is checked. If a target is no longer inside the
   project scope, the run is recorded as rejected, the schedule is flagged,
   and an alert and an audit entry are written (FR-SCH-5). Nothing reaches
   the scanner.
3. Otherwise a scan is created in ``queued``. It shares the queue and the
   concurrent ceiling with manual scans, and the tick starts it.
4. The next run is set to the first occurrence strictly after now. After
   downtime that gives exactly one catch-up run (FR-SCH-4).

A flagged schedule does not run until the analyst saves it again. Schedules of
an archived or deleted project do not run. Every audit entry the scheduler
writes has the system as its actor (FR-AUD-2).
"""

import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from ipaddress import IPv4Address

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ndm.core import scope as scope_rules
from ndm.core.messages import AlertCreated
from ndm.core.ports import PortProfile, parse_port_list, profile_ports
from ndm.core.settings import Settings
from ndm.db.audit import SYSTEM_ACTOR, record_audit
from ndm.db.models import Project, ProjectState, Scan, Schedule, ScheduleRun
from ndm.orchestrator import alerts
from ndm.orchestrator.diff import ranges
from ndm.orchestrator.dispatch import current_scope
from ndm.orchestrator.lifecycle import NOT_FINAL
from ndm.orchestrator.planning import ScanParameters
from ndm.orchestrator.schedule_math import Recurrence, ScheduleError, next_run

log = structlog.get_logger(__name__)

SCHEDULER_SECONDS = 15
# One scan probes at most this many addresses (FR-CFG-1), as the API checks.
MAX_TARGETS = 256

STARTED = "started"
SKIPPED = "skipped"
REJECTED = "rejected"


@dataclass
class Outcome:
    """What one pass of the scheduler did."""

    started: list[int] = field(default_factory=list)
    """The scans it created."""
    alerts: list[AlertCreated] = field(default_factory=list)


class Rejected(Exception):
    def __init__(self, reason: str, details: list[dict[str, str]]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details


def recurrence_of(schedule: Schedule) -> Recurrence:
    return Recurrence(
        interval_seconds=schedule.interval_seconds,
        cron=schedule.cron,
        time_zone=schedule.time_zone,
    )


def ports_of(parameters: dict[str, object]) -> list[int]:
    """The ports of a schedule's parameters: a built-in profile or a custom list."""
    profile = parameters.get("port_profile")
    custom = parameters.get("custom_ports")
    if profile == "custom" and isinstance(custom, str):
        return parse_port_list(custom)
    return list(profile_ports(PortProfile(str(profile))))


def check_targets(
    texts: Sequence[str],
    scope: Sequence[scope_rules.ScopeEntry],
    never_scan: Sequence[scope_rules.ScopeEntry],
    allow_non_public: bool,
) -> list[scope_rules.ScopeEntry]:
    """The schedule's target entries, each checked against the scope as it is now.

    Raises ``Rejected`` listing every entry that may not be probed.
    """
    entries: list[scope_rules.ScopeEntry] = []
    rejected: list[dict[str, str]] = []
    for text in texts:
        try:
            entry = scope_rules.parse_entry(text)
            scope_rules.check_target(
                entry, scope=scope, never_scan=never_scan, allow_non_public=allow_non_public
            )
            entries.append(entry)
        except scope_rules.ScopeError as error:
            rejected.append({"target": text, "reason": error.code})
    if rejected:
        raise Rejected("targets_out_of_scope", rejected)
    if not entries or scope_rules.count_addresses(entries) > MAX_TARGETS:
        raise Rejected("invalid_targets", [])
    return entries


async def _previous_active(db: AsyncSession, schedule: Schedule) -> int | None:
    """The scan of an earlier run of this schedule that has not ended, if any."""
    return await db.scalar(
        select(Scan.id)
        .where(
            Scan.schedule_id == schedule.id,
            Scan.state.in_(NOT_FINAL),
            Scan.deleted_at.is_(None),
        )
        .limit(1)
    )


def _scan(
    schedule: Schedule,
    project: Project,
    entries: Sequence[scope_rules.ScopeEntry],
    settings: Settings,
) -> Scan:
    """A queued scan with the schedule's targets and parameters."""
    assert project.current_attestation_id is not None  # every target is in scope
    parameters = ScanParameters.model_validate(schedule.parameters)
    # The rate ceiling may have been lowered since the schedule was saved.
    parameters.pps = min(parameters.pps, settings.global_rate_pps)
    targets = sorted(
        {
            IPv4Address(number)
            for entry in entries
            for number in range(int(entry.start), int(entry.end) + 1)
        }
    )
    ports = ports_of(schedule.parameters)
    return Scan(
        project_id=project.id,
        attestation_id=project.current_attestation_id,
        schedule_id=schedule.id,
        targets=targets,
        probed_ports=[Range(low, high + 1) for low, high in ranges(frozenset(ports))],
        parameters={
            **parameters.model_dump(mode="json"),
            "port_profile": schedule.parameters.get("port_profile"),
        },
        technique_requested=str(schedule.parameters.get("technique", "automatic")),
        seed=secrets.randbits(31),
    )


async def _run(
    db: AsyncSession,
    schedule: Schedule,
    project: Project,
    *,
    settings: Settings,
    never_scan: Sequence[scope_rules.ScopeEntry],
    now: datetime,
    outcome: Outcome,
) -> None:
    """Handle one due schedule. Does not commit."""
    planned_at = schedule.next_run_at
    run = ScheduleRun(schedule_id=schedule.id, planned_at=planned_at, outcome=STARTED)

    active = await _previous_active(db, schedule)
    if active is not None:
        run.outcome, run.reason = SKIPPED, f"scan {active} is still active"
        outcome.alerts.append(
            await alerts.raise_alert(
                db,
                alerts.SCHEDULED_RUN_SKIPPED,
                f"{schedule.name}: the run planned for {planned_at:%Y-%m-%d %H:%M} UTC was "
                f"skipped, because scan {active} is still active.",
                project_id=project.id,
                link={"schedule_id": schedule.id, "scan_id": active},
            )
        )
    else:
        try:
            scope = await current_scope(db, project)
            entries = check_targets(
                schedule.targets, scope, never_scan, settings.allow_non_public_targets
            )
            scan = _scan(schedule, project, entries, settings)
        except Rejected as rejected:
            await _reject(db, schedule, project, run, rejected, outcome)
        except (ValueError, KeyError) as error:
            # Stored parameters this version cannot read: rejected, never guessed.
            log.warning("schedule_parameters_invalid", error=str(error))
            await _reject(db, schedule, project, run, Rejected("invalid_parameters", []), outcome)
        else:
            db.add(scan)
            await db.flush()
            run.scan_id = scan.id
            outcome.started.append(scan.id)
            await record_audit(
                db,
                actor=SYSTEM_ACTOR,
                action="scan.launch",
                object_type="scan",
                object_id=scan.id,
                details={
                    "project_id": project.id,
                    "schedule_id": schedule.id,
                    "targets": len(scan.targets),
                    "port_profile": scan.parameters.get("port_profile"),
                    "technique": scan.technique_requested,
                    "attestation_id": scan.attestation_id,
                },
            )

    db.add(run)
    schedule.last_run_at = now
    schedule.last_outcome = run.outcome
    try:
        schedule.next_run_at = next_run(recurrence_of(schedule), now, anchor=planned_at)
    except ScheduleError as error:
        # A time zone that has gone from the system, for example: stop, and say why.
        schedule.flag_reason = error.code
        log.error("schedule_not_computable", schedule_id=schedule.id, error=error.message)
    await db.flush()
    log.info(
        "schedule_run",
        schedule_id=schedule.id,
        outcome=run.outcome,
        scan_id=run.scan_id,
        next_run_at=schedule.next_run_at.isoformat(),
    )


async def _reject(
    db: AsyncSession,
    schedule: Schedule,
    project: Project,
    run: ScheduleRun,
    rejected: Rejected,
    outcome: Outcome,
) -> None:
    run.outcome, run.reason = REJECTED, rejected.reason
    schedule.flag_reason = rejected.reason
    await record_audit(
        db,
        actor=SYSTEM_ACTOR,
        action="scan.rejected",
        object_type="project",
        object_id=project.id,
        details={
            "point": "scheduler",
            "schedule_id": schedule.id,
            "reason": rejected.reason,
            "rejected": rejected.details,
        },
    )
    targets = ", ".join(item["target"] for item in rejected.details)
    why = f"outside the project scope: {targets}" if targets else rejected.reason
    outcome.alerts.append(
        await alerts.raise_alert(
            db,
            alerts.SCHEDULED_RUN_REJECTED,
            f"{schedule.name}: the run was rejected and the schedule flagged ({why}).",
            project_id=project.id,
            link={"schedule_id": schedule.id},
        )
    )


async def run_due(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    never_scan: Sequence[scope_rules.ScopeEntry],
    now: datetime | None = None,
) -> Outcome:
    """One pass of the scheduler: handle every schedule that is due at ``now``."""
    now = now or datetime.now(UTC)
    outcome = Outcome()
    due = (
        select(Schedule.id)
        .join(Project, Project.id == Schedule.project_id)
        .where(
            Schedule.enabled,
            Schedule.flag_reason.is_(None),
            Schedule.next_run_at <= now,
            Project.state == ProjectState.ACTIVE,
            Project.deleted_at.is_(None),
        )
        .order_by(Schedule.next_run_at, Schedule.id)
    )
    async with sessions() as db:
        schedule_ids = list(await db.scalars(due))

    for schedule_id in schedule_ids:
        structlog.contextvars.bind_contextvars(schedule_id=schedule_id)
        try:
            async with sessions() as db:
                # Locked, and checked again: it may have been edited or handled meanwhile.
                schedule = await db.scalar(
                    due.with_only_columns(Schedule)
                    .where(Schedule.id == schedule_id)
                    .with_for_update(of=Schedule, skip_locked=True)
                )
                if schedule is None:
                    continue
                project = await db.get_one(Project, schedule.project_id)
                handled = Outcome()
                await _run(
                    db,
                    schedule,
                    project,
                    settings=settings,
                    never_scan=never_scan,
                    now=now,
                    outcome=handled,
                )
                await db.commit()
                outcome.started += handled.started
                outcome.alerts += handled.alerts
        except Exception:
            # Rolled back; the schedule is still due and is tried at the next pass.
            log.exception("schedule_run_failed")
        finally:
            structlog.contextvars.unbind_contextvars("schedule_id")
    return outcome
