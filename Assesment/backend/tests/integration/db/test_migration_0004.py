"""Migration 0004: traceroute, classification and the analyst's annotations."""

import asyncio
from ipaddress import IPv4Address

import pytest
from sqlalchemy import pool, select, text
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from ndm.db.models import (
    Account,
    Attestation,
    ClassificationResult,
    EnrichmentSnapshot,
    Project,
    ProjectHost,
    Scan,
    TracerouteProbe,
    TracerouteResponder,
    TracerouteRun,
    TracerouteState,
)
from tests.integration.conftest import run_alembic

NEW_TABLES = {"traceroute_run", "traceroute_probe", "traceroute_responder", "classification_result"}
NEW_PROJECT_HOST_COLUMNS = {"type_override", "override_at", "tags", "notes"}


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


async def _columns(url: str, table: str) -> set[object]:
    return await _scalars(
        url, f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}'"
    )


# --- Applies, reverts, applies again; earlier data is kept (NFR-OPS-4) ---


async def test_migration_applies_reverts_and_applies_again(scratch_database: str) -> None:
    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0003")
    tables = await _tables(scratch_database)
    host_columns = await _columns(scratch_database, "project_host")
    snapshot_columns = await _columns(scratch_database, "enrichment_snapshot")
    scan_columns = await _columns(scratch_database, "scan")

    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0004")
    added = {"classification_requested_at"}
    assert await _columns(scratch_database, "scan") == scan_columns | added
    assert await _tables(scratch_database) == tables | NEW_TABLES
    assert (
        await _columns(scratch_database, "project_host") == host_columns | NEW_PROJECT_HOST_COLUMNS
    )
    assert await _columns(scratch_database, "enrichment_snapshot") == snapshot_columns | {
        "traceroute_run_id"
    }

    await asyncio.to_thread(run_alembic, scratch_database, "downgrade", "0003")
    assert await _tables(scratch_database) == tables
    assert await _columns(scratch_database, "scan") == scan_columns
    assert await _columns(scratch_database, "project_host") == host_columns
    assert await _columns(scratch_database, "enrichment_snapshot") == snapshot_columns
    assert not await _scalars(
        scratch_database, "SELECT 1 FROM pg_type WHERE typname = 'traceroute_state'"
    )

    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0004")
    assert await _tables(scratch_database) == tables | NEW_TABLES


async def test_database_left_by_phase_2_migrates_forward_and_keeps_its_data(
    scratch_database: str,
) -> None:
    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0003")
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
                "INSERT INTO project_host (project_id, address, latest_scan_id) "
                "VALUES (1, '8.8.8.8', 1)",
                "INSERT INTO enrichment_snapshot (scan_id, address, asn, country) "
                "VALUES (1, '8.8.8.8', 15169, 'US')",
            ):
                await connection.execute(text(statement))

        await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "head")

        async with engine.connect() as connection:
            host = (
                await connection.execute(
                    text("SELECT latest_scan_id, type_override, tags, notes FROM project_host")
                )
            ).one()
            snapshot = (
                await connection.execute(
                    text("SELECT scan_id, traceroute_run_id, asn, country FROM enrichment_snapshot")
                )
            ).one()
    finally:
        await engine.dispose()

    assert tuple(host) == (1, None, [], None)
    assert tuple(snapshot) == (1, None, 15169, "US")


async def test_reverting_drops_the_snapshots_of_traceroute_runs(scratch_database: str) -> None:
    """They cannot exist without their runs; the snapshots of scans are kept."""
    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "head")
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
                "INSERT INTO traceroute_run "
                "(project_id, attestation_id, target, method, parameters) "
                "VALUES (1, 1, '8.8.8.8', 'icmp', '{}')",
                "INSERT INTO enrichment_snapshot (scan_id, address) VALUES (1, '8.8.8.8')",
                "INSERT INTO enrichment_snapshot (traceroute_run_id, address) "
                "VALUES (1, '192.0.2.1')",
            ):
                await connection.execute(text(statement))

        await asyncio.to_thread(run_alembic, scratch_database, "downgrade", "0003")

        async with engine.connect() as connection:
            left = list(
                await connection.scalars(text("SELECT host(address) FROM enrichment_snapshot"))
            )
    finally:
        await engine.dispose()
    assert left == ["8.8.8.8"]


# --- The application role can use the new tables ---


@pytest.fixture
async def scan(db_session: AsyncSession) -> Scan:
    account = Account(username="analyst", password_hash="hash")
    project = Project(name="Edge routers")
    db_session.add_all([account, project])
    await db_session.flush()
    attestation = Attestation(project_id=project.id, account_id=account.id)
    db_session.add(attestation)
    await db_session.flush()
    scan = Scan(
        project_id=project.id,
        attestation_id=attestation.id,
        targets=[IPv4Address("8.8.8.8")],
        probed_ports=[Range(80, 81)],
        parameters={},
        technique_requested="automatic",
        seed=1,
    )
    db_session.add(scan)
    await db_session.commit()
    return scan


async def run_for(db: AsyncSession, scan: Scan) -> TracerouteRun:
    run = TracerouteRun(
        project_id=scan.project_id,
        attestation_id=scan.attestation_id,
        target=IPv4Address("8.8.8.8"),
        method="icmp",
        parameters={"max_hops": 30, "probes_per_hop": 3, "timeout_ms": 1000, "port": None},
    )
    db.add(run)
    await db.commit()
    return run


async def test_traceroute_run_with_its_probes_and_responders(
    db_session: AsyncSession, scan: Scan
) -> None:
    run = await run_for(db_session, scan)
    db_session.add_all(
        [
            TracerouteProbe(
                run_id=run.id, ttl=1, probe=0, responder=IPv4Address("10.0.0.1"), rtt_ms=0.8,
                reply_kind="time_exceeded",
            ),
            TracerouteProbe(run_id=run.id, ttl=2, probe=0),  # nobody answered
            TracerouteResponder(run_id=run.id, address=IPv4Address("10.0.0.1"), unlocatable=True),
        ]
    )  # fmt: skip
    await db_session.commit()
    db_session.expunge_all()

    stored = await db_session.get_one(TracerouteRun, run.id)
    probes = list(await db_session.scalars(select(TracerouteProbe).order_by(TracerouteProbe.ttl)))
    responder = await db_session.scalar(select(TracerouteResponder))

    assert (stored.state, stored.reached, stored.attempts) == (TracerouteState.QUEUED, None, 0)
    assert stored.created_at is not None
    assert [(p.ttl, str(p.responder), p.rtt_ms) for p in probes] == [
        (1, "10.0.0.1", 0.8),
        (2, "None", None),
    ]
    assert responder is not None
    assert (responder.unlocatable, responder.ptr_names, responder.ptr_status) == (True, [], None)


async def test_a_probe_is_stored_once(db_session: AsyncSession, scan: Scan) -> None:
    run = await run_for(db_session, scan)
    db_session.add(TracerouteProbe(run_id=run.id, ttl=1, probe=0))
    await db_session.commit()

    db_session.add(TracerouteProbe(run_id=run.id, ttl=1, probe=0))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_snapshot_belongs_to_a_scan_or_to_a_run_and_never_both(
    db_session: AsyncSession, scan: Scan
) -> None:
    run = await run_for(db_session, scan)
    # Kept apart: a rollback expires the objects of the session.
    run_id, scan_id = run.id, scan.id
    address = IPv4Address("192.0.2.1")
    db_session.add(EnrichmentSnapshot(traceroute_run_id=run_id, address=address, asn=64496))
    await db_session.commit()

    for owners in ({}, {"scan_id": scan_id, "traceroute_run_id": run_id}):
        db_session.add(EnrichmentSnapshot(address=address, **owners))
        with pytest.raises(IntegrityError, match="one_owner"):
            await db_session.commit()
        await db_session.rollback()

    # A hop is enriched once per run.
    db_session.add(EnrichmentSnapshot(traceroute_run_id=run_id, address=address))
    with pytest.raises(IntegrityError, match="uq_enrichment_snapshot_traceroute_run_id"):
        await db_session.commit()
    await db_session.rollback()


async def test_classification_has_one_latest_result_per_host(
    db_session: AsyncSession, scan: Scan
) -> None:
    def result(run: int) -> ClassificationResult:
        return ClassificationResult(
            scan_id=scan.id,
            address=IPv4Address("8.8.8.8"),
            run=run,
            device_type="dns_server",
            confidence=0.7,
            rules=[{"rule": "dns-port", "evidence": {"open_ports": [53]}}],
            ruleset_version="1",
        )

    db_session.add(result(1))
    await db_session.commit()
    db_session.add(result(2))
    with pytest.raises(IntegrityError, match="uq_classification_result_latest"):
        await db_session.commit()
    await db_session.rollback()

    stored = await db_session.scalar(select(ClassificationResult))
    assert stored is not None
    assert (stored.run, stored.latest, stored.sub_role) == (1, True, None)
    assert stored.rules == [{"rule": "dns-port", "evidence": {"open_ports": [53]}}]


async def test_override_tags_and_notes_on_the_host_of_a_project(
    db_session: AsyncSession, scan: Scan
) -> None:
    db_session.add(ProjectHost(project_id=scan.project_id, address=IPv4Address("8.8.8.8")))
    await db_session.commit()
    host = await db_session.get_one(ProjectHost, (scan.project_id, IPv4Address("8.8.8.8")))
    await db_session.refresh(host)
    assert (host.type_override, host.tags, host.notes) == (None, [], None)

    host.type_override = "router"
    host.tags = ["edge", "isp"]
    host.notes = "Upstream of the office."
    await db_session.commit()
    await db_session.refresh(host)

    assert (host.type_override, host.tags, host.notes) == (
        "router",
        ["edge", "isp"],
        "Upstream of the office.",
    )
