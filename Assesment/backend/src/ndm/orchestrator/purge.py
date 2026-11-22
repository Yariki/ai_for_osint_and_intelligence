"""The deletion purge (I3).

Deleting a scan or a project hides it at once: the API sets ``deleted_at``.
This task removes the rows afterwards, in batches. ``host`` rows and audit
entries stay.

The purge works from the registry below. **A task that adds a table holding
scan or project data registers it here**; a test fails for a table that has a
foreign key to ``scan`` or ``project`` and is not registered, so a later phase
cannot forget one (delivery plan 2.2, item 5).
"""

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ndm.db.models import FINAL_SCAN_STATES, Project, Scan

log = structlog.get_logger(__name__)

# Table -> condition that selects the rows of one scan (:id). Deleted in this order.
_DIFFS = "SELECT id FROM scan_diff WHERE base_scan_id = :id OR target_scan_id = :id"
SCAN_DATA: dict[str, str] = {
    "change_event": f"diff_id IN ({_DIFFS})",
    "scan_diff": "base_scan_id = :id OR target_scan_id = :id",
    "classification_result": "scan_id = :id",
    "enrichment_snapshot": "scan_id = :id",
    "port_observation": "scan_id = :id",
    "host_observation": "scan_id = :id",
    "scan_chunk": "scan_id = :id",
}

# Table -> condition that selects the rows of one project (:id). Deleted in this
# order, after every scan of the project is gone.
_RUNS = "SELECT id FROM traceroute_run WHERE project_id = :id"
PROJECT_DATA: dict[str, str] = {
    "enrichment_snapshot": f"traceroute_run_id IN ({_RUNS})",
    "traceroute_probe": f"run_id IN ({_RUNS})",
    "traceroute_responder": f"run_id IN ({_RUNS})",
    "traceroute_run": "project_id = :id",
    "alert": "project_id = :id",
    "schedule_run": "schedule_id IN (SELECT id FROM schedule WHERE project_id = :id)",
    "schedule": "project_id = :id",
    "project_host": "project_id = :id",
    "scope_entry": "attestation_id IN (SELECT id FROM attestation WHERE project_id = :id)",
    "attestation": "project_id = :id",
}

# Tables the purge leaves alone, each for a reason.
NOT_PURGED: dict[str, str] = {
    "host": "the global registry of addresses; not owned by a scan or a project (I1)",
    "audit_event": "append-only; entries survive deletions (I3)",
    "account": "not scan or project data",
    "session": "not scan or project data",
    "vantage_point": "not scan or project data",
    "reference_dataset": "reference data; not owned by a scan or a project",
    "scan_profile": "parameter sets of the installation; not owned by a project",
    "rir_inetnum": "reference data; not owned by a scan or a project",
    "rir_delegation": "reference data; not owned by a scan or a project",
    "spatial_ref_sys": "PostGIS's own table",
    "alembic_version": "schema bookkeeping",
}

BATCH_ROWS = 10_000


async def _delete_in_batches(db: AsyncSession, table: str, condition: str, row_id: int) -> int:
    """Delete the selected rows a batch at a time, committing each batch, so
    that no long transaction holds up the tables a running scan writes to."""
    # tableoid is part of the row address because some tables are partitioned.
    statement = text(
        f"DELETE FROM {table} WHERE (tableoid, ctid) IN "
        f"(SELECT tableoid, ctid FROM {table} WHERE {condition} LIMIT :batch)"
    )
    total = 0
    while True:
        result = await db.execute(statement, {"id": row_id, "batch": BATCH_ROWS})
        await db.commit()
        deleted: int = result.rowcount  # type: ignore[attr-defined]
        total += deleted
        if deleted < BATCH_ROWS:
            return total


async def purge(sessions: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """Remove the rows of deleted scans and projects. Returns what was removed.

    A scan is purged when it, or its project, is marked deleted and it has
    reached a final state; a scan that is still running is stopped by the tick
    first. Safe to interrupt and to repeat: it starts from the database again.
    """
    removed = {"scans": 0, "projects": 0}
    async with sessions() as db:
        deleted_projects = select(Project.id).where(Project.deleted_at.is_not(None))
        scan_ids = list(
            await db.scalars(
                select(Scan.id)
                .where(
                    Scan.deleted_at.is_not(None) | Scan.project_id.in_(deleted_projects),
                    Scan.state.in_(FINAL_SCAN_STATES),
                )
                .order_by(Scan.id)
            )
        )
        for scan_id in scan_ids:
            for table, condition in SCAN_DATA.items():
                await _delete_in_batches(db, table, condition, scan_id)
            await db.execute(text("DELETE FROM scan WHERE id = :id"), {"id": scan_id})
            await db.commit()
            removed["scans"] += 1
            log.info("scan_purged", scan_id=scan_id)

        # A project goes when none of its scans is left.
        project_ids = list(
            await db.scalars(
                select(Project.id)
                .where(
                    Project.deleted_at.is_not(None),
                    ~select(Scan.id).where(Scan.project_id == Project.id).exists(),
                )
                .order_by(Project.id)
            )
        )
        for project_id in project_ids:
            for table, condition in PROJECT_DATA.items():
                await _delete_in_batches(db, table, condition, project_id)
            await db.execute(text("DELETE FROM project WHERE id = :id"), {"id": project_id})
            await db.commit()
            removed["projects"] += 1
            log.info("project_purged", project_id=project_id)
    return removed
