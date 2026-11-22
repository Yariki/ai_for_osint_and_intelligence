"""Host table and map points: the same hosts for the same filters (check 2.9)."""

import itertools
from typing import Any

import pytest
from httpx import AsyncClient

from ndm.db.models import Scan, ScanState
from ndm.orchestrator.tick import Orchestrator
from tests.integration.orchestrator.conftest import Outbox, ScanFactory, run_to_end, scanner_reports
from tests.tools.enrichment_scan import REACHABLE, RECORDED, SCOPE


async def table(
    client: AsyncClient, url: str, page_size: int = 200, **params: Any
) -> list[dict[str, Any]]:
    """Every row of a host table, through all its pages."""
    rows: list[dict[str, Any]] = []
    while True:
        response = await client.get(url, params=params | {"limit": page_size, "offset": len(rows)})
        assert response.status_code == 200, response.text
        page = response.json()
        rows += page["items"]
        if len(rows) >= page["total"]:
            assert len(rows) == page["total"]
            return rows


async def points(client: AsyncClient, url: str, **params: Any) -> dict[str, list[dict[str, Any]]]:
    response = await client.get(url, params=params)
    assert response.status_code == 200, response.text
    body: dict[str, list[dict[str, Any]]] = response.json()
    return body


def addresses(rows: list[dict[str, Any]]) -> set[str]:
    return {row["address"] for row in rows}


def hosts_url(scan: Scan) -> str:
    return f"/api/v1/scans/{scan.id}/hosts"


def map_url(scan: Scan) -> str:
    return f"/api/v1/scans/{scan.id}/map-points"


# --- Rows carry what enrichment and reverse DNS found ---


async def test_row_holds_enrichment_and_reverse_dns(
    client: AsyncClient, enriched_scan: Scan
) -> None:
    rows = {row["address"]: row for row in await table(client, hosts_url(enriched_scan))}

    assert len(rows) == 80
    assert rows["8.8.8.8"] == {
        "address": "8.8.8.8",
        "scan_id": enriched_scan.id,
        "reachable": True,
        "open_port_count": 1,
        "closed_count": 0,
        "filtered_count": 3,
        "ptr_names": ["dns.example.net"],
        "asn": 64496,
        "as_holder": "EXAMPLE-DNS",
        "organization": "Example DNS LLC",
        "country": "US",
        "city": "Mountain View",
        "latitude": 37.4056,
        "longitude": -122.0775,
        "accuracy_radius_km": 1000,
        # Not classified here; test_classification_filters.py covers it.
        "device_type": None,
        "sub_role": None,
        "confidence": None,
        "type_override": None,
        "effective_type": None,
        "tags": [],
    }
    # A target that did not answer has a row, and nothing is known about it.
    silent = rows["8.8.8.4"]
    assert (silent["reachable"], silent["asn"], silent["latitude"]) == (False, None, None)
    assert silent["ptr_names"] == []


# --- Each filter ---


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"reachable": "true"}, None),  # the discovered hosts; counted below
        ({"open_port": 22}, {"193.0.6.1", "1.0.0.5", "200.3.13.7"}),
        ({"open_port": 8080}, {"41.0.0.4"}),
        ({"asn": 64497}, {"193.0.6.1", "193.0.6.2", "193.0.6.9"}),
        ({"country": "nl"}, {"193.0.6.1", "193.0.6.2", "193.0.6.9"}),
        ({"country": "AU"}, {"1.0.0.1", "1.0.0.5"}),
        # A part of the name, in any case.
        ({"organization": "example DNS"}, {"8.8.8.1", "8.8.8.2", "8.8.8.3", "8.8.8.8"}),
        ({"organization": "university"}, {"193.0.6.1", "193.0.6.2", "193.0.6.9"}),
        ({"ptr": "EXAMPLE.NET"}, {"8.8.8.1", "8.8.8.8"}),
        ({"ptr": "www."}, {"193.0.6.1", "1.0.0.1"}),
        # LIKE's wildcards are plain characters here.
        ({"organization": "%"}, set()),
        ({"ptr": "_"}, set()),
        # Two filters narrow each other.
        ({"asn": 64496, "open_port": 443, "ptr": "dns"}, {"8.8.8.8"}),
        ({"country": "US", "open_port": 22}, set()),
    ],
)
async def test_filter_selects_the_same_hosts_in_the_table_and_on_the_map(
    client: AsyncClient, enriched_scan: Scan, params: dict[str, Any], expected: set[str] | None
) -> None:
    rows = await table(client, hosts_url(enriched_scan), **params)
    on_map = await points(client, map_url(enriched_scan), **params)

    if expected is None:
        assert len(rows) == REACHABLE
    else:
        assert addresses(rows) == expected
    assert addresses(on_map["located"]) | addresses(on_map["unlocated"]) == addresses(rows)
    assert len(on_map["located"]) + len(on_map["unlocated"]) == len(rows)


OPTIONS: dict[str, list[Any]] = {
    "reachable": [None, "true", "false"],
    "open_port": [None, 22, 443, 853, 9],
    "asn": [None, 64496, 64500, 1],
    "organization": [None, "example", "LLC", "ejemplo"],
    "country": [None, "US", "za", "FR"],
    "ptr": [None, "example", "www"],
}


async def test_table_and_map_agree_for_generated_filter_combinations(
    client: AsyncClient, enriched_scan: Scan
) -> None:
    """Check 2.9. Every combination of the options above: 2,880 pairs of requests
    would be slow, so every pair of filters is combined in full and the rest
    is sampled along the product."""
    names = list(OPTIONS)
    combinations: list[dict[str, Any]] = []
    for first, second in itertools.combinations(names, 2):
        for one, other in itertools.product(OPTIONS[first], OPTIONS[second]):
            combinations.append({first: one, second: other})
    combinations += [
        dict(zip(names, values, strict=True))
        for values in itertools.islice(itertools.product(*OPTIONS.values()), 0, None, 37)
    ]
    selective = 0

    for combination in combinations:
        params = {name: value for name, value in combination.items() if value is not None}
        rows = await table(client, hosts_url(enriched_scan), **params)
        on_map = await points(client, map_url(enriched_scan), **params)

        mapped = [*on_map["located"], *on_map["unlocated"]]
        assert addresses(mapped) == addresses(rows), params
        assert len(mapped) == len(rows), params
        # A located host has coordinates, and the same ones as its table row.
        by_address = {row["address"]: row for row in rows}
        for point in on_map["located"]:
            row = by_address[point["address"]]
            assert (point["latitude"], point["longitude"]) == (row["latitude"], row["longitude"])
        assert all(by_address[host["address"]]["latitude"] is None for host in on_map["unlocated"])
        selective += 0 < len(rows) < 80

    assert len(combinations) > 250
    assert selective > 60  # the combinations do select something


async def test_hosts_without_coordinates_are_listed_and_never_dropped(
    client: AsyncClient, enriched_scan: Scan
) -> None:
    on_map = await points(client, map_url(enriched_scan), reachable="true")

    # The network without a location in the reference data.
    assert addresses(on_map["unlocated"]) == {"200.3.13.2", "200.3.13.7"}
    assert len(on_map["located"]) == REACHABLE - 2
    # Four hosts of one network on one point: the normal case (FR-MAP-2).
    here = [p for p in on_map["located"] if (p["latitude"], p["longitude"]) == (37.4056, -122.0775)]
    assert addresses(here) == {"8.8.8.1", "8.8.8.2", "8.8.8.3", "8.8.8.8"}
    assert here[0]["accuracy_radius_km"] == 1000
    assert {point["scan_id"] for point in on_map["located"]} == {enriched_scan.id}


# --- Sorting and paging with the new columns ---


@pytest.mark.parametrize(
    ("sort", "order", "first", "field"),
    [
        ("country", "asc", "AU", "country"),
        ("country", "desc", "ZA", "country"),
        ("asn", "asc", 64496, "asn"),
        ("asn", "desc", 64500, "asn"),
        ("organization", "asc", "Cliente de Ejemplo", "organization"),
    ],
)
async def test_sorting_by_enrichment_puts_hosts_without_a_value_last(
    client: AsyncClient, enriched_scan: Scan, sort: str, order: str, first: Any, field: str
) -> None:
    # In pages of seven, so that the order must hold from one page to the next.
    rows = await table(client, hosts_url(enriched_scan), page_size=7, sort=sort, order=order)

    values = [row[field] for row in rows]
    known = [value for value in values if value is not None]
    assert values[0] == first
    assert known == sorted(known, reverse=order == "desc")
    # Nothing is known about the 68 targets that did not answer; they come last.
    assert values[len(known) :] == [None] * (len(values) - len(known))
    assert len(rows) == len(addresses(rows)) == 80


@pytest.mark.parametrize(
    "params",
    [
        {"open_port": 0},
        {"open_port": 70000},
        {"asn": -1},
        {"country": "USA"},
        {"country": "1"},
        {"sort": "city"},
        {"reachable": "maybe"},
    ],
)
async def test_invalid_filter_values_are_refused(
    client: AsyncClient, enriched_scan: Scan, params: dict[str, Any]
) -> None:
    for url in (hosts_url(enriched_scan), map_url(enriched_scan)):
        if "sort" in params and "map-points" in url:
            continue
        response = await client.get(url, params=params)
        assert response.status_code == 422, (url, response.text)
        assert response.json()["error"]["code"] == "validation_error"


async def test_empty_text_filters_are_no_filters(client: AsyncClient, enriched_scan: Scan) -> None:
    rows = await table(client, hosts_url(enriched_scan), organization=" ", ptr="")

    assert len(rows) == 80


# --- A scan without enrichment, and scans that are not there ---


async def test_filters_work_on_a_scan_that_has_no_snapshots(
    client: AsyncClient,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    enriching_orchestrator.enricher = None
    scan = await make_scan(state=ScanState.QUEUED, targets=RECORDED.targets, scope=SCOPE)
    await scanner_reports(enriching_orchestrator)
    await run_to_end(enriching_orchestrator, outbox, RECORDED.world)

    assert len(await table(client, hosts_url(scan), reachable="true")) == REACHABLE
    assert await table(client, hosts_url(scan), asn=64496) == []
    assert len(await table(client, hosts_url(scan), open_port=22)) == 3
    on_map = await points(client, map_url(scan), reachable="true")
    assert (len(on_map["located"]), len(on_map["unlocated"])) == (0, REACHABLE)


async def test_map_points_of_an_unknown_or_deleted_scan(
    client: AsyncClient, enriched_scan: Scan
) -> None:
    assert (await client.get("/api/v1/scans/999999/map-points")).status_code == 404

    deleted = await client.delete(f"/api/v1/scans/{enriched_scan.id}", params={"confirm": "true"})
    assert deleted.status_code == 204
    assert (await client.get(map_url(enriched_scan))).status_code == 404
    assert (await client.get(hosts_url(enriched_scan))).status_code == 404
