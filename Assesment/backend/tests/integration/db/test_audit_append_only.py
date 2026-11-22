"""Check 1A.6, database half: the audit log is append-only (FR-AUD-3)."""

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ndm.db.audit import SYSTEM_ACTOR, record_audit
from ndm.db.models import AuditEvent

CHANGES = [
    "UPDATE audit_event SET action = 'tampered'",
    "DELETE FROM audit_event",
    "TRUNCATE audit_event",
]


async def _add_entry(engine: AsyncEngine) -> None:
    async with async_sessionmaker(engine)() as session:
        await record_audit(session, actor="analyst", action="login", object_type="account")
        await session.commit()


async def _count(engine: AsyncEngine) -> int:
    async with engine.connect() as connection:
        return await connection.scalar(select(func.count()).select_from(AuditEvent)) or 0


# --- First barrier: privileges of the application role ---


@pytest.mark.parametrize("statement", CHANGES)
async def test_application_role_cannot_change_the_audit_log(
    app_engine: AsyncEngine, statement: str
) -> None:
    await _add_entry(app_engine)

    with pytest.raises(DBAPIError, match="permission denied for table audit_event"):
        async with app_engine.begin() as connection:
            await connection.execute(text(statement))

    assert await _count(app_engine) == 1


# --- Second barrier: triggers, for every role ---


@pytest.mark.parametrize("statement", CHANGES)
async def test_trigger_rejects_changes_even_for_the_owner(
    app_engine: AsyncEngine, owner_engine: AsyncEngine, statement: str
) -> None:
    await _add_entry(app_engine)

    with pytest.raises(DBAPIError, match="audit_event is append-only"):
        async with owner_engine.begin() as connection:
            await connection.execute(text(statement))

    assert await _count(app_engine) == 1


# --- The audit writer ---


async def test_entry_is_written_with_its_fields(db_session: AsyncSession) -> None:
    await record_audit(
        db_session,
        actor="analyst",
        action="project.create",
        object_type="project",
        object_id=42,
        details={"name": "Edge routers"},
    )
    await record_audit(db_session, actor=SYSTEM_ACTOR, action="startup", object_type="system")
    await db_session.commit()

    first, second = (await db_session.scalars(select(AuditEvent).order_by(AuditEvent.id))).all()
    assert (first.actor, first.action, first.object_type, first.object_id) == (
        "analyst",
        "project.create",
        "project",
        "42",
    )
    assert first.details == {"name": "Edge routers"}
    assert first.occurred_at.utcoffset() is not None
    assert (second.actor, second.object_id, second.details) == ("system", None, {})


async def test_writer_does_not_commit(app_engine: AsyncEngine, db_session: AsyncSession) -> None:
    await record_audit(db_session, actor="analyst", action="login", object_type="account")

    # Visible inside the caller's transaction, and to nobody else yet.
    assert await db_session.scalar(select(func.count()).select_from(AuditEvent)) == 1
    assert await _count(app_engine) == 0


async def test_rolled_back_transaction_leaves_no_audit_entry(
    app_engine: AsyncEngine, db_session: AsyncSession
) -> None:
    await db_session.execute(text("INSERT INTO project (name) VALUES ('doomed')"))
    await record_audit(
        db_session, actor="analyst", action="project.create", object_type="project", object_id=1
    )
    await db_session.rollback()

    assert await _count(app_engine) == 0
    async with app_engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM project")) == 0
