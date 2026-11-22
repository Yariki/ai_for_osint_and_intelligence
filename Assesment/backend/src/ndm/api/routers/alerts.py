"""Alerts: the list, the read state, and the unread count for the indicator on
every page (FR-HIS-7, FR-HIS-8). Alerts of a deleted project are not shown."""

from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import ColumnElement, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.api.deps import DbDep
from ndm.api.errors import ApiError
from ndm.api.pagination import Page, PageParamsDep
from ndm.api.schemas.alerts import AlertOut, MarkedOut, UnreadOut
from ndm.db.models import Alert, Project

router = APIRouter(tags=["alerts"])

# An alert is shown unless its project has been deleted.
VISIBLE: ColumnElement[bool] = or_(
    Alert.project_id.is_(None),
    Alert.project_id.in_(select(Project.id).where(Project.deleted_at.is_(None))),
)


def _out(alert: Alert, project_name: str | None) -> AlertOut:
    return AlertOut(
        id=alert.id,
        project_id=alert.project_id,
        project_name=project_name,
        type=alert.type,
        summary=alert.summary,
        link=alert.link,
        created_at=alert.created_at,
        read_at=alert.read_at,
    )


async def _alert(db: AsyncSession, alert_id: int) -> Alert:
    alert = await db.scalar(select(Alert).where(Alert.id == alert_id, VISIBLE))
    if alert is None:
        raise ApiError(404, "not_found", "The alert does not exist.")
    return alert


@router.get("/alerts")
async def list_alerts(
    db: DbDep, page: PageParamsDep, unread: bool = False, project_id: int | None = None
) -> Page[AlertOut]:
    """Newest first. ``unread=true`` gives the unread ones only."""
    conditions = [VISIBLE]
    if unread:
        conditions.append(Alert.read_at.is_(None))
    if project_id is not None:
        conditions.append(Alert.project_id == project_id)
    total = await db.scalar(select(func.count()).select_from(Alert).where(*conditions))
    rows = await db.execute(
        select(Alert, Project.name)
        .outerjoin(Project, Project.id == Alert.project_id)
        .where(*conditions)
        .order_by(Alert.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return Page(
        items=[_out(alert, name) for alert, name in rows],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/alerts/unread-count")
async def unread_count(db: DbDep) -> UnreadOut:
    count = await db.scalar(
        select(func.count()).select_from(Alert).where(VISIBLE, Alert.read_at.is_(None))
    )
    return UnreadOut(unread=count or 0)


@router.post("/alerts/{alert_id}/read")
async def mark_read(alert_id: int, db: DbDep) -> AlertOut:
    """Mark one alert read. Marking it again keeps the first time."""
    alert = await _alert(db, alert_id)
    if alert.read_at is None:
        alert.read_at = datetime.now(UTC)
    project = await db.get(Project, alert.project_id) if alert.project_id else None
    await db.commit()
    return _out(alert, project.name if project else None)


@router.post("/alerts/read-all")
async def mark_all_read(db: DbDep) -> MarkedOut:
    result = await db.execute(
        update(Alert)
        .where(VISIBLE, Alert.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
        .returning(Alert.id)
    )
    marked = len(result.all())
    await db.commit()
    return MarkedOut(marked=marked)
