"""The change-status filter (FR-MAP-6): relative to the previous scan, the
same set of addresses in the table and on the map (check 2.9 with the new
filter). For a manual scan the previous scan is the latest earlier completed
scan of the project (Q3)."""

# Fixtures imported by name are used as arguments below.
# ruff: noqa: F811

from typing import Any

import pytest
from httpx import AsyncClient

from ndm.db.models import Scan, ScanState
from ndm.orchestrator.tick import Orchestrator
from tests.integration.api.two_scans import AFTER, STATUS, scan_of, two_scans
from tests.integration.orchestrator.conftest import (  # noqa: F401
    Outbox,
    ScanFactory,
    orchestrator,
)


@pytest.fixture
async def pair(
    client: AsyncClient, orchestrator: Orchestrator, outbox: Outbox, make_scan: ScanFactory
) -> tuple[Scan, Scan]:
    return await two_scans(orchestrator, outbox, make_scan)


async def table(client: AsyncClient, scan: Scan, **filters: Any) -> list[str]:
    response = await client.get(f"/api/v1/scans/{scan.id}/hosts", params={"limit": 200} | filters)
    assert response.status_code == 200, response.text
    return [row["address"] for row in response.json()["items"]]


async def map_points(client: AsyncClient, scan: Scan, **filters: Any) -> list[str]:
    body = (await client.get(f"/api/v1/scans/{scan.id}/map-points", params=filters)).json()
    return sorted(point["address"] for point in body["located"] + body["unlocated"])


@pytest.mark.parametrize("status", sorted(STATUS))
async def test_each_status_gives_the_same_hosts_in_the_table_and_on_the_map(
    client: AsyncClient, pair: tuple[Scan, Scan], status: str
) -> None:
    _, second = pair

    rows = await table(client, second, change=status)

    assert rows == STATUS[status]
    assert await map_points(client, second, change=status) == rows


@pytest.mark.parametrize(
    "others",
    [{"reachable": "true"}, {"reachable": "false"}, {"open_port": 443}, {"open_port": 80}],
)
@pytest.mark.parametrize("status", sorted(STATUS))
async def test_the_status_combines_with_the_other_filters(
    client: AsyncClient, pair: tuple[Scan, Scan], status: str, others: dict[str, Any]
) -> None:
    _, second = pair
    alone = set(await table(client, second, **others))

    rows = await table(client, second, change=status, **others)

    assert rows == sorted(alone & set(STATUS[status]))
    assert await map_points(client, second, change=status, **others) == rows


async def test_the_first_scan_of_a_project_has_no_status(
    client: AsyncClient, pair: tuple[Scan, Scan]
) -> None:
    first, _ = pair

    assert (await client.get(f"/api/v1/scans/{first.id}/baseline")).json() == {
        "scan_id": first.id,
        "base_scan_id": None,
    }
    for status in STATUS:
        assert await table(client, first, change=status) == []


async def test_a_manual_scan_is_compared_with_the_latest_earlier_completed_scan(
    client: AsyncClient,
    pair: tuple[Scan, Scan],
    orchestrator: Orchestrator,
    outbox: Outbox,
    make_scan: ScanFactory,
) -> None:
    first, second = pair
    # A cancelled scan in between is passed over.
    cancelled = await make_scan(
        state=ScanState.CANCELLED,
        phase=None,
        partial=True,
        chunks=0,
        project_id=first.project_id,
        attestation_id=first.attestation_id,
    )
    third = await scan_of(orchestrator, outbox, make_scan, AFTER, like=first)

    baseline = (await client.get(f"/api/v1/scans/{third.id}/baseline")).json()

    assert baseline["base_scan_id"] == second.id != cancelled.id
    assert await table(client, third, change="changed") == []
    assert await table(client, third, change="unchanged") == [
        "8.8.8.1",
        "8.8.8.2",
        "8.8.8.3",
        "8.8.8.4",
    ]


async def test_the_whole_project_view_has_no_change_status(
    client: AsyncClient, pair: tuple[Scan, Scan]
) -> None:
    project = pair[0].project_id

    for path in ("hosts", "map-points"):
        response = await client.get(
            f"/api/v1/projects/{project}/{path}", params={"change": "changed"}
        )
        assert response.status_code == 422
        assert response.json()["error"]["details"][0]["code"] == "needs_a_scan"


async def test_an_unknown_status_is_rejected(client: AsyncClient, pair: tuple[Scan, Scan]) -> None:
    response = await client.get(f"/api/v1/scans/{pair[1].id}/hosts", params={"change": "moved"})

    assert response.status_code == 422
