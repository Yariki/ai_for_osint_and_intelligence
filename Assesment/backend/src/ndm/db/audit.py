"""The audit writer (FR-AUD-1, FR-AUD-2)."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import AuditEvent

SYSTEM_ACTOR = "system"
"""Actor of actions that no account started, such as scheduled runs."""


async def record_audit(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    object_type: str,
    object_id: int | str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Write one audit entry inside the caller's transaction.

    Nothing is committed here. The entry is committed with the action it
    records, and disappears with it if the transaction rolls back.
    """
    session.add(
        AuditEvent(
            actor=actor,
            action=action,
            object_type=object_type,
            object_id=None if object_id is None else str(object_id),
            details=details or {},
        )
    )
    await session.flush()
