"""Migration 0006: the copy of the latest observation on project_host (TASK-078)."""

import asyncio

from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.conftest import run_alembic

COPY = {
    "reachable",
    "open_ports",
    "closed_count",
    "filtered_count",
    "ptr_names",
    "asn",
    "as_holder",
    "network_name",
    "organization",
    "rir",
    "registration_country",
    "country",
    "city",
    "location",
    "accuracy_radius_km",
    "device_type",
    "sub_role",
    "confidence",
}


async def _columns(url: str) -> set[str]:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            return set(
                await connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'project_host'"
                    )
                )
            )
    finally:
        await engine.dispose()


async def test_migration_applies_reverts_and_applies_again(scratch_database: str) -> None:
    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0005")
    columns = await _columns(scratch_database)

    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0006")
    assert await _columns(scratch_database) == columns | COPY

    await asyncio.to_thread(run_alembic, scratch_database, "downgrade", "0005")
    assert await _columns(scratch_database) == columns

    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0006")
    assert await _columns(scratch_database) == columns | COPY


async def test_existing_observations_are_copied(scratch_database: str) -> None:
    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0005")
    engine = create_async_engine(scratch_database, poolclass=pool.NullPool)
    try:
        async with engine.begin() as connection:
            for statement in (
                "INSERT INTO account (username, password_hash) VALUES ('analyst', 'hash')",
                "INSERT INTO project (name) VALUES ('Edge routers')",
                "INSERT INTO attestation (project_id, account_id) VALUES (1, 1)",
                "INSERT INTO scan (project_id, attestation_id, state, targets, probed_ports, "
                "parameters, technique_requested, seed) VALUES (1, 1, 'completed', "
                "ARRAY['8.8.8.8'::inet], '{[53,54),[443,444)}', '{}', 'automatic', 7)",
                "INSERT INTO host_observation (scan_id, address, reachable, closed_count, "
                "filtered_count, ptr_names) VALUES (1, '8.8.8.8', true, 3, 4, "
                "ARRAY['dns.example.net'])",
                "INSERT INTO port_observation (scan_id, address, port) "
                "VALUES (1, '8.8.8.8', 443), (1, '8.8.8.8', 53)",
                "INSERT INTO enrichment_snapshot (scan_id, address, asn, organization, country, "
                "location, accuracy_radius_km) VALUES (1, '8.8.8.8', 15169, 'Example DNS', 'US', "
                "ST_GeogFromText('POINT(-122 37)'), 100)",
                # An earlier run: not the latest, so not copied.
                "INSERT INTO classification_result (scan_id, address, run, latest, device_type, "
                "confidence, rules, ruleset_version) "
                "VALUES (1, '8.8.8.8', 1, false, 'unknown', 0.1, '[]', '1'), "
                "(1, '8.8.8.8', 2, true, 'dns_server', 0.9, '[]', '1')",
                "INSERT INTO project_host (project_id, address, latest_scan_id) "
                "VALUES (1, '8.8.8.8', 1), (1, '8.8.4.4', NULL)",
            ):
                await connection.execute(text(statement))

        await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0006")

        async with engine.connect() as connection:
            copied = (
                await connection.execute(
                    text(
                        "SELECT reachable, open_ports, closed_count, filtered_count, ptr_names, "
                        "asn, organization, country, ST_AsText(location), accuracy_radius_km, "
                        "device_type, confidence FROM project_host WHERE address = '8.8.8.8'"
                    )
                )
            ).one()
            without_scan = (
                await connection.execute(
                    text("SELECT reachable, open_ports FROM project_host WHERE address = '8.8.4.4'")
                )
            ).one()
    finally:
        await engine.dispose()

    assert tuple(copied) == (
        True,
        [53, 443],
        3,
        4,
        ["dns.example.net"],
        15169,
        "Example DNS",
        "US",
        "POINT(-122 37)",
        100,
        "dns_server",
        0.9,
    )
    # No latest scan: no copy, so the address is not in the project-wide view.
    assert tuple(without_scan) == (None, None)
