"""Access: first-run setup, login, logout, current session, password change."""

import secrets

import structlog
from fastapi import APIRouter, Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.api import auth
from ndm.api.deps import CurrentSessionDep, DbDep, SettingsDep
from ndm.api.errors import ApiError
from ndm.api.schemas.access import (
    LoginRequest,
    PasswordChangeRequest,
    SessionInfo,
    SetupRequest,
    SetupStatus,
)
from ndm.db.audit import record_audit
from ndm.db.models import Account

log = structlog.get_logger(__name__)

# The only routes of the API that need no session, besides /healthz.
public_router = APIRouter(tags=["access"])
# Registered under the authenticated parent router.
router = APIRouter(tags=["access"])

# Key of the advisory lock that makes two concurrent setup requests run one after the other.
_SETUP_LOCK = 7_201_001


async def account_exists(db: AsyncSession) -> bool:
    return bool(await db.scalar(select(func.count()).select_from(Account)))


# --- First-run setup ---


@public_router.get("/setup")
async def setup_status(db: DbDep) -> SetupStatus:
    """Tell the interface whether to show the setup page or the login page."""
    return SetupStatus(setup_required=not await account_exists(db))


@public_router.post("/setup", status_code=201)
async def setup(body: SetupRequest, request: Request, db: DbDep) -> SetupStatus:
    """Create the single account. Needs the one-time token from the API log; works once."""
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SETUP_LOCK})
    if await account_exists(db):
        raise ApiError(409, "setup_done", "Setup has already been completed.")

    expected: str | None = request.app.state.setup_token
    if expected is None or not secrets.compare_digest(expected.encode(), body.token.encode()):
        raise ApiError(403, "invalid_setup_token", "The setup token is not valid.")

    account = Account(username=body.username, password_hash=await auth.hash_password(body.password))
    db.add(account)
    await db.flush()
    await record_audit(
        db,
        actor=account.username,
        action="account.setup",
        object_type="account",
        object_id=account.id,
    )
    await db.commit()
    request.app.state.setup_token = None
    return SetupStatus(setup_required=False)


# --- Login and logout ---


@public_router.post("/login")
async def login(
    body: LoginRequest, response: Response, db: DbDep, settings: SettingsDep
) -> SessionInfo:
    wait = await auth.seconds_until_login_allowed(db)
    if wait:
        log.warning("login_refused", retry_after=wait)
        raise ApiError(
            429,
            "too_many_attempts",
            "Too many failed logins. Try again later.",
            headers={"Retry-After": str(wait)},
        )

    account = await db.scalar(select(Account).where(Account.username == body.username))
    password_hash = account.password_hash if account else None
    if account is None or not await auth.verify_password(password_hash, body.password):
        # Committed before the error is raised: the failed login is the action.
        await record_audit(
            db,
            actor="anonymous",
            action=auth.LOGIN_FAILED,
            object_type="account",
            details={"username": body.username[:64]},
        )
        await db.commit()
        raise ApiError(401, "invalid_credentials", "The username or the password is wrong.")

    token, user_session = await auth.create_session(
        db, account.id, settings.session_idle_timeout_seconds
    )
    await record_audit(
        db, actor=account.username, action=auth.LOGIN, object_type="account", object_id=account.id
    )
    await db.commit()
    auth.set_session_cookie(response, token)
    return SessionInfo(username=account.username, expires_at=user_session.expires_at)


@router.post("/logout", status_code=204)
async def logout(response: Response, current: CurrentSessionDep, db: DbDep) -> None:
    await db.delete(current.session)
    await db.commit()
    auth.clear_session_cookie(response)


@router.get("/session")
async def current_session(current: CurrentSessionDep) -> SessionInfo:
    return SessionInfo(username=current.account.username, expires_at=current.session.expires_at)


# --- Password change ---


@router.post("/password", status_code=204)
async def change_password(
    body: PasswordChangeRequest, current: CurrentSessionDep, db: DbDep
) -> None:
    """Change the password. Every other session of the account ends."""
    account = current.account
    if not await auth.verify_password(account.password_hash, body.current_password):
        # Not 401: the session is valid, and 401 would send the browser to the login page.
        raise ApiError(403, "wrong_password", "The current password is wrong.")

    account.password_hash = await auth.hash_password(body.new_password)
    await auth.end_other_sessions(db, account.id, keep=current.session.token_hash)
    await record_audit(
        db,
        actor=account.username,
        action="account.password_change",
        object_type="account",
        object_id=account.id,
    )
    await db.commit()
