"""Scan profiles (FR-CFG-8): named parameter sets, checked like a scan, audited."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import AuditEvent
from tests.integration.api.conftest import audit_actions

PARAMETERS: dict[str, Any] = {
    "port_profile": "custom",
    "custom_ports": "22,80,443,8000-8010",
    "discovery": None,
    "technique": "connect",
    "pps": 2000,
    "timeout_ms": 1000,
    "retries": 2,
}


async def create(client: AsyncClient, name: str = "Web edge", **parameters: Any) -> Any:
    return await client.post(
        "/api/v1/scan-profiles", json={"name": name, "parameters": PARAMETERS | parameters}
    )


async def test_a_profile_is_created_with_every_parameter_filled_in(client: AsyncClient) -> None:
    response = await create(client)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Web edge"
    assert body["port_count"] == 14
    assert body["parameters"] == PARAMETERS | {"scan_all_targets": False, "hosts_in_parallel": 64}
    assert (await client.get(f"/api/v1/scan-profiles/{body['id']}")).json() == body


async def test_profiles_are_listed_by_name(client: AsyncClient) -> None:
    for name in ("web edge", "Mail", "all ports"):
        assert (await create(client, name)).status_code == 201

    listed = (await client.get("/api/v1/scan-profiles")).json()

    assert [profile["name"] for profile in listed] == ["all ports", "Mail", "web edge"]


@pytest.mark.parametrize(
    ("parameters", "field"),
    [
        ({"pps": 999_999}, "parameters.pps"),  # above the operator ceiling
        ({"timeout_ms": 50}, "parameters.timeout_ms"),
        ({"retries": 9}, "parameters.retries"),
        ({"custom_ports": "80,70000"}, "parameters"),
        ({"port_profile": "top-100"}, "parameters"),  # custom ports without the custom profile
        ({"discovery": {"icmp_echo": False, "tcp_ports": []}}, "parameters.discovery"),
    ],
)
async def test_a_profile_is_checked_like_a_scan(
    client: AsyncClient, parameters: dict[str, Any], field: str
) -> None:
    response = await create(client, **parameters)

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == field
    assert (await client.get("/api/v1/scan-profiles")).json() == []


async def test_a_name_is_used_once(client: AsyncClient) -> None:
    await create(client)
    other = (await create(client, "Other")).json()

    assert (await create(client)).status_code == 409
    renamed = await client.put(
        f"/api/v1/scan-profiles/{other['id']}", json={"name": "Web edge", "parameters": PARAMETERS}
    )
    assert renamed.status_code == 409
    assert renamed.json()["error"]["code"] == "name_taken"


async def test_a_profile_is_updated_and_deleted_with_audit_entries(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    created = (await create(client)).json()

    updated = await client.put(
        f"/api/v1/scan-profiles/{created['id']}",
        json={"name": "Web edge, fast", "parameters": PARAMETERS | {"pps": 4000}},
    )
    assert updated.status_code == 200
    assert (updated.json()["name"], updated.json()["parameters"]["pps"]) == ("Web edge, fast", 4000)
    assert updated.json()["updated_at"] > created["updated_at"]

    assert (await client.delete(f"/api/v1/scan-profiles/{created['id']}")).status_code == 204
    assert (await client.get(f"/api/v1/scan-profiles/{created['id']}")).status_code == 404

    actions = await audit_actions(db_session)
    assert actions[-3:] == ["profile.create", "profile.update", "profile.delete"]
    change = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "profile.update")
    )
    assert change is not None
    assert change.actor == "analyst"
    assert change.details["before"]["parameters"]["pps"] == 2000
    assert change.details["after"]["parameters"]["pps"] == 4000


async def test_an_unknown_profile_is_not_found(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/scan-profiles/999")).status_code == 404
    assert (await client.delete("/api/v1/scan-profiles/999")).status_code == 404
