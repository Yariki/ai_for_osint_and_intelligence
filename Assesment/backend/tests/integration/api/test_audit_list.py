"""The audit list endpoint: filterable, paginated, read-only (FR-AUD-3)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from tests.integration.api.conftest import new_client
from tests.integration.api.test_route_auth import registered_routes


async def actions(client: AsyncClient, **params: Any) -> list[str]:
    response = await client.get("/api/v1/audit", params=params)
    assert response.status_code == 200, response.text
    return [item["action"] for item in response.json()["items"]]


@pytest.fixture
async def history(app: FastAPI, client: AsyncClient) -> int:
    """Some audited activity. Returns the identifier of the project it created."""
    async with new_client(app) as stranger:
        await stranger.post("/api/v1/login", json={"username": "analyst", "password": "wrong"})
    project_id: int = (await client.post("/api/v1/projects", json={"name": "Edge"})).json()["id"]
    await client.put(
        f"/api/v1/projects/{project_id}/scope", json={"entries": ["8.8.8.8"], "authorized": True}
    )
    await client.post(f"/api/v1/projects/{project_id}/archive")
    return project_id


ALL = [
    "project.archive",
    "scope.change",
    "project.create",
    "login.failed",
    "login",
    "account.setup",
]


async def test_entries_are_listed_newest_first(client: AsyncClient, history: int) -> None:
    response = await client.get("/api/v1/audit")

    body = response.json()
    assert [item["action"] for item in body["items"]] == ALL
    assert (body["total"], body["limit"], body["offset"]) == (6, 50, 0)
    newest = body["items"][0]
    assert newest.keys() == {
        "id",
        "occurred_at",
        "actor",
        "action",
        "object_type",
        "object_id",
        "details",
    }
    assert (newest["actor"], newest["object_type"], newest["object_id"]) == (
        "analyst",
        "project",
        str(history),
    )
    # Timestamps are UTC (FR-AUD-2).
    assert datetime.fromisoformat(newest["occurred_at"]).utcoffset() == timedelta(0)


async def test_list_is_paginated(client: AsyncClient, history: int) -> None:
    assert await actions(client, limit=2) == ALL[:2]
    assert await actions(client, limit=2, offset=2) == ALL[2:4]
    assert await actions(client, limit=4, offset=4) == ALL[4:]
    assert (await client.get("/api/v1/audit", params={"limit": 2})).json()["total"] == 6

    for params in [{"limit": 0}, {"limit": 201}, {"offset": -1}]:
        assert (await client.get("/api/v1/audit", params=params)).status_code == 422


async def test_list_is_filterable(client: AsyncClient, history: int) -> None:
    assert await actions(client, action="login.failed") == ["login.failed"]
    assert await actions(client, actor="anonymous") == ["login.failed"]
    assert await actions(client, object_type="project", object_id=history) == ALL[:3]
    assert await actions(client, object_type="project", action="scope.change") == ["scope.change"]
    assert await actions(client, action="no.such.action") == []

    now = datetime.now(UTC)
    assert await actions(client, since=(now - timedelta(minutes=1)).isoformat()) == ALL
    assert await actions(client, since=(now + timedelta(minutes=1)).isoformat()) == []
    assert await actions(client, until=(now - timedelta(minutes=1)).isoformat()) == []
    assert (await client.get("/api/v1/audit", params={"since": "yesterday"})).status_code == 422


async def test_audit_log_can_only_be_read_through_the_api(app: FastAPI) -> None:
    methods = {method for method, path in registered_routes(app) if "/audit" in path}

    assert methods == {"GET"}
