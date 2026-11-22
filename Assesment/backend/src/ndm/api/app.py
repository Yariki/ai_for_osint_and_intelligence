"""The FastAPI application.

Run it with ``uvicorn --factory ndm.api.app:production_app``.
"""

import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import APIRouter, Depends, FastAPI

from ndm.api.commands import CommandPublisher
from ndm.api.deps import require_session
from ndm.api.errors import install_error_handlers
from ndm.api.events import EventHub
from ndm.api.routers import (
    access,
    alerts,
    audit,
    comparison,
    config,
    datasets,
    events,
    health,
    hosts,
    profiles,
    projects,
    scans,
    schedules,
    traceroutes,
)
from ndm.api.schemas.common import ErrorResponse
from ndm.core.logging import configure_logging
from ndm.core.scope import parse_never_scan
from ndm.core.settings import Settings, get_settings
from ndm.db.audit import SYSTEM_ACTOR, record_audit
from ndm.db.session import create_engine, create_session_factory

log = structlog.get_logger(__name__)

API_PREFIX = "/api/v1"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = create_engine(settings.database_url)
    app.state.session_factory = create_session_factory(engine)

    async with app.state.session_factory() as db:
        if settings.allow_non_public_targets:
            # NFR-SEC-6: the use of the test switch goes to the log and the audit log.
            log.warning("test_switch_enabled", setting="NDM_ALLOW_NON_PUBLIC_TARGETS")
            await record_audit(
                db,
                actor=SYSTEM_ACTOR,
                action="test_switch.enabled",
                object_type="system",
                details={"process": "api"},
            )
            await db.commit()

        # First run: nobody can log in yet, so the right to create the account
        # is proven with a token that only someone who reads this log has.
        app.state.setup_token = None
        if not await access.account_exists(db):
            app.state.setup_token = secrets.token_urlsafe(32)
            log.warning(
                "setup_required",
                setup_token=app.state.setup_token,
                hint="Open the application and enter this token to create the account.",
            )

    # Live updates. The listener reconnects by itself; the API does not wait for the broker.
    app.state.events.start(settings.broker_url)

    yield
    await app.state.events.stop()
    await app.state.commands.close()
    await engine.dispose()


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(
        title="Network Discovery Map",
        version="1",
        lifespan=_lifespan,
        # No interactive documentation and no schema route: every route the
        # application serves is either authenticated or one of the three public ones.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    )
    app.state.settings = settings
    # Parsed here, before anything is served: with an invalid never-scan entry
    # the API refuses to start (ScopeError) and does not guess what was meant.
    app.state.never_scan = parse_never_scan(settings.never_scan)
    # Opens its broker connection on first use; the API starts without a broker.
    app.state.commands = CommandPublisher(settings.broker_url, settings.vantage_point)
    app.state.events = EventHub()
    install_error_handlers(app)

    # Everything registered on this router needs a session. New routers go here.
    protected = APIRouter(prefix=API_PREFIX, dependencies=[Depends(require_session)])
    protected.include_router(access.router)
    protected.include_router(projects.router)
    protected.include_router(audit.router)
    protected.include_router(scans.router)
    protected.include_router(config.router)
    protected.include_router(hosts.router)
    protected.include_router(datasets.router)
    protected.include_router(traceroutes.router)
    protected.include_router(profiles.router)
    protected.include_router(schedules.router)
    protected.include_router(comparison.router)
    protected.include_router(alerts.router)
    protected.include_router(events.router)

    # The only public routes: /healthz, login and first-run setup.
    app.include_router(health.router)
    app.include_router(access.public_router, prefix=API_PREFIX)
    app.include_router(protected)
    return app


def production_app() -> FastAPI:
    """Application factory for uvicorn: settings from the environment, JSON logging."""
    settings = get_settings()
    configure_logging(settings.secret_values(), settings.log_level)
    # uvicorn installs handlers of its own; send its records through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
    return create_app(settings)
