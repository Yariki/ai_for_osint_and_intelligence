"""Projects: create, rename, describe, archive, delete. Check 1A.7: project states."""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import AuditEvent, Project
from tests.integration.api.conftest import audit_actions

SCOPE = {"entries": ["8.8.8.0/24"], "authorized": True}


async def create(client: AsyncClient, name: str = "Edge routers") -> int:
    response = await client.post("/api/v1/projects", json={"name": name})
    assert response.status_code == 201, response.text
    project_id: int = response.json()["id"]
    return project_id


# --- Create, read, rename, describe ---


async def test_create_and_read(client: AsyncClient, db_session: AsyncSession) -> None:
    created = await client.post(
        "/api/v1/projects", json={"name": "Edge routers", "description": "Quarterly review"}
    )

    assert created.status_code == 201
    project = created.json()
    assert (project["name"], project["description"], project["state"]) == (
        "Edge routers",
        "Quarterly review",
        "active",
    )
    assert (await client.get(f"/api/v1/projects/{project['id']}")).json() == project

    entry = await db_session.scalar(select(AuditEvent).where(AuditEvent.action == "project.create"))
    assert entry is not None
    assert (entry.actor, entry.object_type, entry.object_id) == (
        "analyst",
        "project",
        str(project["id"]),
    )


async def test_list_is_paginated(client: AsyncClient) -> None:
    ids = [await create(client, f"Project {number}") for number in range(3)]

    first = (await client.get("/api/v1/projects", params={"limit": 2})).json()
    rest = (await client.get("/api/v1/projects", params={"limit": 2, "offset": 2})).json()

    assert (first["total"], first["limit"], first["offset"]) == (3, 2, 0)
    assert [item["id"] for item in first["items"]] == ids[:2]
    assert [item["id"] for item in rest["items"]] == ids[2:]


async def test_rename_and_describe(client: AsyncClient) -> None:
    project_id = await create(client)

    renamed = await client.patch(f"/api/v1/projects/{project_id}", json={"name": "Core routers"})
    described = await client.patch(
        f"/api/v1/projects/{project_id}", json={"description": "Backbone only"}
    )

    assert renamed.json()["name"] == "Core routers"
    # A field that is left out keeps its value.
    assert (described.json()["name"], described.json()["description"]) == (
        "Core routers",
        "Backbone only",
    )


async def test_invalid_input_is_rejected(client: AsyncClient) -> None:
    assert (await client.post("/api/v1/projects", json={"name": ""})).status_code == 422
    assert (await client.post("/api/v1/projects", json={})).status_code == 422
    assert (await client.get("/api/v1/projects/not-a-number")).status_code == 422
    assert (await client.get("/api/v1/projects/999")).status_code == 404


# --- Check 1A.7: archived ---


async def test_archived_project_rejects_writes_and_stays_readable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    project_id = await create(client)
    assert (await client.put(f"/api/v1/projects/{project_id}/scope", json=SCOPE)).status_code == 200

    archived = await client.post(f"/api/v1/projects/{project_id}/archive")
    assert archived.status_code == 200
    assert archived.json()["state"] == "archived"

    writes = [
        await client.patch(f"/api/v1/projects/{project_id}", json={"name": "Renamed"}),
        await client.put(
            f"/api/v1/projects/{project_id}/scope",
            json={"entries": ["9.9.9.9"], "authorized": True},
        ),
        await client.post(f"/api/v1/projects/{project_id}/archive"),
    ]
    for response in writes:
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "project_archived"

    # Nothing changed, and everything can still be read.
    project = (await client.get(f"/api/v1/projects/{project_id}")).json()
    scope = (await client.get(f"/api/v1/projects/{project_id}/scope")).json()
    history = (await client.get(f"/api/v1/projects/{project_id}/scope/history")).json()
    assert project["name"] == "Edge routers"
    assert [entry["text"] for entry in scope["entries"]] == ["8.8.8.0/24"]
    assert history["total"] == 1
    assert (await audit_actions(db_session)).count("project.archive") == 1


# --- Check 1A.7: deleted ---


async def test_deletion_needs_explicit_confirmation(client: AsyncClient) -> None:
    project_id = await create(client)

    for params in [{}, {"confirm": "false"}]:
        response = await client.delete(f"/api/v1/projects/{project_id}", params=params)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "confirmation_required"

    assert (await client.get(f"/api/v1/projects/{project_id}")).status_code == 200


async def test_deleted_project_disappears_from_every_endpoint_and_its_audit_entries_remain(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    kept_id = await create(client, "Kept")
    project_id = await create(client)
    await client.put(f"/api/v1/projects/{project_id}/scope", json=SCOPE)

    deleted = await client.delete(f"/api/v1/projects/{project_id}", params={"confirm": "true"})
    assert deleted.status_code == 204

    base = f"/api/v1/projects/{project_id}"
    gone = [
        await client.get(base),
        await client.patch(base, json={"name": "Back"}),
        await client.post(f"{base}/archive"),
        await client.delete(base, params={"confirm": "true"}),
        await client.get(f"{base}/scope"),
        await client.put(f"{base}/scope", json=SCOPE),
        await client.get(f"{base}/scope/history"),
    ]
    assert [response.status_code for response in gone] == [404] * len(gone)
    listed = (await client.get("/api/v1/projects")).json()
    assert (listed["total"], [item["id"] for item in listed["items"]]) == (1, [kept_id])

    # Hidden at once, purged later (I3): the row is still there, marked as deleted.
    row = await db_session.scalar(select(Project).where(Project.id == project_id))
    assert row is not None and row.deleted_at is not None

    # The audit entries of the project are still there and can still be read.
    audit = await client.get(
        "/api/v1/audit", params={"object_type": "project", "object_id": str(project_id)}
    )
    assert [item["action"] for item in audit.json()["items"]] == [
        "project.delete",
        "scope.change",
        "project.create",
    ]


async def test_archived_project_can_be_deleted(client: AsyncClient) -> None:
    project_id = await create(client)
    await client.post(f"/api/v1/projects/{project_id}/archive")

    response = await client.delete(f"/api/v1/projects/{project_id}", params={"confirm": "true"})

    assert response.status_code == 204
    assert (await client.get(f"/api/v1/projects/{project_id}")).status_code == 404
