"""Migration 0005: schedules, comparison and alerts."""

import asyncio
from datetime import UTC, datetime
from ipaddress import IPv4Address

import pytest
from sqlalchemy import pool, text
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from ndm.db.models import (
    Account,
    Alert,
    Attestation,
    ChangeEvent,
    Project,
    Scan,
    ScanDiff,
    ScanProfile,
    Schedule,
    ScheduleRun,
)
from tests.integration.conftest import run_alembic

NEW_TABLES = {"scan_profile", "schedule", "schedule_run", "scan_diff", "change_event", "alert"}


async def _scalars(url: str, query: str) -> set[object]:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            return set(await connection.scalars(text(query)))
    finally:
        await engine.dispose()


async def _tables(url: str) -> set[object]:
    return await _scalars(
        url,
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition "
        "AND c.relname <> 'spatial_ref_sys'",
    )


async def _scan_columns(url: str) -> set[object]:
    return await _scalars(
        url, "SELECT column_name FROM information_schema.columns WHERE table_name = 'scan'"
    )


async def test_migration_applies_reverts_and_applies_again(scratch_database: str) -> None:
    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0004")
    tables, columns = await _tables(scratch_database), await _scan_columns(scratch_database)

    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0005")
    assert await _tables(scratch_database) == tables | NEW_TABLES
    assert await _scan_columns(scratch_database) == columns | {"schedule_id"}

    await asyncio.to_thread(run_alembic, scratch_database, "downgrade", "0004")
    assert await _tables(scratch_database) == tables
    assert await _scan_columns(scratch_database) == columns

    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0005")
    assert await _tables(scratch_database) == tables | NEW_TABLES


async def test_database_left_by_phase_3_migrates_forward_and_keeps_its_data(
    scratch_database: str,
) -> None:
    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0004")
    engine = create_async_engine(scratch_database, poolclass=pool.NullPool)
    try:
        async with engine.begin() as connection:
            for statement in (
                "INSERT INTO account (username, password_hash) VALUES ('analyst', 'hash')",
                "INSERT INTO project (name) VALUES ('Edge routers')",
                "INSERT INTO attestation (project_id, account_id) VALUES (1, 1)",
                "INSERT INTO scan (project_id, attestation_id, state, targets, probed_ports, "
                "parameters, technique_requested, seed) VALUES (1, 1, 'completed', "
                "ARRAY['8.8.8.8'::inet], '{[80,81)}', '{}', 'automatic', 7)",
                "INSERT INTO classification_result (scan_id, address, device_type, confidence, "
                "rules, ruleset_version) VALUES (1, '8.8.8.8', 'router', 0.6, '[]', '1')",
            ):
                await connection.execute(text(statement))

        await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "head")

        async with engine.connect() as connection:
            scan = (await connection.execute(text("SELECT state, schedule_id FROM scan"))).one()
            kept = await connection.scalar(text("SELECT device_type FROM classification_result"))
    finally:
        await engine.dispose()

    assert tuple(scan) == ("completed", None)
    assert kept == "router"


@pytest.fixture
async def scan(db_session: AsyncSession) -> Scan:
    account = Account(username="analyst", password_hash="hash")
    project = Project(name="Edge routers")
    db_session.add_all([account, project])
    await db_session.flush()
    attestation = Attestation(project_id=project.id, account_id=account.id)
    db_session.add(attestation)
    await db_session.flush()
    created = Scan(
        project_id=project.id,
        attestation_id=attestation.id,
        targets=[IPv4Address("8.8.8.8")],
        probed_ports=[Range(80, 81)],
        parameters={},
        technique_requested="automatic",
        seed=1,
    )
    db_session.add(created)
    await db_session.commit()
    return created


def schedule(scan: Scan | int, **values: object) -> Schedule:
    """A schedule of the scan's project; or of the project with that id."""
    return Schedule(
        project_id=scan if isinstance(scan, int) else scan.project_id,
        name="Nightly",
        targets=["8.8.8.0/24"],
        parameters={"port_profile": "top-100"},
        next_run_at=datetime(2026, 11, 18, 2, tzinfo=UTC),
        **({"interval_seconds": 86400} | values),
    )


async def test_schedule_has_an_interval_or_a_cron_expression_and_never_both(
    db_session: AsyncSession, scan: Scan
) -> None:
    project = scan.project_id  # a rollback below expires the scan object
    db_session.add_all(
        [schedule(project), schedule(project, interval_seconds=None, cron="0 2 * * *")]
    )
    await db_session.commit()

    for values in ({"interval_seconds": None}, {"cron": "0 2 * * *"}):
        db_session.add(schedule(project, **values))
        with pytest.raises(IntegrityError, match="interval_or_cron"):
            await db_session.commit()
        await db_session.rollback()


async def test_schedule_runs_and_the_scans_they_started(
    db_session: AsyncSession, scan: Scan
) -> None:
    nightly = schedule(scan)
    db_session.add(nightly)
    await db_session.flush()
    scan.schedule_id = nightly.id
    db_session.add_all(
        [
            ScheduleRun(
                schedule_id=nightly.id,
                planned_at=nightly.next_run_at,
                outcome="started",
                scan_id=scan.id,
            ),
            ScheduleRun(
                schedule_id=nightly.id,
                planned_at=nightly.next_run_at,
                outcome="skipped",
                reason="previous run active",
            ),
        ]
    )
    await db_session.commit()
    await db_session.refresh(nightly)

    assert (nightly.enabled, nightly.time_zone, nightly.flag_reason, nightly.last_outcome) == (
        True,
        "UTC",
        None,
        None,
    )
    # Deleting the schedule removes its runs and leaves its scans, without the link.
    await db_session.delete(nightly)
    await db_session.commit()
    await db_session.refresh(scan)
    assert scan.schedule_id is None
    assert await db_session.scalar(text("SELECT count(*) FROM schedule_run")) == 0


async def test_diff_events_and_alert_round_trip(db_session: AsyncSession, scan: Scan) -> None:
    diff = ScanDiff(
        base_scan_id=scan.id,
        target_scan_id=scan.id,
        compared_ports=[Range(80, 81)],
        not_comparable=[IPv4Address("8.8.4.4")],
        summary={"port_opened": 1},
    )
    db_session.add(diff)
    await db_session.flush()
    db_session.add_all(
        [
            ChangeEvent(
                diff_id=diff.id, address=IPv4Address("8.8.8.8"), kind="port_opened", port=80
            ),
            ChangeEvent(
                diff_id=diff.id,
                address=IPv4Address("8.8.8.8"),
                kind="enrichment_changed",
                field="city",
                before="Mountain View",
                after="Sunnyvale",
                dataset_change=True,
            ),
            Alert(
                project_id=scan.project_id,
                type="changes_detected",
                summary="2 changes",
                link={"diff_id": diff.id},
            ),
        ]
    )
    await db_session.commit()
    db_session.expunge_all()

    stored = await db_session.get_one(ScanDiff, diff.id)
    events = (
        await db_session.execute(
            text("SELECT kind, port, before, after, dataset_change FROM change_event ORDER BY id")
        )
    ).all()
    alert = (await db_session.execute(text("SELECT type, link, read_at FROM alert"))).one()

    assert stored.not_comparable == [IPv4Address("8.8.4.4")]
    assert list(stored.compared_ports) == [Range(80, 81)]
    assert [tuple(e) for e in events] == [
        ("port_opened", 80, None, None, False),
        ("enrichment_changed", None, "Mountain View", "Sunnyvale", True),
    ]
    assert tuple(alert) == ("changes_detected", {"diff_id": diff.id}, None)


async def test_scan_profile_names_are_unique(db_session: AsyncSession) -> None:
    db_session.add(ScanProfile(name="Quick", parameters={"port_profile": "top-100"}))
    await db_session.commit()

    db_session.add(ScanProfile(name="Quick", parameters={}))
    with pytest.raises(IntegrityError, match="uq_scan_profile_name"):
        await db_session.commit()
    await db_session.rollback()
