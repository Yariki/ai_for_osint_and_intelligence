"""Check 5.6: after a scan and a project are deleted and the purge has run,
no row of theirs remains in any table except `host` and `audit_event`.

Every table is filled first, by the seeding tool plus the rows of work in
progress it leaves out (a chunk, a session). What belongs to the deleted scan
and project is found from the schema itself, by following the foreign keys
from the scan and the project: a table added later is covered without
changing this test. A reference that is set to null when its target goes
(ON DELETE SET NULL) makes no ownership: the row belongs to someone else.
"""

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Table, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ndm.db.models import Base, Project, Scan
from ndm.orchestrator.purge import NOT_PURGED, PROJECT_DATA, SCAN_DATA, purge

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "seed_envelope.py"
_spec = importlib.util.spec_from_file_location("seed_envelope_for_deletion", SCRIPT)
assert _spec is not None and _spec.loader is not None
seed_envelope: Any = importlib.util.module_from_spec(_spec)
sys.modules["seed_envelope_for_deletion"] = seed_envelope
_spec.loader.exec_module(seed_envelope)

KEPT = {"host", "audit_event"}
Owned = dict[str, set[tuple[Any, ...]]]


def pk(table: Table) -> list[Any]:
    return list(table.primary_key.columns)


async def owned_rows(db: AsyncSession, scans: set[int], project: int) -> Owned:
    """The primary key of every row that belongs to the scans or the project."""
    owned: Owned = {"scan": {(scan,) for scan in scans}, "project": {(project,)}}
    # Parents before children, so that ownership flows down the foreign keys.
    for table in Base.metadata.sorted_tables:
        if table.name in KEPT:
            continue
        for fk in table.foreign_keys:
            parent = fk.column.table
            if fk.ondelete == "SET NULL" or not owned.get(parent.name):
                continue
            assert [column.name for column in pk(parent)] == [fk.column.name], (
                f"{table.name}.{fk.parent.name} refers to a column that is not the key"
            )
            values = [key[0] for key in owned[parent.name]]
            rows = await db.execute(select(*pk(table)).where(fk.parent.in_(values)))
            owned.setdefault(table.name, set()).update(tuple(row) for row in rows)
    return owned


async def remaining(db: AsyncSession, owned: Owned) -> dict[str, int]:
    """Rows still there that belong to what was deleted: the scans and the
    project themselves, and every row that refers to an owned row by a
    foreign key that confers ownership (the same rule as owned_rows)."""
    left: dict[str, int] = {}
    for table in Base.metadata.sorted_tables:
        if table.name in KEPT:
            continue
        conditions = []
        if table.name in ("scan", "project"):
            conditions.append(pk(table)[0].in_([key[0] for key in owned[table.name]]))
        for fk in table.foreign_keys:
            parent = fk.column.table.name
            if fk.ondelete != "SET NULL" and owned.get(parent):
                conditions.append(fk.parent.in_([key[0] for key in owned[parent]]))
        for condition in conditions:
            found = await db.scalar(select(func.count()).select_from(table).where(condition))
            if found:
                left[table.name] = left.get(table.name, 0) + found
    return left


async def counts(db: AsyncSession) -> dict[str, int]:
    found = {}
    for name in Base.metadata.tables:
        found[name] = await db.scalar(text(f"SELECT count(*) FROM {name}")) or 0
    return found


async def test_deleting_a_scan_and_a_project_leaves_nothing_of_theirs(
    owner_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    # 20 scans per project over 16 networks: each project's scheduled network is
    # scanned twice, so both have a stored diff and an alert.
    await seed_envelope.seed(owner_engine, seed_envelope.Plan(projects=2, scans=40))
    sessions = async_sessionmaker(app_engine, expire_on_commit=False)
    async with sessions() as db:
        first, second = list(await db.scalars(select(Project.id).order_by(Project.id)))
        # A scheduled scan of the first project, with stored diffs, runs and alerts.
        scan = await db.scalar(
            select(Scan.id).where(Scan.project_id == first, Scan.schedule_id.is_not(None))
            .order_by(Scan.id.desc()).limit(1)
        )  # fmt: skip
        assert scan is not None
        # The rows of work in progress that the seeding tool leaves out.
        await db.execute(
            text(
                "INSERT INTO scan_chunk (scan_id, chunk_seq, phase, spec, state) "
                "VALUES (:scan, 0, 'port_scan', '{}', 'done')"
            ),
            {"scan": scan},
        )
        now = datetime.now(UTC)
        await db.execute(
            text(
                "INSERT INTO session (token_hash, account_id, last_seen_at, expires_at) "
                "VALUES ('\\x01', (SELECT min(id) FROM account), :now, :expires)"
            ),
            {"now": now, "expires": now + timedelta(hours=1)},
        )
        await db.commit()

        before = await counts(db)
        empty = sorted(name for name, rows in before.items() if rows == 0)
        assert empty == ["alembic_version"] or empty == [], f"tables left empty: {empty}"

        project_scans = set(await db.scalars(select(Scan.id).where(Scan.project_id == second)))
        owned = await owned_rows(db, {scan} | project_scans, second)
        # The test reaches every table the purge knows.
        reached = {name for name, keys in owned.items() if keys}
        unreached = set(SCAN_DATA) | set(PROJECT_DATA) - {"scan_chunk"}
        assert unreached - reached == set(), unreached - reached
        assert not reached & set(NOT_PURGED), reached & set(NOT_PURGED)

        # The check finds them all before the purge: it cannot pass by looking nowhere.
        assert set(await remaining(db, owned)) == reached

        # Deleted as the API deletes them: marked, then purged in the background.
        await db.execute(update(Scan).where(Scan.id == scan).values(deleted_at=now))
        await db.execute(update(Project).where(Project.id == second).values(deleted_at=now))
        await db.commit()
        audit_before = before["audit_event"]

    removed = await purge(sessions)
    assert removed == {"scans": 1 + len(project_scans), "projects": 1}

    async with sessions() as db:
        assert await remaining(db, owned) == {}
        after = await counts(db)
    # What is kept is kept whole.
    assert after["audit_event"] == audit_before
    assert after["host"] == before["host"]
    # The other project and the rest of its data are still there.
    for name in ("project", "scan", "host_observation", "port_observation", "schedule"):
        assert after[name] > 0, name
