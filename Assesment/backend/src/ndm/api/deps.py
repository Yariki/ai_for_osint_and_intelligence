"""Dependencies shared by the endpoints: database session, settings, authentication."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.api import auth
from ndm.api.errors import ApiError
from ndm.core.settings import Settings
from ndm.db.models import Account, UserSession


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """One database session per request.

    An endpoint that changes data commits explicitly, as its last step, so the
    change and its audit entry are committed together. Anything not committed
    when the request ends is rolled back.
    """
    async with request.app.state.session_factory() as session:
        yield session


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


DbDep = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@dataclass(frozen=True)
class CurrentSession:
    account: Account
    session: UserSession


async def require_session(request: Request, db: DbDep, settings: SettingsDep) -> CurrentSession:
    """Authenticate the request by its session cookie, or answer 401.

    Applied to the parent router of the API, so a new endpoint is protected
    without doing anything (FR-ACC-1).
    """
    token = request.cookies.get(auth.COOKIE_NAME)
    found = None
    if token:
        found = await auth.use_session(db, token, settings.session_idle_timeout_seconds)
        # Commit the extended idle period (or the removal of an expired session)
        # now; the endpoint's own work is a separate transaction.
        await db.commit()
    if found is None:
        raise ApiError(401, "not_authenticated", "Authentication is required.")
    account, user_session = found
    return CurrentSession(account=account, session=user_session)


CurrentSessionDep = Annotated[CurrentSession, Depends(require_session)]
