"""Load checks on a database seeded to the sizing envelope (A7; check 2.13).

The checks on a seeded database are skipped unless one of these is set:

- ``NDM_LOAD_DATABASE_URL``: the owner URL of a migrated database that
  ``scripts/seed_envelope.py`` has filled. This is the real check:

      uv run python scripts/seed_envelope.py --database-url <owner URL>
      NDM_LOAD_DATABASE_URL=<owner URL> uv run pytest tests/load

- ``NDM_LOAD_SCALE``: a share of the envelope, for trying the checks out. A
  database is started as for the integration tests, migrated and seeded at
  that scale. Passing at a small scale proves nothing about the envelope.

The checks of the running stack on the lab (LAB_FILES) need NDM_LAB=1 instead.

A check that measures something writes it to NDM_LOAD_REPORT_DIR as JSON,
when that is set, for the capacity report (docs/release/capacity-report.md).
"""

import asyncio
import os
import secrets
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ndm.api.app import create_app
from ndm.api.auth import COOKIE_NAME, hash_token
from ndm.core.settings import Settings
from tests.integration.conftest import (
    APP_PASSWORD,
    APP_ROLE,
    BACKEND,
    DATABASE,
    OWNER,
    OWNER_PASSWORD,
    POSTGRES_IMAGE,
    run_alembic,
)

URL = os.environ.get("NDM_LOAD_DATABASE_URL")
SCALE = os.environ.get("NDM_LOAD_SCALE")


# Load checks against the Compose lab and the running stack, not a seeded
# database: they run with NDM_LAB=1, like the lab suite (tests/lab/conftest.py).
LAB_FILES = {"test_endurance.py", "test_all_ports_open.py"}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    on_database = pytest.mark.skip(reason="set NDM_LOAD_DATABASE_URL (or NDM_LOAD_SCALE) to run")
    on_lab = pytest.mark.skip(reason="needs the Compose lab; set NDM_LAB=1 on the lab host")
    for item in items:
        if "tests/load" not in str(item.path):
            continue
        if item.path.name in LAB_FILES:
            if os.environ.get("NDM_LAB") != "1":
                item.add_marker(on_lab)
        elif not (URL or SCALE):
            item.add_marker(on_database)


def _seed_module() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "seed_envelope", BACKEND / "scripts" / "seed_envelope.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def owner_url() -> Iterator[str]:
    """The owner URL of the seeded database."""
    if URL:
        yield URL
        return
    from testcontainers.community.postgres import PostgresContainer

    container = PostgresContainer(
        POSTGRES_IMAGE, username=OWNER, password=OWNER_PASSWORD, dbname=DATABASE
    )
    with container:
        host, port = container.get_container_host_ip(), container.get_exposed_port(5432)
        url = f"postgresql+asyncpg://{OWNER}:{OWNER_PASSWORD}@{host}:{port}/{DATABASE}"

        async def prepare() -> None:
            engine = create_async_engine(url, poolclass=pool.NullPool)
            async with engine.begin() as connection:
                await connection.execute(
                    text(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'")
                )
            await asyncio.to_thread(run_alembic, url, "upgrade", "head")
            seed = _seed_module()
            await seed.seed(engine, seed.Plan.for_scale(float(SCALE or 0)))
            await engine.dispose()

        asyncio.run(prepare())
        yield url


@pytest.fixture(scope="session")
async def owner(owner_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(owner_url)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
async def facts(owner: AsyncEngine) -> dict[str, Any]:
    """What the seeded database holds: the largest project and its latest scan."""
    async with owner.connect() as connection:
        counts = {
            table: await connection.scalar(text(f"SELECT count(*) FROM {table}"))
            for table in ("project", "scan", "host_observation", "port_observation")
        }
        project = await connection.scalar(
            text("SELECT project_id FROM project_host GROUP BY 1 ORDER BY count(*) DESC LIMIT 1")
        )
        addresses = await connection.scalar(
            text("SELECT count(*) FROM project_host WHERE project_id = :p"), {"p": project}
        )
        scan = await connection.scalar(
            text("SELECT max(id) FROM scan WHERE project_id = :p"), {"p": project}
        )
    return {"counts": counts, "project": project, "addresses": addresses, "scan": scan}


@pytest.fixture(scope="session")
async def client(owner: AsyncEngine, owner_url: str) -> AsyncIterator[AsyncClient]:
    """A logged-in client of the API on the seeded database.

    The seeded account has no usable password, so a session is written for it
    directly, as the login endpoint would.
    """
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    async with owner.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO session (token_hash, account_id, last_seen_at, expires_at) "
                "VALUES (:hash, (SELECT min(id) FROM account), :now, :expires)"
            ),
            {"hash": hash_token(token), "now": now, "expires": now + timedelta(hours=2)},
        )
    with pytest.MonkeyPatch.context() as patch:
        for name in list(os.environ):
            if name.startswith("NDM_"):
                patch.delenv(name)
        # The owner's URL: the application role of an external database is not known here.
        patch.setenv("NDM_DATABASE_URL", owner_url)
        patch.setenv("NDM_SESSION_IDLE_TIMEOUT_SECONDS", "7200")
        app: FastAPI = create_app(Settings())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="https://testserver",
            cookies={COOKIE_NAME: token},
            timeout=60,
        ) as http:
            yield http


def report(name: str, values: dict[str, Any]) -> None:
    """Print a measurement, and keep it in NDM_LOAD_REPORT_DIR/<name>.json if set."""
    import json
    from pathlib import Path

    print(f"\n{name}: {json.dumps(values, indent=1, default=str)}")
    directory = os.environ.get("NDM_LOAD_REPORT_DIR")
    if directory:
        Path(directory).mkdir(parents=True, exist_ok=True)
        (Path(directory) / f"{name}.json").write_text(json.dumps(values, indent=1, default=str))
