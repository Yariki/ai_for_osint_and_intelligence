"""Check 3.6, API part: a traceroute to an address outside the scope, a
non-public address or one on the never-scan list is refused, nothing is
created, and the attempt is in the audit log."""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import AuditEvent, Scan, TracerouteRun
from tests.integration.api.test_datasets import RecordingPublisher


@pytest.fixture
def published(app: FastAPI) -> list[Any]:
    publisher = RecordingPublisher()
    app.state.commands = publisher
    return publisher.commands


async def refused(
    client: AsyncClient, db: AsyncSession, project: int, target: str
) -> tuple[Any, AuditEvent | None]:
    response = await client.post(f"/api/v1/projects/{project}/traceroutes", json={"target": target})
    await db.commit()
    entry = await db.scalar(
        select(AuditEvent)
        .where(AuditEvent.action == "traceroute.rejected")
        .order_by(AuditEvent.id.desc())
    )
    return response, entry


@pytest.mark.parametrize(
    ("target", "reason"),
    # The scope of the recorded scan runs from 1.0.0.0 to 200.3.13.255.
    [("201.0.0.1", "out_of_scope"), ("223.1.1.1", "out_of_scope"), ("10.0.0.1", "non_public")],
)
async def test_target_outside_the_scope_is_refused_and_audited(
    client: AsyncClient,
    enriched_scan: Scan,
    published: list[Any],
    db_session: AsyncSession,
    target: str,
    reason: str,
) -> None:
    response, entry = await refused(client, db_session, enriched_scan.project_id, target)

    assert response.status_code == 422
    body = response.json()["error"]
    assert (body["code"], body["details"][0]["code"]) == ("target_rejected", reason)
    assert entry is not None
    assert (entry.actor, entry.object_type, entry.object_id) == (
        "analyst",
        "project",
        str(enriched_scan.project_id),
    )
    assert entry.details == {"point": "api", "target": target, "reason": reason}
    assert await db_session.scalar(select(func.count()).select_from(TracerouteRun)) == 0
    assert published == []


@pytest.mark.parametrize("extra_env", [{"NDM_NEVER_SCAN": "8.8.8.8"}])
async def test_target_on_the_never_scan_list_is_refused_and_audited(
    client: AsyncClient, enriched_scan: Scan, db_session: AsyncSession
) -> None:
    response, entry = await refused(client, db_session, enriched_scan.project_id, "8.8.8.8")

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["code"] == "never_scan"
    assert entry is not None and entry.details["reason"] == "never_scan"


async def test_a_project_without_a_scope_has_no_target(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    project = (await client.post("/api/v1/projects", json={"name": "No scope"})).json()["id"]

    response, entry = await refused(client, db_session, project, "8.8.8.8")

    assert response.status_code == 422
    assert entry is not None and entry.details["reason"] == "out_of_scope"


async def test_a_target_inside_the_scope_is_accepted(
    client: AsyncClient, enriched_scan: Scan, db_session: AsyncSession
) -> None:
    response, entry = await refused(client, db_session, enriched_scan.project_id, "8.8.8.200")

    assert response.status_code == 201
    assert entry is None
