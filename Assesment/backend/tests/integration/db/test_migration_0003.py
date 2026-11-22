"""Migration 0003: enrichment snapshots and reference datasets."""

import asyncio
from datetime import date
from ipaddress import IPv4Address

import pytest
from sqlalchemy import pool, select, text
from sqlalchemy.dialects.postgresql import Range, insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from ndm.db.models import (
    Account,
    Attestation,
    EnrichmentSnapshot,
    GeoPoint,
    HostObservation,
    Project,
    ReferenceDataset,
    RirDelegation,
    RirInetnum,
    Scan,
)
from tests.integration.conftest import run_alembic

NEW_TABLES = {"reference_dataset", "rir_inetnum", "rir_delegation", "enrichment_snapshot"}
NEW_COLUMNS = {
    "scan": {"dataset_versions", "failed_phase", "retry_requested_at", "enrichment_requested_at"},
    "host_observation": {"ptr_names", "ptr_status"},
}


async def _scalars(url: str, query: str) -> set[object]:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            return set(await connection.scalars(text(query)))
    finally:
        await engine.dispose()


async def _tables(url: str) -> set[object]:
    """The application's tables. PostGIS's own table and the partitions are left out."""
    return await _scalars(
        url,
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition "
        "AND c.relname <> 'spatial_ref_sys'",
    )


async def _columns(url: str, table: str) -> set[object]:
    return await _scalars(
        url,
        f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}'",
    )


async def _has_postgis(url: str) -> bool:
    return bool(await _scalars(url, "SELECT 1 FROM pg_extension WHERE extname = 'postgis'"))


# --- Applies, reverts, applies again; earlier data is kept (NFR-OPS-4) ---


async def test_migration_applies_reverts_and_applies_again(scratch_database: str) -> None:
    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0002")
    tables = await _tables(scratch_database)
    columns = {table: await _columns(scratch_database, table) for table in NEW_COLUMNS}

    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0003")
    assert await _tables(scratch_database) == tables | NEW_TABLES
    for table, added in NEW_COLUMNS.items():
        assert await _columns(scratch_database, table) == columns[table] | added
    assert await _has_postgis(scratch_database)

    await asyncio.to_thread(run_alembic, scratch_database, "downgrade", "0002")
    assert await _tables(scratch_database) == tables
    for table in NEW_COLUMNS:
        assert await _columns(scratch_database, table) == columns[table]
    # The migration installed PostGIS in this database, so it also removes it.
    assert not await _has_postgis(scratch_database)

    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0003")
    assert await _tables(scratch_database) == tables | NEW_TABLES


async def test_postgis_that_was_there_before_is_left_in_place(scratch_database: str) -> None:
    """The database image comes with PostGIS installed. Reverting must not remove it."""
    engine = create_async_engine(scratch_database, poolclass=pool.NullPool)
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION postgis"))
    await engine.dispose()

    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "head")
    await asyncio.to_thread(run_alembic, scratch_database, "downgrade", "0002")

    assert await _has_postgis(scratch_database)


async def test_database_left_by_phase_1b_migrates_forward_and_keeps_its_data(
    scratch_database: str,
) -> None:
    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0002")
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
                "INSERT INTO host_observation (scan_id, address, reachable, evidence) "
                "VALUES (1, '8.8.8.8', true, '{\"icmp\": {\"rtt_ms\": 4.2}}')",
                "INSERT INTO port_observation (scan_id, address, port) VALUES (1, '8.8.8.8', 80)",
            ):
                await connection.execute(text(statement))

        await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "head")

        async with engine.connect() as connection:
            scan = (
                await connection.execute(
                    text("SELECT state, seed, dataset_versions, failed_phase FROM scan")
                )
            ).one()
            observation = (
                await connection.execute(
                    text("SELECT reachable, evidence, ptr_names, ptr_status FROM host_observation")
                )
            ).one()
            ports = await connection.scalar(text("SELECT count(*) FROM port_observation"))
    finally:
        await engine.dispose()

    # The old rows are there, and the new columns have their defaults.
    assert tuple(scan) == ("completed", 7, {}, None)
    assert tuple(observation) == (True, {"icmp": {"rtt_ms": 4.2}}, [], None)
    assert ports == 1


# --- Indexes ---


async def test_registry_ranges_have_a_gist_index(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as connection:
        definitions = {
            name: definition
            for name, definition in await connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE tablename IN ('rir_inetnum', 'rir_delegation')"
                )
            )
        }

    assert "USING gist (ip_range)" in definitions["ix_rir_inetnum_ip_range"]
    assert "USING gist (ip_range)" in definitions["ix_rir_delegation_ip_range"]


async def test_snapshots_have_no_spatial_index(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as connection:
        indexes = set(
            await connection.scalars(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'enrichment_snapshot'")
            )
        )

    # (Migration 0004 adds the unique key of the snapshots of traceroute runs.)
    assert indexes - {"uq_enrichment_snapshot_traceroute_run_id"} == {
        "pk_enrichment_snapshot",
        "uq_enrichment_snapshot_run",
        "uq_enrichment_snapshot_latest",
    }


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


def snapshot(scan: Scan, **values: object) -> EnrichmentSnapshot:
    return EnrichmentSnapshot(scan_id=scan.id, address=IPv4Address("8.8.8.8"), **values)


async def test_snapshot_round_trips_with_its_location(db_session: AsyncSession, scan: Scan) -> None:
    db_session.add(
        snapshot(
            scan,
            asn=15169,
            as_holder="GOOGLE",
            network_name="GOGL",
            organization="Google LLC",
            organization_source="registry",
            rir="arin",
            registration_country="US",
            country="US",
            city="Mountain View",
            location=GeoPoint(latitude=37.4056, longitude=-122.0775),
            accuracy_radius_km=1000,
            dataset_versions={"geolite2-city": "2026-11-13"},
        )
    )
    db_session.add(
        EnrichmentSnapshot(
            scan_id=scan.id, address=IPv4Address("8.8.4.4"), unavailable=["country", "city"]
        )
    )
    await db_session.commit()
    db_session.expunge_all()

    located, unlocated = await db_session.scalars(
        select(EnrichmentSnapshot).order_by(EnrichmentSnapshot.id)
    )

    assert located.location == GeoPoint(latitude=37.4056, longitude=-122.0775)
    assert (located.run, located.latest, located.unavailable) == (1, True, [])
    assert (located.asn, located.organization_source, located.rir) == (15169, "registry", "arin")
    assert located.dataset_versions == {"geolite2-city": "2026-11-13"}
    assert unlocated.location is None
    assert unlocated.unavailable == ["country", "city"]
    # The column is a real PostGIS point: longitude is X, latitude is Y.
    x, y = (
        await db_session.execute(
            text(
                "SELECT ST_X(location::geometry), ST_Y(location::geometry) "
                "FROM enrichment_snapshot WHERE location IS NOT NULL"
            )
        )
    ).one()
    assert (x, y) == (-122.0775, 37.4056)


async def test_a_scan_has_one_latest_snapshot_per_address(
    db_session: AsyncSession, scan: Scan
) -> None:
    db_session.add(snapshot(scan, run=1))
    await db_session.commit()

    db_session.add(snapshot(scan, run=2))
    with pytest.raises(IntegrityError, match="uq_enrichment_snapshot_latest"):
        await db_session.commit()
    await db_session.rollback()


async def test_a_rerun_adds_a_snapshot_and_moves_the_latest_flag(
    db_session: AsyncSession, scan: Scan
) -> None:
    first = snapshot(scan, run=1, country="US")
    db_session.add(first)
    await db_session.commit()

    first.latest = False
    db_session.add(snapshot(scan, run=2, country="DE"))
    await db_session.commit()

    rows = await db_session.execute(
        select(EnrichmentSnapshot.run, EnrichmentSnapshot.latest, EnrichmentSnapshot.country)
        .where(EnrichmentSnapshot.scan_id == scan.id)
        .order_by(EnrichmentSnapshot.run)
    )
    assert [tuple(row) for row in rows] == [(1, False, "US"), (2, True, "DE")]


async def test_the_same_run_cannot_be_stored_twice(db_session: AsyncSession, scan: Scan) -> None:
    """What makes redelivered findings and a repeated phase harmless."""
    values = {"scan_id": scan.id, "address": IPv4Address("8.8.8.8"), "run": 1}
    await db_session.execute(insert(EnrichmentSnapshot).values(values))
    await db_session.commit()

    again = insert(EnrichmentSnapshot).values(values).on_conflict_do_nothing()
    assert (await db_session.execute(again)).rowcount == 0  # type: ignore[attr-defined]

    with pytest.raises(IntegrityError):
        await db_session.execute(insert(EnrichmentSnapshot).values({**values, "latest": False}))
    await db_session.rollback()


async def test_snapshot_needs_an_existing_scan(db_session: AsyncSession) -> None:
    db_session.add(EnrichmentSnapshot(scan_id=999, address=IPv4Address("8.8.8.8")))

    with pytest.raises(IntegrityError, match="fk_enrichment_snapshot_scan_id_scan"):
        await db_session.commit()
    await db_session.rollback()


async def test_the_most_specific_range_that_covers_an_address_is_found(
    db_session: AsyncSession,
) -> None:
    def span(first: str, last: str) -> Range[int]:
        return Range(int(IPv4Address(first)), int(IPv4Address(last)) + 1)

    db_session.add_all(
        [
            RirInetnum(rir="ripe", ip_range=span("193.0.0.0", "193.0.23.255"), network_name="WIDE"),
            RirInetnum(
                rir="ripe",
                ip_range=span("193.0.6.0", "193.0.6.255"),
                network_name="NARROW",
                organization="Example B.V.",
                country="NL",
            ),
            RirInetnum(rir="ripe", ip_range=span("194.0.0.0", "194.0.0.255"), network_name="OTHER"),
            RirDelegation(
                rir="ripencc",
                ip_range=span("193.0.0.0", "193.0.31.255"),
                country="NL",
                delegated_on=date(1993, 9, 1),
                status="allocated",
            ),
        ]
    )
    await db_session.commit()
    address = int(IPv4Address("193.0.6.139"))

    names = await db_session.scalars(
        select(RirInetnum.network_name)
        .where(RirInetnum.ip_range.contains(address))
        .order_by(text("upper(ip_range) - lower(ip_range)"))
    )
    delegation = (
        await db_session.execute(
            select(RirDelegation.rir, RirDelegation.country, RirDelegation.delegated_on).where(
                RirDelegation.ip_range.contains(address)
            )
        )
    ).one()

    assert list(names) == ["NARROW", "WIDE"]
    assert tuple(delegation) == ("ripencc", "NL", date(1993, 9, 1))


async def test_reference_dataset_round_trips(db_session: AsyncSession) -> None:
    db_session.add(ReferenceDataset(name="geolite2-city"))
    await db_session.commit()
    db_session.expunge_all()

    stored = await db_session.scalar(select(ReferenceDataset))

    assert stored is not None
    assert (stored.name, stored.status, stored.version) == ("geolite2-city", "missing", None)
    assert (stored.loaded_at, stored.checked_at, stored.update_requested_at) == (None, None, None)

    db_session.add(ReferenceDataset(name="geolite2-city"))
    with pytest.raises(IntegrityError, match="uq_reference_dataset_name"):
        await db_session.commit()
    await db_session.rollback()


async def test_reverse_dns_columns_of_an_observation(db_session: AsyncSession, scan: Scan) -> None:
    db_session.add(HostObservation(scan_id=scan.id, address=IPv4Address("8.8.8.8")))
    await db_session.commit()
    observation = await db_session.get_one(HostObservation, (scan.id, IPv4Address("8.8.8.8")))
    await db_session.refresh(observation)
    assert (observation.ptr_names, observation.ptr_status) == ([], None)

    observation.ptr_names = ["dns.google"]
    observation.ptr_status = "ok"
    await db_session.commit()
    await db_session.refresh(observation)

    assert (observation.ptr_names, observation.ptr_status) == (["dns.google"], "ok")


async def test_audit_log_is_still_append_only_for_the_application_role(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        await db_session.execute(text("DELETE FROM audit_event"))
