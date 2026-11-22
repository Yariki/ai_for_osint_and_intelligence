"""Async engine and session factory."""

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(url: SecretStr | None) -> AsyncEngine:
    """Create the engine for a process. Fails if the URL is not configured."""
    if url is None:
        raise RuntimeError("the database URL is not set (NDM_DATABASE_URL)")
    return create_async_engine(url.get_secret_value(), pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: objects stay readable after the commit, which an
    # async session needs because it cannot reload attributes implicitly.
    return async_sessionmaker(engine, expire_on_commit=False)
