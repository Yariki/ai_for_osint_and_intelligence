"""The host API: table rows with sorting and paging, and the host detail."""

from collections.abc import Awaitable, Callable
from ipaddress import IPv4Address
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ndm.core.settings import Settings
from ndm.db.models import PortObservation
from ndm.orchestrator.tick import Orchestrator
from tests.integration.orchestrator.conftest import Outbox, run_to_end, scanner_reports
from tests.tools.stub_scanner import load_recorded_scan

WORLD = load_recorded_scan().world

Pipeline = Callable[[list[str]], Awaitable[int]]


@pytest.fixture
def pipeline(
    client: AsyncClient, app_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> Pipeline:
    """Create a scan through the API and run it to completion, with the
    orchestrator's own code and the stub scanner's answers for 8.8.8.1 to 8.8.8.3."""
    monkeypatch.setenv("NDM_VANTAGE_POINT", "test")
    outbox = Outbox()
    orchestrator = Orchestrator(
        sessions=async_sessionmaker(app_engine, expire_on_commit=False),
        settings=Settings(),
        never_scan=[],
        publish_chunk=outbox.publish_chunk,
        publish_cancel=outbox.publish_cancel,
        publish_events=outbox.publish_events,
    )

    async def scanned(targets: list[str]) -> int:
        project_id = (await client.post("/api/v1/projects", json={"name": "Edge"})).json()["id"]
        await client.put(
            f"/api/v1/projects/{project_id}/scope",
            json={"entries": ["8.8.8.0/24"], "authorized": True},
        )
        created = await client.post(
            f"/api/v1/projects/{project_id}/scans",
            json={"targets": targets, "port_profile": "custom", "custom_ports": "22,80,443,8080"},
        )
        assert created.status_code == 201, created.text
        scan_id: int = created.json()["id"]
        await scanner_reports(orchestrator)
        await run_to_end(orchestrator, outbox, WORLD)
        assert (await client.get(f"/api/v1/scans/{scan_id}")).json()["state"] == "completed"
        return scan_id

    return scanned


@pytest.fixture
async def scan_id(pipeline: Pipeline) -> int:
    return await pipeline(["8.8.8.1-8.8.8.4"])


async def rows(client: AsyncClient, scan_id: int, **params: Any) -> list[dict[str, Any]]:
    response = await client.get(f"/api/v1/scans/{scan_id}/hosts", params=params)
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


def row(
    address: str, reachable: bool, open_ports: int, closed: int, filtered: int
) -> dict[str, Any]:
    return {
        "address": address,
        "reachable": reachable,
        "open_port_count": open_ports,
        "closed_count": closed,
        "filtered_count": filtered,
    }


# --- Rows ---


async def test_rows_hold_what_the_scan_found(client: AsyncClient, scan_id: int) -> None:
    response = await client.get(f"/api/v1/scans/{scan_id}/hosts")

    body = response.json()
    assert (body["total"], body["limit"], body["offset"]) == (4, 50, 0)
    # What the scan itself found. The enrichment columns are covered in test_host_filters.py.
    found = [{field: item[field] for field in row("", False, 0, 0, 0)} for item in body["items"]]
    assert found == [
        row("8.8.8.1", True, 2, 2, 0),
        row("8.8.8.2", True, 1, 0, 3),
        row("8.8.8.3", True, 0, 4, 0),
        row("8.8.8.4", False, 0, 0, 0),
    ]


async def test_scan_detail_shows_the_summary_counts(client: AsyncClient, scan_id: int) -> None:
    scan = (await client.get(f"/api/v1/scans/{scan_id}")).json()

    assert (scan["hosts_reachable"], scan["open_ports"]) == (3, 3)
    assert (scan["chunks_done"], scan["chunks_total"], scan["probes_sent"]) == (2, 2, 16)
    assert (scan["technique_used"], scan["vantage_point"], scan["partial"]) == (
        "syn",
        "test",
        False,
    )


@pytest.mark.parametrize(
    ("sort", "order", "expected"),
    [
        ("address", "asc", ["8.8.8.1", "8.8.8.2", "8.8.8.3", "8.8.8.4"]),
        ("address", "desc", ["8.8.8.4", "8.8.8.3", "8.8.8.2", "8.8.8.1"]),
        ("open_ports", "desc", ["8.8.8.1", "8.8.8.2", "8.8.8.3", "8.8.8.4"]),
        ("open_ports", "asc", ["8.8.8.3", "8.8.8.4", "8.8.8.2", "8.8.8.1"]),
        ("closed", "desc", ["8.8.8.3", "8.8.8.1", "8.8.8.2", "8.8.8.4"]),
        ("filtered", "desc", ["8.8.8.2", "8.8.8.1", "8.8.8.3", "8.8.8.4"]),
        ("reachable", "asc", ["8.8.8.4", "8.8.8.1", "8.8.8.2", "8.8.8.3"]),
        ("reachable", "desc", ["8.8.8.1", "8.8.8.2", "8.8.8.3", "8.8.8.4"]),
    ],
)
async def test_sorting(
    client: AsyncClient, scan_id: int, sort: str, order: str, expected: list[str]
) -> None:
    found = await rows(client, scan_id, sort=sort, order=order)

    # Equal values are ordered by address, so the order is always the same.
    assert [item["address"] for item in found] == expected


async def test_addresses_sort_as_addresses_not_as_text(
    client: AsyncClient, pipeline: Pipeline
) -> None:
    scan = await pipeline(["8.8.8.2", "8.8.8.10", "8.8.8.100", "8.8.8.9"])

    found = await rows(client, scan)

    assert [item["address"] for item in found] == ["8.8.8.2", "8.8.8.9", "8.8.8.10", "8.8.8.100"]


async def test_reachable_filter(client: AsyncClient, scan_id: int) -> None:
    discovered = await rows(client, scan_id, reachable="true")
    silent = await rows(client, scan_id, reachable="false")

    assert [item["address"] for item in discovered] == ["8.8.8.1", "8.8.8.2", "8.8.8.3"]
    assert [item["address"] for item in silent] == ["8.8.8.4"]


@pytest.mark.parametrize("sort", ["address", "reachable", "open_ports", "closed", "filtered"])
@pytest.mark.parametrize("order", ["asc", "desc"])
async def test_paging_repeats_and_misses_no_row(
    client: AsyncClient, pipeline: Pipeline, sort: str, order: str
) -> None:
    """256 rows, most of them equal in every column but the address."""
    scan = await pipeline(["8.8.8.0/24"])

    seen: list[str] = []
    for offset in range(0, 300, 37):
        page = await rows(client, scan, sort=sort, order=order, limit=37, offset=offset)
        seen += [item["address"] for item in page]

    assert len(seen) == 256
    assert len(set(seen)) == 256
    # Asking again gives the same order.
    again = await rows(client, scan, sort=sort, order=order, limit=37, offset=37)
    assert [item["address"] for item in again] == seen[37:74]


async def test_invalid_sort_and_unknown_scan(client: AsyncClient, scan_id: int) -> None:
    assert (await client.get(f"/api/v1/scans/{scan_id}/hosts?sort=name")).status_code == 422
    assert (await client.get(f"/api/v1/scans/{scan_id}/hosts?order=up")).status_code == 422
    assert (await client.get("/api/v1/scans/999/hosts")).status_code == 404


# --- Detail ---


async def test_detail_has_evidence_open_ports_and_counts(client: AsyncClient, scan_id: int) -> None:
    response = await client.get(f"/api/v1/scans/{scan_id}/hosts/8.8.8.1")

    assert response.status_code == 200
    host = response.json()
    assert (host["scan_id"], host["address"], host["reachable"]) == (scan_id, "8.8.8.1", True)
    # The probes that showed the host to be reachable, earliest first (FR-SCN-1).
    assert [item["probe"] for item in host["evidence"]] == ["icmp", "tcp:80", "tcp:443", "tcp:22"]
    assert all(item["rtt_ms"] > 0 and item["at"] for item in host["evidence"])
    # Open ports with the nominal name of the port number, which is not a detected service.
    assert host["open_ports"] == [
        {"port": 22, "nominal_service": "ssh"},
        {"port": 443, "nominal_service": "https"},
    ]
    assert (host["closed_count"], host["filtered_count"]) == (2, 0)


async def test_detail_of_a_target_that_did_not_answer(client: AsyncClient, scan_id: int) -> None:
    host = (await client.get(f"/api/v1/scans/{scan_id}/hosts/8.8.8.4")).json()

    assert (host["reachable"], host["evidence"], host["open_ports"]) == (False, [], [])


async def test_port_without_a_registered_name_has_no_nominal_service(
    client: AsyncClient, scan_id: int, db_session: AsyncSession
) -> None:
    await db_session.execute(
        insert(PortObservation).values(scan_id=scan_id, address=IPv4Address("8.8.8.3"), port=61000)
    )
    await db_session.commit()

    host = (await client.get(f"/api/v1/scans/{scan_id}/hosts/8.8.8.3")).json()

    assert host["open_ports"] == [{"port": 61000, "nominal_service": None}]


@pytest.mark.parametrize(
    ("address", "status"),
    [("8.8.8.9", 404), ("9.9.9.9", 404), ("8.8.8.01", 422), ("not-an-address", 422)],
)
async def test_detail_of_something_that_is_not_a_target(
    client: AsyncClient, scan_id: int, address: str, status: int
) -> None:
    assert (await client.get(f"/api/v1/scans/{scan_id}/hosts/{address}")).status_code == status


# --- Hidden with its scan ---


async def test_hosts_of_a_deleted_scan_are_hidden(client: AsyncClient, scan_id: int) -> None:
    await client.delete(f"/api/v1/scans/{scan_id}", params={"confirm": "true"})

    assert (await client.get(f"/api/v1/scans/{scan_id}/hosts")).status_code == 404
    assert (await client.get(f"/api/v1/scans/{scan_id}/hosts/8.8.8.1")).status_code == 404
