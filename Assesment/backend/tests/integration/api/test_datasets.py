"""The dataset endpoints, the tile configuration, and that no secret is returned."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.messages import DatasetUpdateRequested
from ndm.db.models import AuditEvent, ReferenceDataset, Scan
from tests.integration.api.conftest import audit_actions

LICENSE_KEY = "api-test-licence-key-91c2"
SECRETS = {
    "NDM_MAXMIND_ACCOUNT_ID": "424242",
    "NDM_MAXMIND_LICENSE_KEY": LICENSE_KEY,
}


class RecordingPublisher:
    def __init__(self) -> None:
        self.commands: list[Any] = []

    async def publish(self, command: Any) -> bool:
        self.commands.append(command)
        return True

    async def close(self) -> None:
        pass


@pytest.fixture
def published(app: FastAPI) -> list[Any]:
    publisher = RecordingPublisher()
    app.state.commands = publisher
    return publisher.commands


@pytest.fixture
async def datasets(db_session: AsyncSession) -> None:
    """The rows as the orchestrator leaves them: one loaded, one failing, one not configured."""
    now = datetime.now(UTC)
    db_session.add_all(
        [
            ReferenceDataset(
                name="geolite2-city",
                version="2026-11-13",
                status="ok",
                loaded_at=now - timedelta(days=3, minutes=1),
                checked_at=now - timedelta(hours=2),
            ),
            ReferenceDataset(
                name="geolite2-asn",
                version="2026-10-02",
                status="stale",
                detail="MaxMind refused the account ID or the licence key (GeoLite2-ASN).",
                loaded_at=now - timedelta(days=45),
                checked_at=now - timedelta(hours=2),
            ),
            ReferenceDataset(
                name="rir-objects-arin",
                detail="There is no arin_db.xml in the directory for registry files.",
            ),
        ]
    )
    await db_session.commit()


# --- The list ---


async def test_list_shows_version_age_and_status_of_each_dataset(
    client: AsyncClient, datasets: None
) -> None:
    response = await client.get("/api/v1/datasets")

    assert response.status_code == 200
    city, asn, arin = response.json()
    assert (city["name"], city["version"], city["status"], city["detail"]) == (
        "geolite2-city",
        "2026-11-13",
        "ok",
        None,
    )
    three_days = timedelta(days=3).total_seconds()
    assert three_days + 60 <= city["age_seconds"] < three_days + 120
    assert city["loaded_at"] and city["checked_at"]
    assert city["update_requested"] is False
    # Data that may not be used any more, and why its updates fail.
    assert (asn["status"], asn["version"]) == ("stale", "2026-10-02")
    assert "refused" in asn["detail"]
    # A dataset that was never loaded has no version and no age.
    assert arin == {
        "name": "rir-objects-arin",
        "version": None,
        "status": "missing",
        "detail": "There is no arin_db.xml in the directory for registry files.",
        "loaded_at": None,
        "age_seconds": None,
        "checked_at": None,
        "update_requested": False,
    }


async def test_list_is_empty_before_the_orchestrator_has_run(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/datasets")).json() == []


# --- The trigger ---


async def test_trigger_records_the_request_then_publishes_one_command(
    client: AsyncClient, datasets: None, published: list[Any], db_session: AsyncSession
) -> None:
    response = await client.post("/api/v1/datasets/geolite2-city/update")

    assert response.status_code == 202
    assert response.json()["update_requested"] is True
    # The request is in the database, which is what the orchestrator acts on...
    row = await db_session.scalar(
        select(ReferenceDataset).where(ReferenceDataset.name == "geolite2-city")
    )
    assert row is not None and row.update_requested_at is not None
    # ...and one command says so.
    assert published == [
        DatasetUpdateRequested(dataset="geolite2-city", message_id=published[0].message_id)
    ]
    entry = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "dataset.update_requested")
    )
    assert entry is not None
    assert (entry.actor, entry.object_type, entry.object_id) == (
        "analyst",
        "dataset",
        "geolite2-city",
    )
    assert (await client.get("/api/v1/datasets")).json()[0]["update_requested"] is True


async def test_trigger_repeated_keeps_one_request_and_one_audit_entry(
    client: AsyncClient, datasets: None, published: list[Any], db_session: AsyncSession
) -> None:
    first = await client.post("/api/v1/datasets/geolite2-asn/update")
    second = await client.post("/api/v1/datasets/geolite2-asn/update")

    assert (first.status_code, second.status_code) == (202, 202)
    assert (await audit_actions(db_session)).count("dataset.update_requested") == 1
    # The second command is harmless: it only makes the orchestrator look again.
    assert len(published) == 2


async def test_trigger_works_when_the_command_cannot_be_published(
    client: AsyncClient, datasets: None, db_session: AsyncSession
) -> None:
    """No broker is configured in these tests. The request is recorded all the same."""
    response = await client.post("/api/v1/datasets/geolite2-city/update")

    assert response.status_code == 202
    requested = await db_session.scalar(
        select(ReferenceDataset.update_requested_at).where(ReferenceDataset.name == "geolite2-city")
    )
    assert requested is not None


@pytest.mark.parametrize("name", ["no-such-dataset", "GeoLite2-City", "a b", "x" * 65])
async def test_trigger_for_an_unknown_dataset(
    client: AsyncClient, datasets: None, published: list[Any], name: str
) -> None:
    response = await client.post(f"/api/v1/datasets/{name}/update")

    assert response.status_code in (404, 422)
    assert published == []


# --- Tile configuration (FR-MAP-11, R6) ---


async def test_without_a_tile_source_the_configuration_says_so(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/config")).json()["tiles"] is None


@pytest.mark.parametrize(
    "extra_env",
    [
        {
            "NDM_TILE_URL_TEMPLATE": "https://tiles.example.org/{z}/{x}/{y}.png",
            "NDM_TILE_ATTRIBUTION": "© Example Maps contributors",
        }
    ],
)
async def test_configured_tile_source_is_returned_with_its_attribution(
    client: AsyncClient,
) -> None:
    assert (await client.get("/api/v1/config")).json()["tiles"] == {
        "url_template": "https://tiles.example.org/{z}/{x}/{y}.png",
        "attribution": "© Example Maps contributors",
    }


# --- No endpoint returns a secret ---


@pytest.mark.parametrize("extra_env", [SECRETS])
async def test_no_response_body_holds_a_configured_secret(
    client: AsyncClient,
    app: FastAPI,
    enriched_scan: Scan,
) -> None:
    # The licence key, and the database URL with the password inside it.
    secrets = app.state.settings.secret_values()
    assert LICENSE_KEY in secrets and len(secrets) >= 3
    scan, project = enriched_scan.id, enriched_scan.project_id
    paths = [
        "/api/v1/config",
        "/api/v1/datasets",
        "/api/v1/session",
        "/api/v1/projects",
        f"/api/v1/projects/{project}",
        f"/api/v1/projects/{project}/scope",
        f"/api/v1/projects/{project}/scans",
        f"/api/v1/projects/{project}/hosts",
        f"/api/v1/projects/{project}/map-points",
        f"/api/v1/scans/{scan}",
        f"/api/v1/scans/{scan}/hosts",
        f"/api/v1/scans/{scan}/map-points",
        f"/api/v1/scans/{scan}/hosts/8.8.8.8",
        "/api/v1/audit",
        "/healthz",
    ]
    posts = [
        "/api/v1/datasets/geolite2-city/update",
        f"/api/v1/scans/{scan}/enrich",
        f"/api/v1/scans/{scan}/retry-phase",
    ]

    bodies = {path: await client.get(path) for path in paths}
    bodies |= {path: await client.post(path) for path in posts}

    for path, response in bodies.items():
        assert response.status_code < 500, (path, response.text)
        for secret in secrets:
            assert secret not in response.text, path
        assert "424242" not in response.text, path
    assert sum(response.status_code == 200 for response in bodies.values()) >= 13
