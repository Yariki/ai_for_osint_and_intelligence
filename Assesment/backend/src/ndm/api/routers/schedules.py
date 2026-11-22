"""Schedules: recurring scans of a project (FR-SCH-1, FR-SCH-2, FR-SCH-6).

A schedule is checked when it is saved, and the scheduler checks it again at
every run:

- every target must be inside the current project scope, public, and not on
  the never-scan list; otherwise nothing is saved and the rejection is in the
  audit log;
- the runs must be at least the operator's minimum interval apart; for a
  cron expression, the gaps between its upcoming runs are checked;
- the rate must be within the operator ceiling.

Saving a schedule computes its next run from now and clears a flag the
scheduler set: the analyst has looked at it again. Creation and every change
are in the audit log, in the same transaction. An archived project accepts no
schedule writes.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.api.deps import CurrentSessionDep, DbDep, SettingsDep
from ndm.api.errors import ApiError
from ndm.api.pagination import Page, PageParamsDep
from ndm.api.routers.profiles import check_ceiling
from ndm.api.routers.scans import _project
from ndm.api.schemas.common import ErrorDetail
from ndm.api.schemas.scans import ScanSettings
from ndm.api.schemas.schedules import ScheduleIn, ScheduleOut, ScheduleRunOut
from ndm.core.settings import Settings
from ndm.db.audit import record_audit
from ndm.db.models import Project, ProjectState, Scan, Schedule, ScheduleRun
from ndm.orchestrator.dispatch import current_scope
from ndm.orchestrator.schedule_math import Recurrence, ScheduleError, check, next_run
from ndm.orchestrator.scheduler import Rejected, check_targets

router = APIRouter(tags=["schedules"])

# Which request field a schedule error concerns.
_ERROR_FIELDS = {
    "invalid_time_zone": "time_zone",
    "invalid_cron": "cron",
    "invalid_interval": "interval_seconds",
    "no_recurrence": "interval_seconds",
}


def _recurrence(body: ScheduleIn) -> Recurrence:
    return Recurrence(
        interval_seconds=body.interval_seconds, cron=body.cron, time_zone=body.time_zone
    )


def _check_recurrence(body: ScheduleIn, settings: Settings, now: datetime) -> datetime:
    """The first run after ``now``, or 422 if the recurrence is not valid or too frequent."""
    recurrence = _recurrence(body)
    try:
        check(recurrence, now, settings.min_schedule_interval_seconds)
        return next_run(recurrence, now)
    except ScheduleError as error:
        default = "cron" if body.cron is not None else "interval_seconds"
        field = _ERROR_FIELDS.get(error.code, default)
        raise ApiError(
            422,
            "validation_error",
            "The request is not valid.",
            [ErrorDetail(field=field, code=error.code, message=error.message)],
        ) from error


async def _check_scope(
    db: AsyncSession,
    request: Request,
    settings: Settings,
    project: Project,
    body: ScheduleIn,
    actor: str,
    schedule_id: int | None,
) -> None:
    """The scheduler's own scope check, at save time. A rejection is audited."""
    try:
        check_targets(
            body.targets,
            await current_scope(db, project),
            request.app.state.never_scan,
            settings.allow_non_public_targets,
        )
    except Rejected as rejected:
        if not rejected.details:
            raise ApiError(
                422,
                "too_many_targets",
                "A scan probes at most 256 addresses; a /24 is exactly that many.",
            ) from rejected
        # Committed before the error is raised: the rejected attempt is the action.
        await record_audit(
            db,
            actor=actor,
            action="schedule.rejected",
            object_type="project",
            object_id=project.id,
            details={"point": "api", "schedule_id": schedule_id, "rejected": rejected.details},
        )
        await db.commit()
        index = {text: position for position, text in enumerate(body.targets)}
        raise ApiError(
            422,
            "targets_rejected",
            "The schedule was not saved.",
            [
                ErrorDetail(
                    field=f"targets.{index[item['target']]}",
                    code=item["reason"],
                    message=f"{item['target']!r} cannot be scheduled: {item['reason']}.",
                )
                for item in rejected.details
            ],
        ) from rejected


async def _validated(
    db: AsyncSession,
    request: Request,
    settings: Settings,
    project: Project,
    body: ScheduleIn,
    actor: str,
    schedule_id: int | None = None,
) -> datetime:
    """Every check of a schedule that is about to be saved. Returns its next run."""
    if project.state is not ProjectState.ACTIVE:
        raise ApiError(409, "project_archived", "The project is archived and read-only.")
    check_ceiling(body.parameters, settings, "parameters.pps")
    next_run_at = _check_recurrence(body, settings, datetime.now(UTC))
    await _check_scope(db, request, settings, project, body, actor, schedule_id)
    return next_run_at


def _stored(body: ScheduleIn) -> dict[str, Any]:
    """The columns of a schedule, as the request gives them."""
    return {
        "name": body.name,
        "targets": list(body.targets),
        "parameters": body.parameters.model_dump(mode="json"),
        "interval_seconds": body.interval_seconds,
        "cron": body.cron,
        "time_zone": body.time_zone,
        "enabled": body.enabled,
    }


async def _out(db: AsyncSession, schedule: Schedule) -> ScheduleOut:
    last = await db.execute(
        select(ScheduleRun, Scan.state)
        .outerjoin(Scan, Scan.id == ScheduleRun.scan_id)
        .where(ScheduleRun.schedule_id == schedule.id)
        .order_by(ScheduleRun.id.desc())
        .limit(1)
    )
    row = last.first()
    return ScheduleOut(
        id=schedule.id,
        project_id=schedule.project_id,
        name=schedule.name,
        targets=list(schedule.targets),
        parameters=ScanSettings.model_validate(schedule.parameters),
        interval_seconds=schedule.interval_seconds,
        cron=schedule.cron,
        time_zone=schedule.time_zone,
        enabled=schedule.enabled,
        flag_reason=schedule.flag_reason,
        next_run_at=schedule.next_run_at,
        last_run=_run_out(row[0], row[1]) if row is not None else None,
        created_at=schedule.created_at,
    )


def _run_out(run: ScheduleRun, state: Any) -> ScheduleRunOut:
    return ScheduleRunOut(
        planned_at=run.planned_at,
        outcome=run.outcome,
        reason=run.reason,
        scan_id=run.scan_id,
        scan_state=state.value if state is not None else None,
        created_at=run.created_at,
    )


async def _schedule(db: AsyncSession, schedule_id: int, *, for_update: bool = False) -> Schedule:
    """A schedule, or 404. A schedule of a deleted project does not exist."""
    query = (
        select(Schedule)
        .join(Project, Project.id == Schedule.project_id)
        .where(Schedule.id == schedule_id, Project.deleted_at.is_(None))
    )
    schedule = await db.scalar(query.with_for_update(of=Schedule) if for_update else query)
    if schedule is None:
        raise ApiError(404, "not_found", "The schedule does not exist.")
    return schedule


def _writable(project: Project) -> None:
    if project.state is not ProjectState.ACTIVE:
        raise ApiError(409, "project_archived", "The project is archived and read-only.")


@router.get("/projects/{project_id}/schedules")
async def list_schedules(project_id: int, db: DbDep, page: PageParamsDep) -> Page[ScheduleOut]:
    project = await _project(db, project_id)
    of_project = Schedule.project_id == project.id
    total = await db.scalar(select(func.count()).select_from(Schedule).where(of_project))
    schedules = await db.scalars(
        select(Schedule)
        .where(of_project)
        .order_by(Schedule.id)
        .limit(page.limit)
        .offset(page.offset)
    )
    return Page(
        items=[await _out(db, schedule) for schedule in schedules],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/projects/{project_id}/schedules", status_code=201)
async def create_schedule(
    project_id: int,
    body: ScheduleIn,
    request: Request,
    current: CurrentSessionDep,
    db: DbDep,
    settings: SettingsDep,
) -> ScheduleOut:
    project = await _project(db, project_id)
    actor = current.account.username
    next_run_at = await _validated(db, request, settings, project, body, actor)
    schedule = Schedule(project_id=project.id, next_run_at=next_run_at, **_stored(body))
    db.add(schedule)
    await db.flush()
    await db.refresh(schedule)
    await record_audit(
        db,
        actor=actor,
        action="schedule.create",
        object_type="schedule",
        object_id=schedule.id,
        details={"project_id": project.id, **_stored(body)},
    )
    result = await _out(db, schedule)
    await db.commit()
    return result


@router.get("/schedules/{schedule_id}")
async def get_schedule(schedule_id: int, db: DbDep) -> ScheduleOut:
    return await _out(db, await _schedule(db, schedule_id))


@router.get("/schedules/{schedule_id}/runs")
async def list_runs(schedule_id: int, db: DbDep, page: PageParamsDep) -> Page[ScheduleRunOut]:
    """The runs of a schedule, newest first: started, skipped and rejected."""
    schedule = await _schedule(db, schedule_id)
    of_schedule = ScheduleRun.schedule_id == schedule.id
    total = await db.scalar(select(func.count()).select_from(ScheduleRun).where(of_schedule))
    rows = await db.execute(
        select(ScheduleRun, Scan.state)
        .outerjoin(Scan, Scan.id == ScheduleRun.scan_id)
        .where(of_schedule)
        .order_by(ScheduleRun.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return Page(
        items=[_run_out(run, state) for run, state in rows],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )


@router.put("/schedules/{schedule_id}")
async def update_schedule(
    schedule_id: int,
    body: ScheduleIn,
    request: Request,
    current: CurrentSessionDep,
    db: DbDep,
    settings: SettingsDep,
) -> ScheduleOut:
    schedule = await _schedule(db, schedule_id, for_update=True)
    project = await db.get_one(Project, schedule.project_id)
    actor = current.account.username
    next_run_at = await _validated(db, request, settings, project, body, actor, schedule.id)
    before = {name: getattr(schedule, name) for name in _stored(body)}
    for name, value in _stored(body).items():
        setattr(schedule, name, value)
    schedule.next_run_at = next_run_at
    flag_cleared = schedule.flag_reason
    schedule.flag_reason = None
    await record_audit(
        db,
        actor=actor,
        action="schedule.update",
        object_type="schedule",
        object_id=schedule.id,
        details={
            "project_id": project.id,
            "changed": {
                name: {"before": before[name], "after": value}
                for name, value in _stored(body).items()
                if before[name] != value
            },
            "flag_cleared": flag_cleared,
        },
    )
    await db.flush()
    result = await _out(db, schedule)
    await db.commit()
    return result


async def _set_enabled(
    schedule_id: int, enabled: bool, current: CurrentSessionDep, db: AsyncSession
) -> ScheduleOut:
    schedule = await _schedule(db, schedule_id, for_update=True)
    project = await db.get_one(Project, schedule.project_id)
    _writable(project)
    if schedule.enabled != enabled:
        schedule.enabled = enabled
        if enabled:
            # The time it was off is not caught up: the next run is counted from now.
            recurrence = Recurrence(schedule.interval_seconds, schedule.cron, schedule.time_zone)
            schedule.next_run_at = next_run(recurrence, datetime.now(UTC))
        await record_audit(
            db,
            actor=current.account.username,
            action="schedule.enable" if enabled else "schedule.disable",
            object_type="schedule",
            object_id=schedule.id,
            details={"project_id": project.id},
        )
        await db.flush()
    result = await _out(db, schedule)
    await db.commit()
    return result


@router.post("/schedules/{schedule_id}/enable")
async def enable_schedule(schedule_id: int, current: CurrentSessionDep, db: DbDep) -> ScheduleOut:
    return await _set_enabled(schedule_id, True, current, db)


@router.post("/schedules/{schedule_id}/disable")
async def disable_schedule(schedule_id: int, current: CurrentSessionDep, db: DbDep) -> ScheduleOut:
    return await _set_enabled(schedule_id, False, current, db)


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: int, current: CurrentSessionDep, db: DbDep) -> None:
    """The schedule and its run records go; the scans it started stay, as manual scans do."""
    schedule = await _schedule(db, schedule_id, for_update=True)
    project = await db.get_one(Project, schedule.project_id)
    _writable(project)
    await record_audit(
        db,
        actor=current.account.username,
        action="schedule.delete",
        object_type="schedule",
        object_id=schedule.id,
        details={"project_id": project.id, "name": schedule.name},
    )
    await db.delete(schedule)
    await db.commit()
