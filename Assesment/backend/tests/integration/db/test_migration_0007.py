"""Migration 0007: a scan records that ICMP discovery was unavailable (TASK-079)."""

import asyncio

from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.conftest import run_alembic

COLUMNS = (
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_name = 'scan' AND column_name = 'icmp_discovery_unavailable'"
)


async def test_existing_scans_get_false_and_the_column_reverts(scratch_database: str) -> None:
    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0006")
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
            ):
                await connection.execute(text(statement))

        await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0007")
        async with engine.connect() as connection:
            kept = await connection.scalar(text("SELECT icmp_discovery_unavailable FROM scan"))

        await asyncio.to_thread(run_alembic, scratch_database, "downgrade", "0006")
        async with engine.connect() as connection:
            after_downgrade = list(await connection.scalars(text(COLUMNS)))
    finally:
        await engine.dispose()

    assert kept is False
    assert after_downgrade == []
