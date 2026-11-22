"""Check 2.13 (NFR-PRF-4): host table, each filter and the scan list answer
within 1 second at the sizing envelope; the project-wide view answers within
1 second for 65,536 hosts.

Each request is made three times and the median is compared with the limit,
so that one cold cache does not decide the result. The first request of
each kind also warms the cache, as an analyst's first click would.
"""

import statistics
import time
from typing import Any

import pytest
from httpx import AsyncClient

LIMIT_SECONDS = 1.0
REPEATS = 3

FILTERS: list[dict[str, Any]] = [
    {},
    {"reachable": "true"},
    {"open_port": 443},
    {"asn": 64500},
    {"organization": "Organization 1"},
    {"country": "NL"},
    {"ptr": "host-1"},
    {"open_port": 22, "country": "US", "ptr": "example"},
]


async def timed(client: AsyncClient, path: str, **params: Any) -> tuple[float, Any]:
    durations = []
    body: Any = None
    for _ in range(REPEATS):
        started = time.perf_counter()
        response = await client.get(path, params=params)
        durations.append(time.perf_counter() - started)
        assert response.status_code == 200, response.text
        body = response.json()
    return statistics.median(durations), body


def test_the_database_is_seeded(facts: dict[str, Any]) -> None:
    counts = facts["counts"]
    print(f"\nseeded: {counts}, largest project: {facts['addresses']:,} addresses")
    assert counts["scan"] > 0 and counts["host_observation"] > 0


@pytest.mark.parametrize("params", FILTERS, ids=lambda params: ",".join(params) or "none")
async def test_host_table_of_a_scan(
    client: AsyncClient, facts: dict[str, Any], params: dict[str, Any]
) -> None:
    seconds, body = await timed(client, f"/api/v1/scans/{facts['scan']}/hosts", **params)

    print(f"\nscan host table {params}: {seconds:.3f} s, {body['total']} rows")
    assert seconds <= LIMIT_SECONDS


@pytest.mark.parametrize("sort", ["address", "open_ports", "country", "organization"])
async def test_host_table_sorted(client: AsyncClient, facts: dict[str, Any], sort: str) -> None:
    seconds, _ = await timed(
        client, f"/api/v1/scans/{facts['scan']}/hosts", sort=sort, order="desc", offset=150
    )

    print(f"\nscan host table sorted by {sort}: {seconds:.3f} s")
    assert seconds <= LIMIT_SECONDS


@pytest.mark.parametrize("params", FILTERS, ids=lambda params: ",".join(params) or "none")
async def test_project_wide_view(
    client: AsyncClient, facts: dict[str, Any], params: dict[str, Any]
) -> None:
    seconds, body = await timed(client, f"/api/v1/projects/{facts['project']}/hosts", **params)

    print(
        f"\nproject-wide view of {facts['addresses']:,} addresses {params}: "
        f"{seconds:.3f} s, {body['total']} rows"
    )
    assert seconds <= LIMIT_SECONDS


async def test_project_wide_view_last_page(client: AsyncClient, facts: dict[str, Any]) -> None:
    seconds, _ = await timed(
        client,
        f"/api/v1/projects/{facts['project']}/hosts",
        sort="open_ports",
        offset=max(0, facts["addresses"] - 50),
    )

    print(f"\nproject-wide view, last page: {seconds:.3f} s")
    assert seconds <= LIMIT_SECONDS


async def test_map_points_of_a_scan_and_of_the_project(
    client: AsyncClient, facts: dict[str, Any]
) -> None:
    scan_seconds, _ = await timed(client, f"/api/v1/scans/{facts['scan']}/map-points")
    project_seconds, body = await timed(
        client, f"/api/v1/projects/{facts['project']}/map-points", reachable="true"
    )

    points = len(body["located"]) + len(body["unlocated"])
    print(f"\nmap points: scan {scan_seconds:.3f} s, project {project_seconds:.3f} s ({points})")
    assert scan_seconds <= LIMIT_SECONDS
    assert project_seconds <= LIMIT_SECONDS


async def test_scan_list(client: AsyncClient, facts: dict[str, Any]) -> None:
    seconds, body = await timed(client, f"/api/v1/projects/{facts['project']}/scans", limit=50)

    print(f"\nscan list: {seconds:.3f} s of {body['total']} scans")
    assert seconds <= LIMIT_SECONDS


async def test_host_detail_with_timeline(client: AsyncClient, facts: dict[str, Any]) -> None:
    page = (await client.get(f"/api/v1/scans/{facts['scan']}/hosts", params={"limit": 1})).json()
    address = page["items"][0]["address"]

    seconds, body = await timed(client, f"/api/v1/scans/{facts['scan']}/hosts/{address}")

    print(f"\nhost detail with {len(body['timeline'])} timeline entries: {seconds:.3f} s")
    assert seconds <= LIMIT_SECONDS
