"""Fixtures for integration tests: a real PostgreSQL and a real RabbitMQ through
testcontainers (NFR-TST-1).

One container serves the whole run. It is migrated once, as the owning role;
tests then connect as the application role, as the API does. Every test starts
with empty tables.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import aio_pika
import pytest
from aio_pika.abc import AbstractChannel
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.rabbitmq import RabbitMqContainer

from ndm.core import broker

# The same pinned image as the Compose stack (deploy/compose.yaml).
POSTGRES_IMAGE = "postgis/postgis:17-3.5"
RABBITMQ_IMAGE = "rabbitmq:4.3.7-alpine"

BROKER_USER, BROKER_PASSWORD = "ndm", "broker-test-password"
BROKER_VANTAGE = "test"

DATABASE = "ndm"
OWNER, OWNER_PASSWORD = "ndm_owner", "owner-test-password"
APP_ROLE, APP_PASSWORD = "ndm_app", "app-test-password"

BACKEND = Path(__file__).resolve().parents[2]
# Mounted over the image's own init script, as in the Compose stack: the
# database gets no extension but those the migrations install.
NO_POSTGIS_INIT = BACKEND.parent / "deploy" / "postgres" / "no-postgis-init.sh"


@dataclass(frozen=True)
class DatabaseServer:
    host: str
    port: int

    def owner_url(self, database: str = DATABASE) -> str:
        return self._url(OWNER, OWNER_PASSWORD, database)

    def app_url(self, database: str = DATABASE) -> str:
        return self._url(APP_ROLE, APP_PASSWORD, database)

    def _url(self, user: str, password: str, database: str) -> str:
        return f"postgresql+asyncpg://{user}:{password}@{self.host}:{self.port}/{database}"


def run_alembic(owner_url: str, action: str, revision: str) -> None:
    """Run ``alembic upgrade`` or ``alembic downgrade`` against a database.

    Blocking, and it starts an event loop of its own: from an async test call
    it as ``await asyncio.to_thread(run_alembic, ...)``.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("NDM_MIGRATION_DATABASE_URL", owner_url)
        patch.setenv("NDM_APP_DB_ROLE", APP_ROLE)
        getattr(command, action)(Config(str(BACKEND / "alembic.ini")), revision)


@pytest.fixture(scope="session")
def database_server() -> Iterator[DatabaseServer]:
    container = PostgresContainer(
        POSTGRES_IMAGE, username=OWNER, password=OWNER_PASSWORD, dbname=DATABASE
    ).with_volume_mapping(NO_POSTGIS_INIT, "/docker-entrypoint-initdb.d/10_postgis.sh")
    with container:
        yield DatabaseServer(
            host=container.get_container_host_ip(), port=int(container.get_exposed_port(5432))
        )


@pytest.fixture(scope="session")
async def owner_engine(database_server: DatabaseServer) -> AsyncIterator[AsyncEngine]:
    """Engine of the owning role, on a migrated database."""
    engine = create_async_engine(database_server.owner_url())
    async with engine.begin() as connection:
        # The application role is created outside the migrations: its password is a secret.
        await connection.execute(text(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'"))
    await asyncio.to_thread(run_alembic, database_server.owner_url(), "upgrade", "head")
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
async def app_engine(
    database_server: DatabaseServer, owner_engine: AsyncEngine
) -> AsyncIterator[AsyncEngine]:
    """Engine of the application role, which is what the API uses."""
    engine = create_async_engine(database_server.app_url())
    yield engine
    await engine.dispose()


@pytest.fixture(autouse=True)
async def empty_tables(owner_engine: AsyncEngine) -> None:
    """Empty every table before each test.

    The audit log rejects TRUNCATE through a trigger, for every role. Only the
    owner can switch that trigger off, and it does so here for the clean-up alone.
    """
    async with owner_engine.begin() as connection:
        tables = await connection.scalars(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                # spatial_ref_sys is PostGIS's own table, not the application's.
                "AND tablename NOT IN ('alembic_version', 'spatial_ref_sys')"
            )
        )
        names = ", ".join(f'"{name}"' for name in tables)
        await connection.execute(text("ALTER TABLE audit_event DISABLE TRIGGER USER"))
        await connection.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        await connection.execute(text("ALTER TABLE audit_event ENABLE TRIGGER USER"))


@pytest.fixture
async def db_session(app_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session of the application role."""
    async with async_sessionmaker(app_engine, expire_on_commit=False)() as session:
        yield session


@pytest.fixture
async def scratch_database(
    database_server: DatabaseServer, owner_engine: AsyncEngine
) -> AsyncIterator[str]:
    """Owner URL of an empty database, so reverting does not disturb other tests."""
    name = "migration_test"
    async with owner_engine.connect() as connection:
        autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
        await autocommit.execute(text(f"DROP DATABASE IF EXISTS {name}"))
        await autocommit.execute(text(f"CREATE DATABASE {name}"))
    yield database_server.owner_url(name)
    async with owner_engine.connect() as connection:
        autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
        await autocommit.execute(text(f"DROP DATABASE {name}"))


# --- RabbitMQ ---


@pytest.fixture(scope="session")
def broker_url() -> Iterator[str]:
    """URL of a RabbitMQ that serves the whole run. Started when first asked for."""
    container = RabbitMqContainer(RABBITMQ_IMAGE, username=BROKER_USER, password=BROKER_PASSWORD)
    with container:
        host, port = container.get_container_host_ip(), container.get_exposed_port(5672)
        yield f"amqp://{BROKER_USER}:{BROKER_PASSWORD}@{host}:{port}/"


@pytest.fixture
async def topology(broker_url: str) -> AsyncIterator[AbstractChannel]:
    """A channel on a broker with the topology declared and every queue empty."""
    connection = await aio_pika.connect_robust(broker_url)
    channel = await connection.channel()
    await broker.declare_topology(channel, BROKER_VANTAGE)
    for name in (
        broker.chunk_queue(BROKER_VANTAGE),
        broker.QUEUE_RESULTS,
        broker.QUEUE_COMMANDS,
        broker.QUEUE_DEAD,
    ):
        await (await channel.get_queue(name)).purge()
    yield channel
    await connection.close()
