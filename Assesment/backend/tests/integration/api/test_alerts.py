"""Alerts (FR-HIS-8): the list, the read state, and the unread count."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.orchestrator import alerts


@pytest.fixture
async def project_id(client: AsyncClient) -> int:
    created: int = (await client.post("/api/v1/projects", json={"name": "Edge"})).json()["id"]
    return created


async def raise_alert(
    db: AsyncSession, project_id: int | None, summary: str, kind: str = alerts.CHANGES_DETECTED
) -> int:
    event = await alerts.raise_alert(
        db, kind, summary, project_id=project_id, link={"diff_id": 1} if project_id else {}
    )
    await db.commit()
    return event.alert_id


async def unread(client: AsyncClient) -> int:
    count: int = (await client.get("/api/v1/alerts/unread-count")).json()["unread"]
    return count


async def listed(client: AsyncClient, **params: Any) -> list[str]:
    page = (await client.get("/api/v1/alerts", params=params)).json()
    return [alert["summary"] for alert in page["items"]]


async def test_alerts_are_listed_newest_first_with_their_project(
    client: AsyncClient, db_session: AsyncSession, project_id: int
) -> None:
    await raise_alert(db_session, project_id, "first")
    await raise_alert(db_session, None, "installation", alerts.MESSAGE_DEAD_LETTERED)
    await raise_alert(db_session, project_id, "third", alerts.SCHEDULED_RUN_SKIPPED)

    page = (await client.get("/api/v1/alerts")).json()

    assert page["total"] == 3
    assert [a["summary"] for a in page["items"]] == ["third", "installation", "first"]
    third, installation, first = page["items"]
    assert (third["type"], third["project_name"], third["read_at"]) == (
        "scheduled_run_skipped",
        "Edge",
        None,
    )
    assert (installation["project_id"], installation["project_name"]) == (None, None)
    assert first["link"] == {"diff_id": 1}
    assert await listed(client, project_id=project_id) == ["third", "first"]


async def test_the_unread_count_follows_the_read_state(
    client: AsyncClient, db_session: AsyncSession, project_id: int
) -> None:
    ids = [await raise_alert(db_session, project_id, f"alert {n}") for n in range(3)]
    assert await unread(client) == 3

    read = await client.post(f"/api/v1/alerts/{ids[0]}/read")
    assert read.status_code == 200
    read_at = read.json()["read_at"]
    assert read_at is not None
    assert await unread(client) == 2
    # Marking it again changes nothing.
    assert (await client.post(f"/api/v1/alerts/{ids[0]}/read")).json()["read_at"] == read_at
    assert await unread(client) == 2
    assert await listed(client, unread=True) == ["alert 2", "alert 1"]

    marked = (await client.post("/api/v1/alerts/read-all")).json()
    assert marked == {"marked": 2}
    assert await unread(client) == 0
    assert await listed(client, unread=True) == []
    assert len(await listed(client)) == 3

    # A new alert is unread.
    await raise_alert(db_session, project_id, "later")
    assert await unread(client) == 1


async def test_alerts_of_a_deleted_project_are_not_shown(
    client: AsyncClient, db_session: AsyncSession, project_id: int
) -> None:
    hidden = await raise_alert(db_session, project_id, "of the deleted project")
    await raise_alert(db_session, None, "installation", alerts.MESSAGE_DEAD_LETTERED)

    deleted = await client.delete(f"/api/v1/projects/{project_id}", params={"confirm": "true"})
    assert deleted.status_code == 204

    assert await listed(client) == ["installation"]
    assert await unread(client) == 1
    assert (await client.post(f"/api/v1/alerts/{hidden}/read")).status_code == 404
    assert (await client.post("/api/v1/alerts/read-all")).json() == {"marked": 1}


async def test_an_unknown_alert_is_not_found(client: AsyncClient) -> None:
    assert (await client.post("/api/v1/alerts/99999/read")).status_code == 404
