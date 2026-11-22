"""Fixtures for API tests: the application on the test database, and HTTP clients.

Requests go through the whole application in-process. The base URL is HTTPS
because the session cookie is ``Secure``.
"""

# Fixtures imported by name are used as arguments below.
# ruff: noqa: F811

import io
import logging
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import structlog
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ndm.api.app import create_app
from ndm.core.logging import configure_logging
from ndm.core.settings import Settings
from ndm.db.models import AuditEvent, Scan
from ndm.orchestrator.tick import Orchestrator
from tests.integration.conftest import DatabaseServer

# The orchestrator's fixtures, for API tests that need a scan with real results.
from tests.integration.orchestrator.conftest import (  # noqa: F401
    Outbox,
    ScanFactory,
    enriching_orchestrator,
    enrichment_settings,
    make_scan,
    outbox,
)
from tests.tools import reference_data
from tests.tools.enrichment_scan import RECORDED, scan_to_end

USERNAME = "analyst"
PASSWORD = "correct horse battery staple"


def new_client(app: FastAPI) -> AsyncClient:
    """A client with its own cookie jar, that is, its own browser."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver")


async def log_in(client: AsyncClient, password: str = PASSWORD) -> None:
    response = await client.post("/api/v1/login", json={"username": USERNAME, "password": password})
    assert response.status_code == 200, response.text


async def audit_actions(db_session: AsyncSession) -> list[str]:
    """Actions of all audit entries, oldest first."""
    await db_session.rollback()  # start a new snapshot
    return list(await db_session.scalars(select(AuditEvent.action).order_by(AuditEvent.id)))


@pytest.fixture
def json_log() -> Iterator[io.StringIO]:
    """Logging as in production, into a buffer."""
    root = logging.getLogger()
    handlers, level = root.handlers, root.level
    stream = io.StringIO()
    configure_logging(secrets=[], stream=stream)
    yield stream
    root.handlers, root.level = handlers, level
    structlog.reset_defaults()


@pytest.fixture
def extra_env() -> dict[str, str]:
    """More ``NDM_`` variables for the application under test.

    A test sets them with ``@pytest.mark.parametrize("extra_env", [{...}])``.
    """
    return {}


@pytest.fixture
def settings(
    monkeypatch: pytest.MonkeyPatch, database_server: DatabaseServer, extra_env: dict[str, str]
) -> Settings:
    for name in list(os.environ):
        if name.startswith("NDM_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("NDM_DATABASE_URL", database_server.app_url())
    for name, value in extra_env.items():
        monkeypatch.setenv(name, value)
    return Settings()


@pytest.fixture
async def app(settings: Settings, owner_engine: AsyncEngine) -> AsyncIterator[FastAPI]:
    """The application, started. No account exists yet."""
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def anonymous_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with new_client(app) as client:
        yield client


@pytest.fixture
async def account(app: FastAPI, anonymous_client: AsyncClient) -> None:
    """The account exists: first-run setup has been done."""
    response = await anonymous_client.post(
        "/api/v1/setup",
        json={"token": app.state.setup_token, "username": USERNAME, "password": PASSWORD},
    )
    assert response.status_code == 201, response.text


@pytest.fixture
async def client(app: FastAPI, account: None) -> AsyncIterator[AsyncClient]:
    """A logged-in client."""
    async with new_client(app) as logged_in:
        await log_in(logged_in)
        yield logged_in


# --- A scan with enrichment, for the host endpoints -------------------------------------


class Names:
    """Stands in for the resolver: reverse-DNS names for some hosts of the replayed scan."""

    NAMES = {
        "8.8.8.8": ["dns.example.net"],
        "8.8.8.1": ["ns1.example.net"],
        "193.0.6.1": ["web.example.nl", "www.example.nl"],
        "1.0.0.1": ["www.library.example.au"],
    }

    async def __call__(self, addresses: Any, nameserver: Any) -> dict[Any, tuple[list[str], str]]:
        return {
            address: (self.NAMES[str(address)], "ok")
            if str(address) in self.NAMES
            else ([], "no_name")
            for address in addresses
        }


@pytest.fixture
async def enriching(client: AsyncClient, enriching_orchestrator: Orchestrator) -> Orchestrator:
    """An orchestrator with every reference dataset loaded and reverse DNS answered
    from a table. The logged-in client comes first, so the account is the API's."""
    enricher = enriching_orchestrator.enricher
    assert enricher is not None
    await reference_data.load(
        enriching_orchestrator.sessions,
        enriching_orchestrator.settings,
        enricher.geolite,
        RECORDED.reference,
    )
    enricher.resolve = Names()
    enricher.settings = enricher.settings.model_copy(update={"ptr_enabled": True})
    return enriching_orchestrator


@pytest.fixture
async def enriched_scan(enriching: Orchestrator, make_scan: ScanFactory, outbox: Outbox) -> Scan:
    """The replayed scan of public addresses, complete, with snapshots and names."""
    return await scan_to_end(enriching, outbox, make_scan)


@pytest.fixture
async def classified_scan(
    enriching: Orchestrator, make_scan: ScanFactory, outbox: Outbox, monkeypatch: pytest.MonkeyPatch
) -> Scan:
    """The replayed scan, complete, with snapshots, names and classification."""
    from ndm.db.models import ScanPhase
    from ndm.orchestrator import phases
    from ndm.orchestrator.classification import load_ruleset
    from ndm.orchestrator.classification_phase import Classifier

    classifier = Classifier(load_ruleset(), 0.5)
    monkeypatch.setitem(phases.PHASES, ScanPhase.CLASSIFICATION, classifier.run_phase)
    return await scan_to_end(enriching, outbox, make_scan)
