"""The audit log: read-only, filterable, newest first (FR-AUD-3)."""

from datetime import datetime

from fastapi import APIRouter
from sqlalchemy import func, select

from ndm.api.deps import DbDep
from ndm.api.pagination import Page, PageParamsDep
from ndm.api.schemas.audit import AuditEventOut
from ndm.db.models import AuditEvent

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
async def list_audit_events(
    db: DbDep,
    page: PageParamsDep,
    action: str | None = None,
    actor: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Page[AuditEventOut]:
    conditions = []
    if action is not None:
        conditions.append(AuditEvent.action == action)
    if actor is not None:
        conditions.append(AuditEvent.actor == actor)
    if object_type is not None:
        conditions.append(AuditEvent.object_type == object_type)
    if object_id is not None:
        conditions.append(AuditEvent.object_id == object_id)
    if since is not None:
        conditions.append(AuditEvent.occurred_at >= since)
    if until is not None:
        conditions.append(AuditEvent.occurred_at <= until)

    total = await db.scalar(select(func.count()).select_from(AuditEvent).where(*conditions))
    events = await db.scalars(
        select(AuditEvent)
        .where(*conditions)
        .order_by(AuditEvent.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return Page(
        items=[
            AuditEventOut(
                id=event.id,
                occurred_at=event.occurred_at,
                actor=event.actor,
                action=event.action,
                object_type=event.object_type,
                object_id=event.object_id,
                details=event.details,
            )
            for event in events
        ],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )
