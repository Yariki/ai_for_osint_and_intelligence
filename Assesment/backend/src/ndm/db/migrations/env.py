"""Alembic environment.

Migrations run as the role that owns the schema, whose URL comes from
NDM_MIGRATION_DATABASE_URL. Only online mode is supported.
"""

import asyncio
import logging

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from ndm.core.logging import configure_logging
from ndm.core.settings import Settings
from ndm.db.models import Base

settings = Settings()

# Set up logging when run from the command line. Under a host that has already
# set up logging (the test runner), leave its handlers alone.
if not logging.getLogger().handlers:
    configure_logging(settings.secret_values(), settings.log_level)


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _migrate() -> None:
    if settings.migration_database_url is None:
        raise RuntimeError("NDM_MIGRATION_DATABASE_URL is not set")
    engine = create_async_engine(
        settings.migration_database_url.get_secret_value(), poolclass=pool.NullPool
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline mode is not supported; run against a database")

asyncio.run(_migrate())
