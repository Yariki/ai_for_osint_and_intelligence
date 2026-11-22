"""Check 1A.5: scope and attestation. Check 1A.6, API half: a rollback leaves no audit entry."""

import io
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.api.app import create_app
from ndm.core.scope import ScopeError
from ndm.core.settings import Settings
from ndm.db.audit import record_audit
from ndm.db.models import Attestation, AuditEvent, Project, ScopeEntry
from tests.integration.api.conftest import audit_actions, log_in


@pytest.fixture
async def project_id(client: AsyncClient) -> int:
    response = await client.post("/api/v1/projects", json={"name": "Edge routers"})
    created: int = response.json()["id"]
    return created


async def save(client: AsyncClient, project_id: int, entries: list[str], **body: Any) -> Any:
    return await client.put(
        f"/api/v1/projects/{project_id}/scope",
        json={"entries": entries, "authorized": True, **body},
    )


async def count(db_session: AsyncSession, model: type) -> int:
    await db_session.rollback()
    return await db_session.scalar(select(func.count()).select_from(model)) or 0


# --- Attestation ---


async def test_new_project_has_an_empty_scope(client: AsyncClient, project_id: int) -> None:
    scope = (await client.get(f"/api/v1/projects/{project_id}/scope")).json()

    assert scope == {
        "attestation_id": None,
        "attested_at": None,
        "attested_by": None,
        "entries": [],
    }


@pytest.mark.parametrize("body", [{"authorized": False}, {}])
async def test_scope_cannot_be_saved_without_the_confirmation(
    client: AsyncClient, project_id: int, db_session: AsyncSession, body: dict[str, bool]
) -> None:
    response = await client.put(
        f"/api/v1/projects/{project_id}/scope", json={"entries": ["8.8.8.0/24"], **body}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "attestation_required"
    assert await count(db_session, Attestation) == 0
    assert await count(db_session, ScopeEntry) == 0
    assert "scope.change" not in await audit_actions(db_session)


async def test_each_save_creates_a_new_immutable_attestation_with_the_exact_entries(
    client: AsyncClient, project_id: int, db_session: AsyncSession
) -> None:
    first_entries = ["8.8.8.0/24", "1.1.1.1", "9.9.9.1-9.9.9.9"]
    second_entries = ["8.8.4.0/24"]

    first = (await save(client, project_id, first_entries)).json()
    second = (await save(client, project_id, second_entries)).json()

    assert first["attested_by"] == "analyst"
    assert first["entries"] == [
        {"kind": "cidr", "text": "8.8.8.0/24", "start": "8.8.8.0", "end": "8.8.8.255"},
        {"kind": "address", "text": "1.1.1.1", "start": "1.1.1.1", "end": "1.1.1.1"},
        {"kind": "range", "text": "9.9.9.1-9.9.9.9", "start": "9.9.9.1", "end": "9.9.9.9"},
    ]
    assert second["attestation_id"] != first["attestation_id"]
    assert second["attested_at"] >= first["attested_at"]

    # The current scope is the latest save.
    assert (await client.get(f"/api/v1/projects/{project_id}/scope")).json() == second
    # The history holds both, newest first, and the first one is unchanged (FR-PRJ-5).
    history = (await client.get(f"/api/v1/projects/{project_id}/scope/history")).json()
    assert history["total"] == 2
    assert history["items"] == [second, first]

    # Each change is in the audit log with its attestation.
    await db_session.rollback()
    entries = (
        await db_session.scalars(
            select(AuditEvent).where(AuditEvent.action == "scope.change").order_by(AuditEvent.id)
        )
    ).all()
    assert [entry.details for entry in entries] == [
        {"attestation_id": first["attestation_id"], "entries": first_entries},
        {"attestation_id": second["attestation_id"], "entries": second_entries},
    ]
    assert all(entry.object_id == str(project_id) for entry in entries)


async def test_scope_can_be_emptied_with_an_attestation(
    client: AsyncClient, project_id: int
) -> None:
    await save(client, project_id, ["8.8.8.0/24"])

    emptied = await save(client, project_id, [])

    assert emptied.status_code == 200
    assert emptied.json()["entries"] == []
    assert emptied.json()["attestation_id"] is not None


# --- Validation with ndm.core.scope ---


async def test_rejected_entries_are_reported_on_the_entry_and_nothing_is_saved(
    client: AsyncClient, project_id: int, db_session: AsyncSession
) -> None:
    await save(client, project_id, ["8.8.4.0/24"])

    response = await save(client, project_id, ["8.8.8.0/24", "192.168.1.0/24", "8.8.8.1/24"])

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "scope_rejected"
    assert [(detail["field"], detail["code"]) for detail in error["details"]] == [
        ("entries.1", "non_public"),
        ("entries.2", "malformed"),
    ]
    # The earlier scope is still the current one.
    scope = (await client.get(f"/api/v1/projects/{project_id}/scope")).json()
    assert [entry["text"] for entry in scope["entries"]] == ["8.8.4.0/24"]
    assert await count(db_session, Attestation) == 1
    assert (await audit_actions(db_session)).count("scope.change") == 1


@pytest.mark.parametrize("extra_env", [{"NDM_NEVER_SCAN": "8.8.8.8,9.9.9.0/24"}])
async def test_never_scan_addresses_are_rejected(client: AsyncClient, project_id: int) -> None:
    response = await save(client, project_id, ["8.8.8.0/24", "1.1.1.1", "9.9.9.9"])

    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert [(detail["field"], detail["code"]) for detail in details] == [
        ("entries.0", "never_scan"),
        ("entries.2", "never_scan"),
    ]
    assert (await save(client, project_id, ["8.8.4.0/24", "1.1.1.1"])).status_code == 200


@pytest.mark.parametrize("extra_env", [{"NDM_PROJECT_SCOPE_CEILING": "256"}])
async def test_scope_above_the_ceiling_is_rejected(client: AsyncClient, project_id: int) -> None:
    assert (await save(client, project_id, ["8.8.8.0/24"])).status_code == 200

    response = await save(client, project_id, ["8.8.8.0/24", "1.1.1.1"])

    assert response.status_code == 422
    (detail,) = response.json()["error"]["details"]
    assert (detail["field"], detail["code"]) == (None, "ceiling_exceeded")


@pytest.mark.parametrize("extra_env", [{"NDM_NEVER_SCAN": "8.8.8.8,8.8.8.1/24"}])
def test_api_refuses_to_start_with_an_invalid_never_scan_entry(settings: Settings) -> None:
    with pytest.raises(ScopeError, match="8.8.8.1/24"):
        create_app(settings)


@pytest.mark.parametrize("extra_env", [{"NDM_ALLOW_NON_PUBLIC_TARGETS": "true"}])
async def test_test_switch_allows_a_non_public_scope(client: AsyncClient, project_id: int) -> None:
    assert (await save(client, project_id, ["10.0.0.0/24"])).status_code == 200


# --- Check 1A.6, API half ---


async def test_action_that_rolls_back_leaves_no_audit_entry(
    app: FastAPI,
    client: AsyncClient,
    project_id: int,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    json_log: io.StringIO,
) -> None:
    async def record_then_fail(*args: Any, **kwargs: Any) -> None:
        await record_audit(*args, **kwargs)
        raise RuntimeError("failure after the audit entry was written, before the commit")

    monkeypatch.setattr("ndm.api.routers.projects.record_audit", record_then_fail)
    before = await audit_actions(db_session)

    # A client that turns the unhandled error into the response a browser would get.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://testserver") as failing:
        await log_in(failing)
        logged_in = await audit_actions(db_session)
        scope = await save(failing, project_id, ["8.8.8.0/24"])
        created = await failing.post("/api/v1/projects", json={"name": "Never created"})

    for response in (scope, created):
        assert response.status_code == 500
        # One error shape, with no internals.
        assert response.json() == {
            "error": {
                "code": "internal_error",
                "message": "An internal error occurred.",
                "details": [],
            }
        }

    # The cause is in the log, with its traceback, and nowhere in the response.
    assert json_log.getvalue().count("failure after the audit entry was written") >= 2
    assert "failure after" not in scope.text + created.text

    assert logged_in == [*before, "login"]
    assert await audit_actions(db_session) == logged_in
    assert await count(db_session, Attestation) == 0
    assert await count(db_session, ScopeEntry) == 0
    assert await count(db_session, Project) == 1
