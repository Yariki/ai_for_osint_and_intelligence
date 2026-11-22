"""Comparison: a manual comparison returns the events of the diff function and
stores nothing; a stored diff is returned unchanged after later scans."""

# Fixtures imported by name are used as arguments below.
# ruff: noqa: F811

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import ChangeEvent, Scan, ScanDiff
from ndm.orchestrator import diff_phase
from ndm.orchestrator.diff import compute_diff
from ndm.orchestrator.tick import Orchestrator
from tests.integration.api.two_scans import AFTER, BEFORE, scan_of, two_scans
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


async def compare(client: AsyncClient, base: Scan, target: Scan, project: int | None = None) -> Any:
    return await client.get(
        f"/api/v1/projects/{project or target.project_id}/compare",
        params={"base": base.id, "target": target.id},
    )


async def test_a_comparison_gives_the_events_of_the_diff_function_and_stores_nothing(
    client: AsyncClient, db_session: AsyncSession, pair: tuple[Scan, Scan]
) -> None:
    first, second = pair

    response = await compare(client, first, second)

    assert response.status_code == 200, response.text
    body = response.json()
    expected = await compute_diff(db_session, first.id, second.id)
    assert (body["id"], body["stored"], body["created_at"]) == (None, False, None)
    assert (body["base_scan_id"], body["target_scan_id"]) == (first.id, second.id)
    assert body["summary"] == expected.summary
    assert [(c["address"], c["kind"], c["port"]) for c in body["changes"]] == [
        (str(c.address), c.kind, c.port) for c in expected.changes
    ]
    assert sorted((c["address"], c["kind"], c["port"]) for c in body["changes"]) == [
        ("8.8.8.1", "port_closed", 22),
        ("8.8.8.1", "port_opened", 80),
        ("8.8.8.3", "host_appeared", None),
        ("8.8.8.4", "host_disappeared", None),
    ]
    assert body["compared_ports"] == [[22, 22], [80, 80], [443, 443], [8080, 8080]]
    assert body["not_comparable"] == []
    assert await db_session.scalar(select(func.count()).select_from(ScanDiff)) == 0


async def test_the_comparison_runs_either_way(client: AsyncClient, pair: tuple[Scan, Scan]) -> None:
    first, second = pair

    body = (await compare(client, second, first)).json()

    kinds = sorted((c["address"], c["kind"]) for c in body["changes"])
    assert ("8.8.8.3", "host_disappeared") in kinds and ("8.8.8.4", "host_appeared") in kinds


async def test_scans_of_another_project_are_not_compared(
    client: AsyncClient,
    pair: tuple[Scan, Scan],
    orchestrator: Orchestrator,
    outbox: Outbox,
    make_scan: ScanFactory,
) -> None:
    first, _ = pair
    elsewhere = await scan_of(orchestrator, outbox, make_scan, AFTER)

    response = await compare(client, first, elsewhere, project=first.project_id)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "not_comparable"
    assert (await compare(client, first, elsewhere)).status_code == 422
    missing = await client.get(
        f"/api/v1/projects/{first.project_id}/compare", params={"base": first.id, "target": 99999}
    )
    assert missing.status_code == 404


async def test_a_stored_diff_is_returned_unchanged_after_later_scans(
    client: AsyncClient,
    db_session: AsyncSession,
    pair: tuple[Scan, Scan],
    orchestrator: Orchestrator,
    outbox: Outbox,
    make_scan: ScanFactory,
) -> None:
    first, second = pair
    stored = await diff_phase.store(db_session, await compute_diff(db_session, first.id, second.id))
    await db_session.commit()
    before = (await client.get(f"/api/v1/diffs/{stored.id}")).json()

    # A later scan sees the network as it was at first.
    await scan_of(orchestrator, outbox, make_scan, BEFORE, like=first)

    after = await client.get(f"/api/v1/diffs/{stored.id}")
    assert after.status_code == 200
    assert after.json() == before
    assert (before["id"], before["stored"]) == (stored.id, True)
    assert len(before["changes"]) == 4
    assert before["compared_ports"] == [[22, 22], [80, 80], [443, 443], [8080, 8080]]
    # Computed and stored agree.
    computed = (await compare(client, first, second)).json()
    assert before["changes"] == computed["changes"]
    assert (await client.get(f"/api/v1/scans/{second.id}/diff")).json() == before


async def test_a_diff_of_a_deleted_scan_is_gone(
    client: AsyncClient, db_session: AsyncSession, pair: tuple[Scan, Scan]
) -> None:
    first, second = pair
    stored = await diff_phase.store(db_session, await compute_diff(db_session, first.id, second.id))
    await db_session.commit()

    deleted = await client.delete(f"/api/v1/scans/{second.id}", params={"confirm": "true"})
    assert deleted.status_code == 204

    assert (await client.get(f"/api/v1/diffs/{stored.id}")).status_code == 404
    assert (await client.get("/api/v1/diffs/99999")).status_code == 404
    assert await db_session.scalar(select(func.count()).select_from(ChangeEvent)) == 4


async def test_a_scan_without_an_automatic_diff_says_so(
    client: AsyncClient, pair: tuple[Scan, Scan]
) -> None:
    response = await client.get(f"/api/v1/scans/{pair[1].id}/diff")

    assert response.status_code == 404
