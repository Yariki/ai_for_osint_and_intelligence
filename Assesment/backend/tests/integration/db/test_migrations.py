"""Migrations apply, revert and apply again (delivery plan 2.2 item 3, NFR-OPS-4)."""

import asyncio
import re

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, pool, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ndm.db.models import Base
from tests.integration.conftest import run_alembic

FOUNDATION_TABLES = {
    "account",
    "session",
    "project",
    "attestation",
    "scope_entry",
    "audit_event",
}


async def _names(url: str, query: str) -> set[str]:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            return set(await connection.scalars(text(query)))
    finally:
        await engine.dispose()


async def _tables(url: str) -> set[str]:
    return await _names(url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")


async def _enums_and_functions(url: str) -> set[str]:
    return await _names(
        url,
        "SELECT typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
        "WHERE n.nspname = 'public' AND t.typtype = 'e' "
        "UNION SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public'",
    )


async def test_migrations_apply_revert_and_apply_again(scratch_database: str) -> None:
    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "head")
    tables = await _tables(scratch_database)
    types_and_functions = await _enums_and_functions(scratch_database)
    assert FOUNDATION_TABLES | {"alembic_version"} <= tables
    assert {"project_state", "scope_entry_kind", "audit_event_reject_change"} <= types_and_functions

    # Reverting every migration leaves nothing behind.
    await asyncio.to_thread(run_alembic, scratch_database, "downgrade", "base")
    assert await _tables(scratch_database) == {"alembic_version"}
    assert await _enums_and_functions(scratch_database) == set()

    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "head")
    assert await _tables(scratch_database) == tables
    assert await _enums_and_functions(scratch_database) == types_and_functions


async def test_first_migration_creates_the_foundation_tables(scratch_database: str) -> None:
    await asyncio.to_thread(run_alembic, scratch_database, "upgrade", "0001")

    assert await _tables(scratch_database) == FOUNDATION_TABLES | {"alembic_version"}


async def test_models_match_the_migrated_schema(owner_engine: AsyncEngine) -> None:
    def is_not_a_partition(
        _object: object, name: str | None, kind: str, reflected: bool, _compare_to: object
    ) -> bool:
        # Partitions exist in the database only; the models describe their parents.
        # spatial_ref_sys is PostGIS's own table.
        in_database_only = re.fullmatch(r".+_p\d\d|spatial_ref_sys", name or "")
        return not (kind == "table" and reflected and in_database_only)

    def differences(connection: Connection) -> list[object]:
        context = MigrationContext.configure(
            connection, opts={"include_object": is_not_a_partition}
        )
        return list(compare_metadata(context, Base.metadata))

    async with owner_engine.connect() as connection:
        assert await connection.run_sync(differences) == []


async def test_the_database_holds_only_the_extensions_the_product_uses(
    owner_engine: AsyncEngine,
) -> None:
    """Not the topology and geocoder extensions of the image's own init script."""
    async with owner_engine.connect() as connection:
        names = set((await connection.execute(text("SELECT extname FROM pg_extension"))).scalars())

    assert names == {"plpgsql", "postgis"}


async def test_application_role_can_use_the_tables(app_engine: AsyncEngine) -> None:
    """Identity columns work for the application role, which owns no sequence."""
    async with app_engine.begin() as connection:
        account_id = await connection.scalar(
            text("INSERT INTO account (username, password_hash) VALUES ('a', 'h') RETURNING id")
        )
        project_id = await connection.scalar(
            text("INSERT INTO project (name) VALUES ('p') RETURNING id")
        )
        attestation_id = await connection.scalar(
            text("INSERT INTO attestation (project_id, account_id) VALUES (:p, :a) RETURNING id"),
            {"p": project_id, "a": account_id},
        )
        await connection.execute(
            text("UPDATE project SET current_attestation_id = :a WHERE id = :p"),
            {"p": project_id, "a": attestation_id},
        )
        state = await connection.scalar(text("SELECT state FROM project"))

    assert (account_id, project_id, attestation_id) == (1, 1, 1)
    assert state == "active"
