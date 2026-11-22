"""Migration 0002: the schema of the scan pipeline."""

import asyncio
from datetime import UTC, datetime
from ipaddress import IPv4Address

import pytest
from sqlalchemy import pool, select, text
from sqlalchemy.dialects.postgresql import Range, insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from ndm.db.models import (
    Account,
    Attestation,
    AuditEvent,
    Host,
    HostObservation,
    PortObservation,
    Project,
    ProjectHost,
    Scan,
    ScanChunk,
    ScanPhase,
    ScanState,
    VantagePoint,
)
from tests.integration.conftest import run_alembic

NEW_TABLES = {
    "vantage_point",
    "scan",
    "scan_chunk",
    "host",
    "project_host",
    "host_observation",
    "port_observation",
}


async def _scalars(url: str, query: str) -> list[object]:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            return list(await connection.scalars(text(query)))
    finally:
        await engine.dispose()


async def _tables(url: str) -> set[object]:
    # Partitions are tables too; they are told apart by pg_inherits below.
    return set(
        await _scalars(
            url,
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition",
        )
    )


async def _enums(url: str) -> set[object]:
    return set(await _scalars(url, "SELECT typname FROM pg_type WHERE typtype = 'e'"))


# --- Applies, reverts, applies again; earlier data is kept (NFR-OPS-4) ---


async def test_migration_applies_reverts_and_applies_again(scratch_database: str) -> None:
    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0001")
    before = await _tables(scratch_database)

    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0002")
    assert await _tables(scratch_database) == before | NEW_TABLES
    assert {"scan_state", "scan_phase", "chunk_state"} <= await _enums(scratch_database)

    await asyncio.to_thread(run_alembic, scratch_database, "downgrade", "0001")
    assert await _tables(scratch_database) == before
    assert not {"scan_state", "scan_phase", "chunk_state"} & await _enums(scratch_database)

    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0002")
    assert await _tables(scratch_database) == before | NEW_TABLES


async def test_database_left_by_phase_1a_migrates_forward_and_keeps_its_data(
    scratch_database: str,
) -> None:
    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0001")
    engine = create_async_engine(scratch_database, poolclass=pool.NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO account (username, password_hash) VALUES ('analyst', 'hash')")
            )
            await connection.execute(text("INSERT INTO project (name) VALUES ('Edge routers')"))
            await connection.execute(
                text("INSERT INTO attestation (project_id, account_id) VALUES (1, 1)")
            )
            await connection.execute(
                text(
                    "INSERT INTO scope_entry "
                    "(attestation_id, kind, original_text, start_address, end_address) "
                    "VALUES (1, 'cidr', '8.8.8.0/24', '8.8.8.0', '8.8.8.255')"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO audit_event (actor, action, object_type) "
                    "VALUES ('analyst', 'project.create', 'project')"
                )
            )

        await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "head")

        async with engine.connect() as connection:
            counts = {
                table: await connection.scalar(text(f"SELECT count(*) FROM {table}"))
                for table in ["account", "project", "attestation", "scope_entry", "audit_event"]
            }
            name = await connection.scalar(text("SELECT name FROM project"))
            # The protections on the audit log are as they were.
            with pytest.raises(DBAPIError, match="append-only"):
                await connection.execute(text("DELETE FROM audit_event"))
    finally:
        await engine.dispose()

    assert counts == dict.fromkeys(counts, 1)
    assert name == "Edge routers"


# --- Partitions and indexes (AD8) ---


@pytest.mark.parametrize("table", ["host_observation", "port_observation"])
async def test_observation_tables_have_16_hash_partitions(
    owner_engine: AsyncEngine, table: str
) -> None:
    async with owner_engine.connect() as connection:
        partitions = await connection.scalar(
            text(
                "SELECT count(*) FROM pg_inherits i JOIN pg_class p ON p.oid = i.inhparent "
                "WHERE p.relname = :table"
            ),
            {"table": table},
        )
        strategy = await connection.scalar(
            text(
                "SELECT partstrat FROM pg_partitioned_table t "
                "JOIN pg_class c ON c.oid = t.partrelid WHERE c.relname = :table"
            ),
            {"table": table},
        )

    assert partitions == 16
    assert strategy in ("h", b"h")


async def test_large_tables_have_only_the_indexes_the_design_names(
    owner_engine: AsyncEngine,
) -> None:
    async with owner_engine.connect() as connection:
        indexes = set(
            await connection.scalars(
                text(
                    "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
                    "AND tablename IN ('host_observation', 'port_observation')"
                )
            )
        )

    assert indexes == {
        "pk_host_observation",
        "pk_port_observation",
        "ix_host_observation_address_scan_id",
    }


# --- The application role can use the new tables ---


async def test_scan_data_round_trips_for_the_application_role(db_session: AsyncSession) -> None:
    account = Account(username="analyst", password_hash="hash")
    project = Project(name="Edge routers")
    vantage = VantagePoint(name="default", capabilities={"syn": True})
    db_session.add_all([account, project, vantage])
    await db_session.flush()
    attestation = Attestation(project_id=project.id, account_id=account.id)
    db_session.add(attestation)
    await db_session.flush()

    targets = [IPv4Address("8.8.8.1"), IPv4Address("8.8.8.2")]
    scan = Scan(
        project_id=project.id,
        attestation_id=attestation.id,
        vantage_point_id=vantage.id,
        targets=targets,
        probed_ports=[Range(22, 23), Range(80, 81), Range(8000, 8100)],
        parameters={"pps": 1000},
        technique_requested="automatic",
        seed=91823,
    )
    db_session.add(scan)
    await db_session.flush()
    db_session.add(
        ScanChunk(scan_id=scan.id, chunk_seq=0, phase=ScanPhase.DISCOVERY, spec={"targets": 2})
    )
    now = datetime.now(UTC)
    await db_session.execute(
        insert(Host),
        [
            {"address": a, "first_seen_at": now, "last_seen_at": now, "is_public": True}
            for a in targets
        ],
    )
    await db_session.execute(
        insert(ProjectHost),
        [
            {
                "project_id": project.id,
                "address": address,
                "first_seen_at": now,
                "last_seen_at": now,
                "latest_scan_id": scan.id,
            }
            for address in targets
        ],
    )
    await db_session.execute(
        insert(HostObservation), [{"scan_id": scan.id, "address": address} for address in targets]
    )
    await db_session.execute(
        insert(PortObservation),
        [{"scan_id": scan.id, "address": targets[0], "port": port} for port in (22, 80)],
    )
    await db_session.commit()

    stored = await db_session.get_one(Scan, scan.id)
    await db_session.refresh(stored)
    assert (stored.state, stored.phase, stored.partial) == (ScanState.QUEUED, None, False)
    assert stored.targets == targets
    assert list(stored.probed_ports) == [Range(22, 23), Range(80, 81), Range(8000, 8100)]
    observation = await db_session.get_one(HostObservation, (scan.id, targets[0]))
    assert (observation.reachable, observation.evidence, observation.closed_count) == (False, {}, 0)
    ports = await db_session.scalars(select(PortObservation.port).order_by(PortObservation.port))
    assert list(ports) == [22, 80]

    # "Was this port probed?" is a question the multirange answers directly (FR-HIS-4).
    probed = await db_session.scalars(
        text(
            "SELECT p FROM unnest(ARRAY[22, 23, 80, 8050, 8100]) AS p, scan WHERE probed_ports @> p"
        )
    )
    assert list(probed) == [22, 80, 8050]


async def test_port_outside_the_valid_range_is_refused(db_session: AsyncSession) -> None:
    with pytest.raises(DBAPIError, match="ck_port_observation_port_range|port_range"):
        await db_session.execute(
            text(
                "INSERT INTO port_observation (scan_id, address, port) VALUES (1, '8.8.8.8', 70000)"
            )
        )


async def test_audit_log_is_still_append_only_for_the_application_role(
    db_session: AsyncSession,
) -> None:
    db_session.add(AuditEvent(actor="analyst", action="login", object_type="account"))
    await db_session.commit()

    with pytest.raises(DBAPIError, match="permission denied"):
        await db_session.execute(text("DELETE FROM audit_event"))
