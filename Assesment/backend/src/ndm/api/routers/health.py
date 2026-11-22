"""Health check. Public: the container health check and the proxy call it."""

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ndm.api.deps import DbDep
from ndm.api.errors import ApiError
from ndm.api.schemas.access import HealthStatus

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(db: DbDep) -> HealthStatus:
    try:
        await db.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError) as error:
        raise ApiError(503, "unavailable", "The database is not reachable.") from error
    return HealthStatus(status="ok")
