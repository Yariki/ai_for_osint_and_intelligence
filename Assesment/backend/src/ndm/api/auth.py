"""Passwords, sessions and login throttling (architecture section 12.3)."""

import asyncio
import hashlib
import math
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import Account, AuditEvent, UserSession

COOKIE_NAME = "ndm_session"

# Audit actions written by the access endpoints.
LOGIN = "login"
LOGIN_FAILED = "login.failed"

# Throttling (FR-ACC-4): after this many failed logins inside the window,
# further attempts are refused until the oldest of them leaves the window.
FAILED_LOGIN_LIMIT = 5
FAILED_LOGIN_WINDOW = timedelta(minutes=5)

_hasher = PasswordHasher()  # argon2id (NFR-SEC-3)
# Verified against when the username is unknown, so that an unknown username
# and a wrong password take the same time.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def _now() -> datetime:
    return datetime.now(UTC)


# --- Passwords ---


async def hash_password(password: str) -> str:
    # Hashing is slow on purpose; keep it off the event loop.
    return await asyncio.to_thread(_hasher.hash, password)


def _verify(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError, InvalidHashError:
        return False


async def verify_password(password_hash: str | None, password: str) -> bool:
    """Check a password. ``None`` as hash (unknown account) never matches."""
    matches = await asyncio.to_thread(_verify, password_hash or _DUMMY_HASH, password)
    return matches and password_hash is not None


# --- Sessions ---


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


async def create_session(
    db: AsyncSession, account_id: int, idle_seconds: int
) -> tuple[str, UserSession]:
    """Create a session and return its token. Only the token's hash is stored."""
    token = secrets.token_urlsafe(32)  # 256 random bits
    now = _now()
    user_session = UserSession(
        token_hash=hash_token(token),
        account_id=account_id,
        last_seen_at=now,
        expires_at=now + timedelta(seconds=idle_seconds),
    )
    db.add(user_session)
    await db.flush()
    return token, user_session


async def use_session(
    db: AsyncSession, token: str, idle_seconds: int
) -> tuple[Account, UserSession] | None:
    """Find the session of a token and extend its idle period.

    Returns ``None`` when there is no such session or it has expired; an
    expired session is deleted. The caller commits.
    """
    row = (
        await db.execute(
            select(Account, UserSession)
            .join(UserSession, UserSession.account_id == Account.id)
            .where(UserSession.token_hash == hash_token(token))
        )
    ).one_or_none()
    if row is None:
        return None
    account, user_session = row
    now = _now()
    if user_session.expires_at <= now:
        await db.delete(user_session)
        return None
    user_session.last_seen_at = now
    user_session.expires_at = now + timedelta(seconds=idle_seconds)
    return account, user_session


async def end_other_sessions(db: AsyncSession, account_id: int, keep: bytes) -> None:
    await db.execute(
        delete(UserSession).where(
            UserSession.account_id == account_id, UserSession.token_hash != keep
        )
    )


def set_session_cookie(response: Response, token: str) -> None:
    # No Max-Age: the cookie ends with the browser session, and the server
    # enforces the idle expiry.
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=True, samesite="strict", path="/")


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, httponly=True, secure=True, samesite="strict", path="/")


# --- Throttling ---


async def seconds_until_login_allowed(db: AsyncSession) -> int:
    """0 if a login attempt may be made now, otherwise how long to wait.

    Failed logins are audit events; the recent ones are counted here.
    """
    now = _now()
    recent = (
        await db.scalars(
            select(AuditEvent.occurred_at)
            .where(
                AuditEvent.action == LOGIN_FAILED,
                AuditEvent.occurred_at > now - FAILED_LOGIN_WINDOW,
            )
            .order_by(AuditEvent.occurred_at.desc())
            .limit(FAILED_LOGIN_LIMIT)
        )
    ).all()
    if len(recent) < FAILED_LOGIN_LIMIT:
        return 0
    oldest = recent[-1]
    return max(1, math.ceil((oldest + FAILED_LOGIN_WINDOW - now).total_seconds()))
