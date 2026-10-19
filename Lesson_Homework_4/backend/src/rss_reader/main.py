import logging
from collections.abc import Iterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from rss_reader import store
from rss_reader.config import Settings
from rss_reader.db import create_db_engine
from rss_reader.jobs import request_job
from rss_reader.models import (
    Job,
    Source,
    Tag,
)
from rss_reader.schemas import (
    ArticleOut,
    ErrorResponse,
    JobOut,
    JobStatus,
    Page,
    SourceCreate,
    SourceOut,
    SourceUpdate,
    Stats,
    TagOut,
)

logger = logging.getLogger(__name__)


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine, expire_on_commit=False) as session:
        yield session


DB = Annotated[Session, Depends(get_session)]


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.getLogger("httpx").setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.engine = create_db_engine(settings)
        yield
        app.state.engine.dispose()

    app = FastAPI(
        title="Respectful RSS Reader",
        lifespan=lifespan,
        responses={code: {"model": ErrorResponse} for code in (400, 403, 404, 409, 422, 503)},
    )

    @app.middleware("http")
    async def same_origin_mutations(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
            supplied = request.headers.get("origin")
            if request.headers.get("sec-fetch-site") == "cross-site" or (
                supplied and supplied != expected
            ):
                return error_response(403, "origin", "Cross-origin mutations are not permitted")
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        return error_response(exc.status_code, "request", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        fields = sorted({".".join(str(x) for x in item["loc"]) for item in exc.errors()})
        return error_response(422, "validation", "Invalid fields: " + ", ".join(fields))

    @app.exception_handler(SQLAlchemyError)
    async def storage_error(_request: Request, _exc: SQLAlchemyError):
        logger.error("An API database operation failed")
        return error_response(503, "storage", "Database is unavailable; try again shortly")

    @app.get("/api/health")
    def health(session: DB) -> dict[str, str]:
        store.check_database(session)
        return {"status": "ok"}

    @app.get("/api/settings")
    def public_settings() -> dict[str, int]:
        return {"default_period_minutes": settings.default_period_minutes}

    @app.get("/api/sources", response_model=list[SourceOut])
    def sources(session: DB) -> Sequence[Source]:
        return store.list_sources(session)

    @app.post("/api/sources", response_model=SourceOut, status_code=201)
    def add_source(payload: SourceCreate, session: DB) -> Source:
        try:
            return store.add_source(session, payload, settings.default_period_minutes)
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, "This feed URL already exists") from None

    @app.put("/api/sources/{source_id}", response_model=SourceOut)
    def update_source(source_id: int, payload: SourceUpdate, session: DB) -> Source:
        try:
            return store.update_source(session, source_id, payload)
        except LookupError:
            raise HTTPException(404, "Source not found") from None

    @app.post("/api/sources/{source_id}/crawl", response_model=JobOut, status_code=202)
    def crawl_now(source_id: int, request: Request, session: DB) -> Job:
        try:
            job_id = request_job(request.app.state.engine, source_id)
            # Manual requests join the durable queue for the next dispatcher tick.
            return store.get_job(session, job_id)
        except LookupError:
            raise HTTPException(404, "Source not found") from None

    @app.get("/api/jobs", response_model=Page[JobOut])
    def jobs(
        session: DB,
        source_id: int | None = None,
        status: JobStatus | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ) -> Page[JobOut]:
        return store.list_jobs(session, source_id, status, page, page_size)

    @app.get("/api/tags", response_model=list[TagOut])
    def tags(session: DB) -> Sequence[Tag]:
        return store.list_tags(session)

    @app.get("/api/articles", response_model=Page[ArticleOut])
    def articles(
        session: DB,
        tag_ids: Annotated[list[int] | None, Query(max_length=50)] = None,
        source_id: int | None = None,
        search: str = Query("", max_length=200),
        published_from: datetime | None = None,
        published_to: datetime | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ) -> Page[ArticleOut]:
        if any(value and value.tzinfo is None for value in (published_from, published_to)):
            raise HTTPException(422, "Date filters must include a timezone")
        if published_from and published_to and published_from > published_to:
            raise HTTPException(422, "Start date must precede end date")
        return store.list_articles(
            session,
            tag_ids=tag_ids,
            source_id=source_id,
            search=search,
            published_from=published_from,
            published_to=published_to,
            page=page,
            page_size=page_size,
        )

    @app.get("/api/stats", response_model=Stats)
    def stats(session: DB) -> Stats:
        return store.statistics(session)

    return app
