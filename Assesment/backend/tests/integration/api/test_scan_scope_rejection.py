"""Check 1B.8, creation part: out-of-scope, non-public and never-scan targets
are rejected at creation, each with an audit entry (FR-PRJ-7, AC 1).

Check 1B.11, creation part: a forced SYN scan is rejected when the capability
is absent.
"""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.scope import parse_never_scan
from ndm.db.models import AuditEvent, Scan
from tests.integration.api.test_scans import launch, scanner

__all__ = ["project_id"]

from tests.integration.api.test_scans import project_id  # noqa: E402  (a fixture)


async def rejections(db: AsyncSession) -> list[dict[str, Any]]:
    await db.rollback()
    entries = await db.scalars(
        select(AuditEvent).where(AuditEvent.action == "scan.rejected").order_by(AuditEvent.id)
    )
    return [
        {"actor": e.actor, "object": (e.object_type, e.object_id), **e.details} for e in entries
    ]


async def scans(db: AsyncSession) -> int:
    await db.rollback()
    return await db.scalar(select(func.count()).select_from(Scan)) or 0


# --- Out of scope ---


@pytest.mark.parametrize(
    "target",
    [
        "8.8.4.4",  # another address
        "8.8.9.0",  # just above the scope
        "8.8.7.255",  # just below it
        "8.8.8.250-8.8.9.5",  # partly outside: rejected as a whole
        "8.8.9.0/24",
    ],
)
async def test_out_of_scope_target_is_rejected_with_an_audit_entry(
    client: AsyncClient, project_id: int, db_session: AsyncSession, target: str
) -> None:
    response = await launch(client, project_id, targets=[target])

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "targets_rejected"
    assert [(d["field"], d["code"]) for d in error["details"]] == [("targets.0", "out_of_scope")]
    assert await scans(db_session) == 0
    assert await rejections(db_session) == [
        {
            "actor": "analyst",
            "object": ("project", str(project_id)),
            "point": "api",
            "rejected": [{"target": target, "reason": "out_of_scope"}],
        }
    ]


async def test_each_rejected_target_is_reported_on_its_entry(
    client: AsyncClient, project_id: int, db_session: AsyncSession
) -> None:
    response = await launch(
        client, project_id, targets=["8.8.8.1", "8.8.4.4", "8.8.8.2", "1.1.1.1"]
    )

    details = response.json()["error"]["details"]
    assert [(d["field"], d["code"]) for d in details] == [
        ("targets.1", "out_of_scope"),
        ("targets.3", "out_of_scope"),
    ]
    # One audit entry for the attempt, naming both targets. Nothing was created
    # for the two targets that were in scope either.
    (entry,) = await rejections(db_session)
    assert [item["target"] for item in entry["rejected"]] == ["8.8.4.4", "1.1.1.1"]
    assert await scans(db_session) == 0


async def test_project_without_a_scope_accepts_no_target(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    bare: int = (await client.post("/api/v1/projects", json={"name": "No scope"})).json()["id"]

    response = await launch(client, bare, targets=["8.8.8.8"])

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["code"] == "out_of_scope"
    assert len(await rejections(db_session)) == 1


async def test_scope_reduced_since_the_last_scan_applies_to_the_next(
    client: AsyncClient, project_id: int, db_session: AsyncSession
) -> None:
    assert (await launch(client, project_id, targets=["8.8.8.200"])).status_code == 201
    await client.put(
        f"/api/v1/projects/{project_id}/scope", json={"entries": ["8.8.8.0/25"], "authorized": True}
    )

    response = await launch(client, project_id, targets=["8.8.8.200"])

    assert response.status_code == 422
    assert await scans(db_session) == 1
    assert len(await rejections(db_session)) == 1


# --- Non-public ---


@pytest.mark.parametrize("target", ["10.0.0.1", "192.168.1.0/24", "127.0.0.1", "100.64.0.1"])
async def test_non_public_target_is_rejected_with_an_audit_entry(
    client: AsyncClient, project_id: int, db_session: AsyncSession, target: str
) -> None:
    response = await launch(client, project_id, targets=[target])

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["code"] == "non_public"
    assert await scans(db_session) == 0
    (entry,) = await rejections(db_session)
    assert entry["rejected"] == [{"target": target, "reason": "non_public"}]


@pytest.mark.parametrize("extra_env", [{"NDM_ALLOW_NON_PUBLIC_TARGETS": "true"}])
async def test_test_switch_lets_the_api_accept_lab_targets(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The API's part of the switch. The orchestrator and the scanner check their own."""
    lab: int = (await client.post("/api/v1/projects", json={"name": "Lab"})).json()["id"]
    await client.put(
        f"/api/v1/projects/{lab}/scope", json={"entries": ["10.77.3.0/24"], "authorized": True}
    )

    assert (await launch(client, lab, targets=["10.77.3.0/24"])).status_code == 201
    # Outside the scope it is still rejected.
    assert (await launch(client, lab, targets=["10.77.4.1"])).status_code == 422


# --- Never-scan list ---


async def test_never_scan_target_is_rejected_with_an_audit_entry(
    app: FastAPI, client: AsyncClient, project_id: int, db_session: AsyncSession
) -> None:
    # The operator put the address on the list after the scope had been saved.
    app.state.never_scan = parse_never_scan(["8.8.8.8", "8.8.8.128/25"])

    single = await launch(client, project_id, targets=["8.8.8.8"])
    block = await launch(client, project_id, targets=["8.8.8.0/24"])
    allowed = await launch(client, project_id, targets=["8.8.8.9"])

    assert (single.status_code, block.status_code, allowed.status_code) == (422, 422, 201)
    assert single.json()["error"]["details"][0]["code"] == "never_scan"
    assert [entry["rejected"][0]["reason"] for entry in await rejections(db_session)] == [
        "never_scan",
        "never_scan",
    ]
    assert await scans(db_session) == 1


# --- Malformed targets are not scope rejections ---


@pytest.mark.parametrize(
    "target", ["8.8.8.1/24", "08.8.8.8", "8.8.8.5-8.8.8.1", "8.8.8.8 ", "host"]
)
async def test_malformed_target_is_refused_as_invalid_input(
    client: AsyncClient, project_id: int, db_session: AsyncSession, target: str
) -> None:
    response = await launch(client, project_id, targets=["8.8.8.1", target])

    assert response.status_code == 422
    assert [(d["field"], d["code"]) for d in response.json()["error"]["details"]] == [
        ("targets.1", "malformed")
    ]
    assert await scans(db_session) == 0
    assert await rejections(db_session) == []


# --- Check 1B.11, creation part ---


async def test_forced_syn_is_rejected_when_the_capability_is_absent(
    client: AsyncClient, project_id: int, db_session: AsyncSession
) -> None:
    # No scanner has reported yet.
    unknown = await launch(client, project_id, technique="syn")
    assert unknown.status_code == 409
    assert unknown.json()["error"]["code"] == "syn_unavailable"

    # A scanner that started without the capability.
    await scanner(db_session, syn=False)
    absent = await launch(client, project_id, technique="syn")
    assert absent.status_code == 409
    assert await scans(db_session) == 0

    # Automatic and connect do not need it.
    assert (await launch(client, project_id, technique="automatic")).status_code == 201
    assert (await launch(client, project_id, technique="connect")).status_code == 201


async def test_forced_syn_is_accepted_when_the_scanner_has_the_capability(
    client: AsyncClient, project_id: int, db_session: AsyncSession
) -> None:
    await scanner(db_session, syn=True)

    response = await launch(client, project_id, technique="syn")

    assert response.status_code == 201
    assert response.json()["technique_requested"] == "syn"
