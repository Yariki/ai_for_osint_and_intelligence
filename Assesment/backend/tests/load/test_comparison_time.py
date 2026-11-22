"""Check 4.12 (NFR-PRF-5): comparing two 256-address scans with the largest
realistic port counts completes within 5 seconds.

The seeding tool writes the pair at every scale: two top-1000 scans of one
/24 in which every address answers on every port (question Q1), 256,000 open
ports per scan. In the later scan every second address closed half of its
ports and moved to another city under a new dataset version, which gives the
largest diff such a pair can have: 64,000 closed ports and 128 enrichment
changes. The time is that of the whole request, the response included.
"""

import statistics
import time
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

LIMIT_SECONDS = 5.0
REPEATS = 3


@pytest.fixture(scope="session")
async def pair(owner: AsyncEngine) -> dict[str, int]:
    async with owner.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT id, project_id FROM scan "
                    "WHERE parameters->>'seeded' = 'comparison' ORDER BY id"
                )
            )
        ).all()
        ports = await connection.scalar(
            text("SELECT count(*) FROM port_observation WHERE scan_id = :scan"),
            {"scan": rows[0][0]} if rows else {"scan": 0},
        )
    assert len(rows) == 2, "the comparison pair is missing: seed with the current seed_envelope.py"
    assert ports == 256 * 1000
    return {"base": rows[0][0], "target": rows[1][0], "project": rows[0][1]}


async def test_comparing_the_largest_pair_takes_at_most_5_seconds(
    client: AsyncClient, pair: dict[str, int]
) -> None:
    durations = []
    body: Any = None
    for _ in range(REPEATS):
        started = time.perf_counter()
        response = await client.get(
            f"/api/v1/projects/{pair['project']}/compare",
            params={"base": pair["base"], "target": pair["target"]},
        )
        durations.append(time.perf_counter() - started)
        assert response.status_code == 200, response.text
        body = response.json()
    seconds = statistics.median(durations)

    summary = body["summary"]
    print(f"\ncomparison: {seconds:.2f} s (each: {[round(d, 2) for d in durations]}), {summary}")
    assert summary["port_closed"] == 128 * 500
    assert summary["enrichment_changed"] == 128
    assert summary["compared_ports"] == 1000 and summary["compared_addresses"] == 256
    assert all(c["dataset_change"] for c in body["changes"] if c["kind"] == "enrichment_changed")
    assert seconds <= LIMIT_SECONDS


async def test_exporting_the_largest_diff_takes_at_most_5_seconds(
    client: AsyncClient, pair: dict[str, int]
) -> None:
    started = time.perf_counter()
    response = await client.get(
        f"/api/v1/projects/{pair['project']}/compare",
        params={"base": pair["base"], "target": pair["target"], "format": "csv"},
    )
    seconds = time.perf_counter() - started

    assert response.status_code == 200
    lines = response.text.count("\n")
    print(f"\ncomparison export: {seconds:.2f} s, {lines} lines")
    assert lines == 1 + 128 * 500 + 128
    assert seconds <= LIMIT_SECONDS
