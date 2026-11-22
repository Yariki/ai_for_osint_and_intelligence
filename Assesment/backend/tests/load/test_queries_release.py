"""Check 5.3 (NFR-PRF-4, NFR-PRF-5): on a database seeded to A7 with every
table filled, every list, filter, timeline and comparison endpoint answers in
time: within 1 second, a comparison within 5 seconds.

test_queries_envelope.py covers the host table, its filters, the scan list
and the project-wide view; this file covers every other read endpoint. Each
request is made three times and the median is compared with the limit.
"""

import statistics
import time
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.load.conftest import report

LIST_SECONDS = 1.0
COMPARE_SECONDS = 5.0
REPEATS = 3
TIMES: dict[str, float] = {}


@pytest.fixture(scope="session")
async def ids(owner: AsyncEngine, facts: dict[str, Any]) -> dict[str, Any]:
    """Identifiers of seeded rows for each endpoint, in the largest project."""
    queries = {
        "schedule": "SELECT min(id) FROM schedule",
        "scheduled_scan": "SELECT max(id) FROM scan "
        "WHERE schedule_id = (SELECT min(id) FROM schedule)",
        "diff": "SELECT max(id) FROM scan_diff",
        "traceroute": "SELECT max(id) FROM traceroute_run WHERE project_id = :project",
        "traceroute_target": "SELECT host(target) FROM traceroute_run WHERE project_id = :project "
        "ORDER BY id DESC LIMIT 1",
        "profile": "SELECT min(id) FROM scan_profile",
        # A host the latest scan found, with a timeline over every scan of its network.
        "address": "SELECT host(address) FROM host_observation WHERE scan_id = :scan "
        "AND reachable ORDER BY address LIMIT 1",
        # Two ordinary scans of one network, the later one the latest scan.
        "base": "SELECT max(id) FROM scan WHERE project_id = :project AND id < :scan "
        "AND targets[1] = (SELECT targets[1] FROM scan WHERE id = :scan)",
    }
    found: dict[str, Any] = {}
    async with owner.connect() as connection:
        for name, query in queries.items():
            found[name] = await connection.scalar(
                text(query), {"project": facts["project"], "scan": facts["scan"]}
            )
    assert all(value is not None for value in found.values()), found
    return found


async def timed(client: AsyncClient, path: str, **params: Any) -> float:
    durations = []
    for _ in range(REPEATS):
        started = time.perf_counter()
        response = await client.get(path, params=params)
        durations.append(time.perf_counter() - started)
        assert response.status_code == 200, f"{path}: {response.text[:300]}"
    seconds = statistics.median(durations)
    TIMES[f"{path} {params or ''}".strip()] = round(seconds, 3)
    return seconds


def endpoints(facts: dict[str, Any], ids: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    project, scan = facts["project"], facts["scan"]
    return [
        ("/api/v1/projects", {}),
        (f"/api/v1/projects/{project}", {}),
        (f"/api/v1/projects/{project}/scope", {}),
        (f"/api/v1/projects/{project}/scope/history", {}),
        (f"/api/v1/projects/{project}/scans", {"limit": 200}),
        (f"/api/v1/scans/{scan}", {}),
        # The host detail holds the timeline of the address over every scan of the project.
        (f"/api/v1/scans/{scan}/hosts/{ids['address']}", {}),
        (f"/api/v1/scans/{scan}/hosts", {"change": "changed"}),
        (f"/api/v1/scans/{scan}/map-points", {"change": "unchanged"}),
        (f"/api/v1/scans/{scan}/baseline", {}),
        (f"/api/v1/projects/{project}/traceroutes", {}),
        (f"/api/v1/projects/{project}/traceroutes", {"target": ids["traceroute_target"]}),
        (f"/api/v1/traceroutes/{ids['traceroute']}", {}),
        (f"/api/v1/projects/{project}/schedules", {}),
        (f"/api/v1/schedules/{ids['schedule']}", {}),
        (f"/api/v1/schedules/{ids['schedule']}/runs", {}),
        (f"/api/v1/scans/{ids['scheduled_scan']}/diff", {}),
        (f"/api/v1/diffs/{ids['diff']}", {}),
        ("/api/v1/alerts", {}),
        ("/api/v1/alerts", {"unread": "true"}),
        ("/api/v1/alerts", {"project_id": project}),
        ("/api/v1/alerts/unread-count", {}),
        ("/api/v1/audit", {}),
        ("/api/v1/audit", {"action": "scan.launch"}),
        ("/api/v1/audit", {"object_type": "scan", "object_id": str(scan)}),
        ("/api/v1/scan-profiles", {}),
        (f"/api/v1/scan-profiles/{ids['profile']}", {}),
        ("/api/v1/datasets", {}),
        ("/api/v1/config", {}),
    ]


async def test_every_list_filter_and_timeline_endpoint_answers_within_1_second(
    client: AsyncClient, facts: dict[str, Any], ids: dict[str, Any]
) -> None:
    slow = {}
    for path, params in endpoints(facts, ids):
        seconds = await timed(client, path, **params)
        if seconds > LIST_SECONDS:
            slow[f"{path} {params}"] = round(seconds, 3)

    report("queries-release", {"counts": facts["counts"], "seconds": TIMES})
    assert slow == {}


async def test_comparing_two_scans_of_the_envelope_answers_within_5_seconds(
    client: AsyncClient, facts: dict[str, Any], ids: dict[str, Any]
) -> None:
    project = facts["project"]
    params = {"base": ids["base"], "target": facts["scan"]}

    seconds = await timed(client, f"/api/v1/projects/{project}/compare", **params)
    exported = await timed(client, f"/api/v1/projects/{project}/compare", **params, format="csv")

    report("comparison-envelope", {"seconds": seconds, "export_seconds": exported})
    assert seconds <= COMPARE_SECONDS
    assert exported <= COMPARE_SECONDS
