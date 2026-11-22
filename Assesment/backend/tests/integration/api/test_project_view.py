"""The project-wide view (FR-MAP-5): every address of the project as the latest
scan that targeted it saw it."""

from ipaddress import IPv4Address, IPv4Network
from typing import Any

from httpx import AsyncClient

from ndm.db.models import Scan, ScanState
from ndm.orchestrator.tick import Orchestrator
from tests.integration.api.test_host_filters import addresses, points, table
from tests.integration.orchestrator.conftest import Outbox, ScanFactory, run_to_end, scanner_reports
from tests.tools.enrichment_scan import SCOPE, run_phases

# A later scan of one of the networks. Things have changed there.
LATER_NETWORK = "8.8.8.0/28"
LATER_WORLD = {
    # More open ports than before.
    "8.8.8.1": {"ping": True, "open_ports": [22, 53, 443], "closed": "drop"},
    # A host that was not there before.
    "8.8.8.9": {"ping": True, "open_ports": [80], "closed": "reset"},
    # 8.8.8.2, 8.8.8.3 and 8.8.8.8 do not answer any more.
}


async def later_scan(
    first: Scan, orchestrator: Orchestrator, make_scan: ScanFactory, outbox: Outbox
) -> Scan:
    """A second scan in the project of the first, of one network only."""
    outbox.chunks.clear()
    scan = await make_scan(
        state=ScanState.QUEUED,
        targets=list(IPv4Network(LATER_NETWORK)),
        scope=SCOPE,
        project_id=first.project_id,
        attestation_id=first.attestation_id,
    )
    await scanner_reports(orchestrator)
    await run_to_end(orchestrator, outbox, LATER_WORLD)
    await run_phases(orchestrator)
    return scan


def view(scan: Scan) -> str:
    return f"/api/v1/projects/{scan.project_id}/hosts"


def view_map(scan: Scan) -> str:
    return f"/api/v1/projects/{scan.project_id}/map-points"


async def test_view_of_a_project_with_one_scan_is_that_scan(
    client: AsyncClient, enriched_scan: Scan
) -> None:
    in_view = await table(client, view(enriched_scan))
    in_scan = await table(client, f"/api/v1/scans/{enriched_scan.id}/hosts")

    assert in_view == in_scan
    assert len(in_view) == 80


async def test_each_address_is_shown_as_its_latest_scan_saw_it(
    client: AsyncClient,
    enriched_scan: Scan,
    enriching: Orchestrator,
    make_scan: ScanFactory,
    outbox: Outbox,
) -> None:
    later = await later_scan(enriched_scan, enriching, make_scan, outbox)

    rows = {row["address"]: row for row in await table(client, view(enriched_scan))}

    # Still one row per address of the project: 80, not 96.
    assert len(rows) == 80
    in_later_network = {str(address) for address in IPv4Network(LATER_NETWORK)}
    for address, row in rows.items():
        assert row["scan_id"] == (later.id if address in in_later_network else enriched_scan.id)
    # The network that was scanned again shows its new state...
    assert (rows["8.8.8.1"]["reachable"], rows["8.8.8.1"]["open_port_count"]) == (True, 2)
    assert (rows["8.8.8.9"]["reachable"], rows["8.8.8.9"]["open_port_count"]) == (True, 1)
    assert rows["8.8.8.9"]["city"] == "Mountain View"
    # ...including that a host is gone: the latest observation says so.
    gone = rows["8.8.8.8"]
    assert (gone["reachable"], gone["open_port_count"], gone["asn"]) == (False, 0, None)
    assert gone["ptr_names"] == []
    # The networks that were not scanned again are as the first scan left them.
    assert (rows["193.0.6.1"]["reachable"], rows["193.0.6.1"]["open_port_count"]) == (True, 3)
    assert rows["193.0.6.1"]["ptr_names"] == ["web.example.nl", "www.example.nl"]


async def test_filters_and_map_points_follow_the_view(
    client: AsyncClient,
    enriched_scan: Scan,
    enriching: Orchestrator,
    make_scan: ScanFactory,
    outbox: Outbox,
) -> None:
    await later_scan(enriched_scan, enriching, make_scan, outbox)
    cases: list[tuple[dict[str, Any], set[str]]] = [
        ({"reachable": "true", "asn": 64496}, {"8.8.8.1", "8.8.8.9"}),
        # 8.8.8.2, 8.8.8.3 and 8.8.8.8 had 443 open in the earlier scan only.
        ({"open_port": 443}, {"8.8.8.1", "193.0.6.1", "1.0.0.1", "200.3.13.2"}),
        ({"open_port": 22}, {"8.8.8.1", "193.0.6.1", "1.0.0.5", "200.3.13.7"}),
        ({"country": "NL", "ptr": "www"}, {"193.0.6.1"}),
        ({"ptr": "dns.example"}, set()),  # the name belonged to the host that is gone
        ({}, None),  # type: ignore[list-item]
    ]

    for params, expected in cases:
        rows = await table(client, view(enriched_scan), **params)
        on_map = await points(client, view_map(enriched_scan), **params)

        if expected is not None:
            assert addresses(rows) == expected, params
        assert addresses(on_map["located"]) | addresses(on_map["unlocated"]) == addresses(rows)
        assert len(on_map["located"]) + len(on_map["unlocated"]) == len(rows)


async def test_view_sorts_and_pages_like_a_scan(
    client: AsyncClient,
    enriched_scan: Scan,
    enriching: Orchestrator,
    make_scan: ScanFactory,
    outbox: Outbox,
) -> None:
    await later_scan(enriched_scan, enriching, make_scan, outbox)

    rows = await table(client, view(enriched_scan), page_size=9, sort="open_ports", order="desc")

    counts = [row["open_port_count"] for row in rows]
    assert counts == sorted(counts, reverse=True)
    assert len(addresses(rows)) == len(rows) == 80
    by_address = await table(client, view(enriched_scan), page_size=9)
    assert [row["address"] for row in by_address] == [
        str(address) for address in sorted(IPv4Address(row["address"]) for row in rows)
    ]


async def test_deleting_the_latest_scan_brings_back_the_one_before(
    client: AsyncClient,
    enriched_scan: Scan,
    enriching: Orchestrator,
    make_scan: ScanFactory,
    outbox: Outbox,
) -> None:
    before = await table(client, view(enriched_scan))
    later = await later_scan(enriched_scan, enriching, make_scan, outbox)
    assert await table(client, view(enriched_scan)) != before

    deleted = await client.delete(f"/api/v1/scans/{later.id}", params={"confirm": "true"})

    assert deleted.status_code == 204
    assert await table(client, view(enriched_scan)) == before


async def test_deleting_the_only_scan_of_an_address_removes_it_from_the_view(
    client: AsyncClient, enriched_scan: Scan
) -> None:
    await client.delete(f"/api/v1/scans/{enriched_scan.id}", params={"confirm": "true"})

    assert await table(client, view(enriched_scan)) == []
    on_map = await points(client, view_map(enriched_scan))
    assert on_map == {"located": [], "unlocated": []}


async def test_view_of_another_project_holds_only_its_own_hosts(
    client: AsyncClient,
    enriched_scan: Scan,
    enriching: Orchestrator,
    make_scan: ScanFactory,
    outbox: Outbox,
) -> None:
    outbox.chunks.clear()
    other = await make_scan(
        state=ScanState.QUEUED, targets=list(IPv4Network(LATER_NETWORK)), scope=SCOPE
    )
    await scanner_reports(enriching)
    await run_to_end(enriching, outbox, LATER_WORLD)
    await run_phases(enriching)
    assert other.project_id != enriched_scan.project_id

    assert len(await table(client, view(other))) == 16
    # The first project does not see the other project's later scan of the same addresses.
    rows = {row["address"]: row for row in await table(client, view(enriched_scan))}
    assert {row["scan_id"] for row in rows.values()} == {enriched_scan.id}
    assert rows["8.8.8.8"]["reachable"] is True


async def test_view_of_a_project_that_does_not_exist(client: AsyncClient) -> None:
    for path in ("hosts", "map-points"):
        response = await client.get(f"/api/v1/projects/999999/{path}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


async def test_project_without_a_scan_has_an_empty_view(client: AsyncClient) -> None:
    project_id = (await client.post("/api/v1/projects", json={"name": "Empty"})).json()["id"]

    body = (await client.get(f"/api/v1/projects/{project_id}/hosts")).json()

    assert (body["items"], body["total"]) == ([], 0)
