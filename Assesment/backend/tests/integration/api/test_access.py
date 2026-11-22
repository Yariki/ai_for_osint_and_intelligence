"""Check 1A.4: first run and sessions. Also the audit entries of the access endpoints."""

import io
import json
import runpy
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ndm.api import auth
from ndm.api.app import create_app
from ndm.core.settings import Settings
from ndm.db.models import Account, AuditEvent, UserSession
from tests.integration.api.conftest import (
    PASSWORD,
    USERNAME,
    audit_actions,
    log_in,
    new_client,
)

NEW_PASSWORD = "a different long password"


def setup_body(app: FastAPI, **changes: str) -> dict[str, str]:
    body = {"token": app.state.setup_token, "username": USERNAME, "password": PASSWORD}
    return {**body, **changes}


# --- First-run setup ---


async def test_setup_needs_the_token_and_works_once(
    app: FastAPI, anonymous_client: AsyncClient, db_session: AsyncSession
) -> None:
    token = app.state.setup_token
    assert (await anonymous_client.get("/api/v1/setup")).json() == {"setup_required": True}

    wrong = await anonymous_client.post("/api/v1/setup", json=setup_body(app, token="guess"))
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "invalid_setup_token"
    assert await db_session.scalar(select(func.count()).select_from(Account)) == 0

    done = await anonymous_client.post("/api/v1/setup", json=setup_body(app))
    assert done.status_code == 201
    assert (await anonymous_client.get("/api/v1/setup")).json() == {"setup_required": False}

    # The token is used up: a second setup fails, even with the token that was valid.
    again = await anonymous_client.post(
        "/api/v1/setup", json={"token": token, "username": "intruder", "password": NEW_PASSWORD}
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "setup_done"
    assert list(await db_session.scalars(select(Account.username))) == [USERNAME]
    assert await audit_actions(db_session) == ["account.setup"]


async def test_password_is_stored_as_an_argon2id_hash(
    account: None, db_session: AsyncSession
) -> None:
    stored = await db_session.scalar(select(Account.password_hash))

    assert stored is not None
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored


async def test_short_password_is_rejected_without_echoing_it(
    app: FastAPI, anonymous_client: AsyncClient
) -> None:
    response = await anonymous_client.post(
        "/api/v1/setup", json=setup_body(app, password="short-pw")
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert [detail["field"] for detail in error["details"]] == ["password"]
    assert "short-pw" not in response.text


async def test_setup_token_is_printed_to_the_log_while_no_account_exists(
    settings: Settings, owner_engine: AsyncEngine, json_log: io.StringIO
) -> None:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        lines = [json.loads(line) for line in json_log.getvalue().splitlines()]
        (line,) = [line for line in lines if line["event"] == "setup_required"]
        assert line["setup_token"] == application.state.setup_token
        assert len(application.state.setup_token) >= 43  # 256 bits, URL-safe base64

        async with new_client(application) as client:
            assert (
                await client.post("/api/v1/setup", json=setup_body(application))
            ).status_code == 201

    # With an account, a restart prints no token and setup stays closed.
    json_log.truncate(0)
    restarted = create_app(settings)
    async with restarted.router.lifespan_context(restarted):
        assert restarted.state.setup_token is None
        assert "setup_required" not in json_log.getvalue()


# --- Login and sessions ---


async def test_login_sets_a_strict_cookie_and_stores_only_the_token_hash(
    app: FastAPI, account: None, db_session: AsyncSession
) -> None:
    async with new_client(app) as client:
        response = await client.post(
            "/api/v1/login", json={"username": USERNAME, "password": PASSWORD}
        )
        assert response.status_code == 200
        assert response.json()["username"] == USERNAME

        cookie = response.headers["set-cookie"]
        token = response.cookies["ndm_session"]
        for attribute in ["HttpOnly", "Secure", "SameSite=strict", "Path=/"]:
            assert attribute in cookie

        stored = await db_session.scalar(select(UserSession.token_hash))
        assert stored == auth.hash_token(token)
        assert token.encode() not in (stored or b"")

        session = await client.get("/api/v1/session")
        assert session.status_code == 200
        assert session.json()["username"] == USERNAME

    assert await audit_actions(db_session) == ["account.setup", "login"]


@pytest.mark.parametrize(
    ("username", "password"), [(USERNAME, "wrong password!"), ("nobody", PASSWORD)]
)
async def test_failed_login_is_refused_and_audited(
    account: None,
    anonymous_client: AsyncClient,
    db_session: AsyncSession,
    username: str,
    password: str,
) -> None:
    response = await anonymous_client.post(
        "/api/v1/login", json={"username": username, "password": password}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"
    assert "set-cookie" not in response.headers
    assert await db_session.scalar(select(func.count()).select_from(UserSession)) == 0

    await db_session.rollback()
    entry = await db_session.scalar(select(AuditEvent).where(AuditEvent.action == "login.failed"))
    assert entry is not None
    assert (entry.actor, entry.details) == ("anonymous", {"username": username})


async def test_logout_ends_the_session(client: AsyncClient, db_session: AsyncSession) -> None:
    assert (await client.post("/api/v1/logout")).status_code == 204

    assert await db_session.scalar(select(func.count()).select_from(UserSession)) == 0
    assert (await client.get("/api/v1/session")).status_code == 401


async def test_idle_session_expires(
    client: AsyncClient, owner_engine: AsyncEngine, db_session: AsyncSession
) -> None:
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE session SET expires_at = :past"),
            {"past": datetime.now(UTC) - timedelta(seconds=1)},
        )

    response = await client.get("/api/v1/session")

    assert response.status_code == 401
    assert await db_session.scalar(select(func.count()).select_from(UserSession)) == 0


async def test_activity_extends_the_idle_period(
    client: AsyncClient, owner_engine: AsyncEngine, settings: Settings
) -> None:
    soon = datetime.now(UTC) + timedelta(seconds=30)
    async with owner_engine.begin() as connection:
        await connection.execute(text("UPDATE session SET expires_at = :soon"), {"soon": soon})

    response = await client.get("/api/v1/session")

    expires_at = datetime.fromisoformat(response.json()["expires_at"])
    idle = timedelta(seconds=settings.session_idle_timeout_seconds)
    assert expires_at > soon
    assert abs(expires_at - (datetime.now(UTC) + idle)) < timedelta(seconds=10)


async def test_idle_timeout_is_configurable(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, owner_engine: AsyncEngine
) -> None:
    monkeypatch.setenv("NDM_SESSION_IDLE_TIMEOUT_SECONDS", "60")
    application = create_app(Settings())
    async with application.router.lifespan_context(application), new_client(application) as client:
        await client.post("/api/v1/setup", json=setup_body(application))
        response = await client.post(
            "/api/v1/login", json={"username": USERNAME, "password": PASSWORD}
        )

    expires_at = datetime.fromisoformat(response.json()["expires_at"])
    assert abs(expires_at - (datetime.now(UTC) + timedelta(seconds=60))) < timedelta(seconds=10)


# --- Password change ---


async def test_password_change_ends_other_sessions(
    app: FastAPI, client: AsyncClient, db_session: AsyncSession
) -> None:
    async with new_client(app) as other:
        await log_in(other)

        response = await client.post(
            "/api/v1/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
        )
        assert response.status_code == 204

        # The session that changed the password goes on; the other one is over.
        assert (await client.get("/api/v1/session")).status_code == 200
        assert (await other.get("/api/v1/session")).status_code == 401

    async with new_client(app) as fresh:
        old = await fresh.post("/api/v1/login", json={"username": USERNAME, "password": PASSWORD})
        assert old.status_code == 401
        await log_in(fresh, NEW_PASSWORD)

    assert "account.password_change" in await audit_actions(db_session)


async def test_password_change_needs_the_current_password(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    before = await db_session.scalar(select(Account.password_hash))

    response = await client.post(
        "/api/v1/password",
        json={"current_password": "not my password", "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "wrong_password"
    await db_session.rollback()
    assert await db_session.scalar(select(Account.password_hash)) == before
    assert "account.password_change" not in await audit_actions(db_session)


# --- Throttling (FR-ACC-4) ---


async def test_repeated_failed_logins_are_refused(
    account: None,
    anonymous_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong = {"username": USERNAME, "password": "wrong password!"}
    right = {"username": USERNAME, "password": PASSWORD}
    for _ in range(auth.FAILED_LOGIN_LIMIT):
        assert (await anonymous_client.post("/api/v1/login", json=wrong)).status_code == 401

    # Now even the right password is refused, and the refusal says how long to wait.
    refused = await anonymous_client.post("/api/v1/login", json=right)
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "too_many_attempts"
    assert 0 < int(refused.headers["retry-after"]) <= auth.FAILED_LOGIN_WINDOW.total_seconds()

    # A refused attempt is not counted, so it cannot prolong the lock.
    actions = await audit_actions(db_session)
    assert actions.count("login.failed") == auth.FAILED_LOGIN_LIMIT
    assert "login" not in actions

    # When the window has passed, login works again.
    later = datetime.now(UTC) + auth.FAILED_LOGIN_WINDOW + timedelta(seconds=1)
    monkeypatch.setattr(auth, "_now", lambda: later)
    assert (await anonymous_client.post("/api/v1/login", json=right)).status_code == 200


# --- Test switch at start-up (NFR-SEC-6) ---


async def test_test_switch_writes_an_audit_entry_at_start_up(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    owner_engine: AsyncEngine,
    db_session: AsyncSession,
) -> None:
    quiet = create_app(settings)
    async with quiet.router.lifespan_context(quiet):
        assert await audit_actions(db_session) == []

    monkeypatch.setenv("NDM_ALLOW_NON_PUBLIC_TARGETS", "true")
    switched_on = create_app(Settings())
    async with switched_on.router.lifespan_context(switched_on):
        pass

    await db_session.rollback()
    entry = await db_session.scalar(select(AuditEvent))
    assert entry is not None
    assert (entry.actor, entry.action, entry.details) == (
        "system",
        "test_switch.enabled",
        {"process": "api"},
    )


# --- Error shape and schema export ---


async def test_unknown_path_answers_in_the_error_shape(anonymous_client: AsyncClient) -> None:
    response = await anonymous_client.get("/api/v1/no-such-thing")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Not Found", "details": []}
    }


def test_openapi_schema_is_exported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script = Path(__file__).resolve().parents[3] / "scripts" / "export_openapi.py"
    output = tmp_path / "openapi.json"
    monkeypatch.setattr(sys, "argv", [str(script), str(output)])

    runpy.run_path(str(script), run_name="__main__")

    schema = json.loads(output.read_text())
    assert {"/healthz", "/api/v1/setup", "/api/v1/login", "/api/v1/session"} <= schema[
        "paths"
    ].keys()
    assert "ErrorResponse" in schema["components"]["schemas"]
