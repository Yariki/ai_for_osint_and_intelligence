"""Check 4.11: host, diff and traceroute exports parse as CSV and as JSON,
contain the same rows as the filtered table query, and carry scan identifier,
scan time, vantage point and dataset versions. A cell that would be a formula
is exported as text. Exports need a session."""

# Fixtures imported by name are used as arguments below.
# ruff: noqa: F811

import csv
import io
import json
from ipaddress import IPv4Address
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.api.export import cell, escape
from ndm.db.models import (
    EnrichmentSnapshot,
    HostObservation,
    ProjectHost,
    Scan,
    TracerouteProbe,
    TracerouteResponder,
    TracerouteRun,
    TracerouteState,
    VantagePoint,
)
from ndm.orchestrator import diff_phase
from ndm.orchestrator.diff import compute_diff
from ndm.orchestrator.tick import Orchestrator
from tests.integration.api.two_scans import two_scans
from tests.integration.orchestrator.conftest import (  # noqa: F401
    Outbox,
    ScanFactory,
    orchestrator,
)

FORMULA_PTR = '=HYPERLINK("http://attacker.example","click")'


def parse_csv(response: Any) -> list[dict[str, str]]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"].startswith("attachment; filename=")
    return list(csv.DictReader(io.StringIO(response.text)))


def parse_json(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/json"
    document: dict[str, Any] = json.loads(response.text)
    return document


async def table(client: AsyncClient, url: str, **params: Any) -> list[str]:
    """Every address of the paged table, page after page."""
    addresses: list[str] = []
    while True:
        page = (
            await client.get(url, params=params | {"limit": 50, "offset": len(addresses)})
        ).json()
        addresses += [row["address"] for row in page["items"]]
        if len(addresses) >= page["total"]:
            return addresses


async def expected_metadata(db: AsyncSession, scan: Scan) -> dict[str, Any]:
    stored = await db.get_one(Scan, scan.id, populate_existing=True)
    vantage = await db.get_one(VantagePoint, stored.vantage_point_id)
    assert stored.started_at is not None
    return {
        "scan_id": stored.id,
        "scan_time": stored.started_at.isoformat(),
        "vantage_point": vantage.name,
        "dataset_versions": stored.dataset_versions,
        "icmp_discovery_unavailable": stored.icmp_discovery_unavailable,
    }


# --- Hosts ---


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"reachable": "true", "sort": "asn", "order": "desc"},
        {"open_port": 443},
        {"country": "NL"},
        {"ptr": "example", "sort": "open_ports", "order": "desc"},
    ],
)
async def test_the_host_export_has_the_rows_of_the_filtered_table_and_the_metadata(
    client: AsyncClient, db_session: AsyncSession, enriched_scan: Scan, params: dict[str, Any]
) -> None:
    url = f"/api/v1/scans/{enriched_scan.id}/hosts"
    rows = await table(client, url, **params)
    meta = await expected_metadata(db_session, enriched_scan)
    assert meta["dataset_versions"], "the enriched scan records its dataset versions"

    as_csv = parse_csv(await client.get(url, params=params | {"format": "csv"}))
    as_json = parse_json(await client.get(url, params=params | {"format": "json"}))

    assert [row["address"] for row in as_csv] == rows
    assert [row["address"] for row in as_json["rows"]] == rows
    assert rows, "the filter should leave some hosts"
    for row in as_csv:
        assert row["scan_id"] == str(meta["scan_id"])
        assert row["scan_time"] == meta["scan_time"]
        assert row["vantage_point"] == meta["vantage_point"]
        assert json.loads(row["dataset_versions"]) == meta["dataset_versions"]
        assert row["icmp_discovery_unavailable"] == "false"
    [scan_meta] = as_json["metadata"]["scans"]
    assert {key: scan_meta[key] for key in meta} == meta
    assert as_json["metadata"]["scan_id"] == enriched_scan.id
    assert as_json["metadata"]["rows"] == len(rows)
    first = as_json["rows"][0]
    assert {key: first[key] for key in meta} == meta


async def test_a_scan_without_icmp_discovery_says_so_on_its_page_and_in_every_row(
    client: AsyncClient, db_session: AsyncSession, enriched_scan: Scan
) -> None:
    """TASK-079: the product owner's decision on check 1B.11."""
    await db_session.execute(
        update(Scan).where(Scan.id == enriched_scan.id).values(icmp_discovery_unavailable=True)
    )
    await db_session.commit()
    url = f"/api/v1/scans/{enriched_scan.id}/hosts"

    scan = (await client.get(f"/api/v1/scans/{enriched_scan.id}")).json()
    as_csv = parse_csv(await client.get(url, params={"format": "csv"}))
    as_json = parse_json(await client.get(url, params={"format": "json"}))

    assert scan["icmp_discovery_unavailable"] is True
    assert as_csv and {row["icmp_discovery_unavailable"] for row in as_csv} == {"true"}
    assert {row["icmp_discovery_unavailable"] for row in as_json["rows"]} == {True}
    assert as_json["metadata"]["scans"][0]["icmp_discovery_unavailable"] is True


async def test_the_host_export_holds_what_the_detail_shows(
    client: AsyncClient, enriched_scan: Scan
) -> None:
    url = f"/api/v1/scans/{enriched_scan.id}/hosts"
    rows = parse_json(await client.get(url, params={"format": "json", "reachable": "true"}))
    by_address = {row["address"]: row for row in rows["rows"]}

    for address in list(by_address)[:5]:
        detail = (await client.get(f"/api/v1/scans/{enriched_scan.id}/hosts/{address}")).json()
        row = by_address[address]
        assert row["open_ports"] == [port["port"] for port in detail["open_ports"]]
        assert row["asn"] == detail["enrichment"]["asn"]
        assert row["ptr_names"] == detail["reverse_dns"]["names"]


async def test_a_cell_that_would_be_a_formula_is_exported_as_text(
    client: AsyncClient, db_session: AsyncSession, enriched_scan: Scan
) -> None:
    address = IPv4Address("8.8.8.8")
    await db_session.execute(
        update(HostObservation)
        .where(HostObservation.scan_id == enriched_scan.id, HostObservation.address == address)
        .values(ptr_names=[FORMULA_PTR])
    )
    await db_session.execute(
        update(ProjectHost)
        .where(ProjectHost.project_id == enriched_scan.project_id, ProjectHost.address == address)
        .values(notes="@SUM(A1:A9)", tags=["-1+1", "edge"])
    )
    await db_session.commit()
    url = f"/api/v1/scans/{enriched_scan.id}/hosts"

    [row] = parse_csv(await client.get(url, params={"format": "csv", "ptr": "attacker"}))
    [raw] = parse_json(await client.get(url, params={"format": "json", "ptr": "attacker"}))["rows"]

    assert row["ptr_names"] == "'" + FORMULA_PTR
    assert row["notes"] == "'@SUM(A1:A9)"
    assert row["tags"] == "'-1+1;edge"
    # JSON is not a spreadsheet: the values are as they are.
    assert (raw["ptr_names"], raw["notes"], raw["tags"]) == (
        [FORMULA_PTR],
        "@SUM(A1:A9)",
        ["-1+1", "edge"],
    )


@pytest.mark.parametrize("start", ["=", "+", "-", "@", "\t", "\r"])
def test_every_formula_character_is_escaped(start: str) -> None:
    assert escape(f"{start}1+1") == f"'{start}1+1"
    assert cell([f"{start}x", "y"]) == f"'{start}x;y"


def test_numbers_and_plain_text_are_left_alone() -> None:
    assert (cell(-5), cell(2.5), cell(True), cell(None)) == ("-5", "2.5", "true", "")
    assert cell("dns.example.net") == "dns.example.net"
    assert cell({"b": 1, "a": "=x"}) == '{"a": "=x", "b": 1}'


async def test_the_project_export_has_the_rows_of_the_project_table(
    client: AsyncClient, enriched_scan: Scan
) -> None:
    url = f"/api/v1/projects/{enriched_scan.project_id}/hosts"
    rows = await table(client, url, reachable="true")

    as_csv = parse_csv(await client.get(url, params={"format": "csv", "reachable": "true"}))
    as_json = parse_json(await client.get(url, params={"format": "json", "reachable": "true"}))

    assert [row["address"] for row in as_csv] == rows
    assert [row["address"] for row in as_json["rows"]] == rows
    assert [scan["scan_id"] for scan in as_json["metadata"]["scans"]] == [enriched_scan.id]
    assert {row["scan_id"] for row in as_csv} == {str(enriched_scan.id)}


async def test_an_empty_export_still_says_which_scan_it_is_of(
    client: AsyncClient, enriched_scan: Scan
) -> None:
    url = f"/api/v1/scans/{enriched_scan.id}/hosts"

    as_json = parse_json(await client.get(url, params={"format": "json", "open_port": 1}))
    as_csv = await client.get(url, params={"format": "csv", "open_port": 1})

    assert as_json["rows"] == []
    assert as_json["metadata"]["scans"][0]["scan_id"] == enriched_scan.id
    assert as_csv.text.splitlines()[0].startswith("scan_id,scan_time,vantage_point")


# --- Diffs ---


@pytest.fixture
async def pair(
    client: AsyncClient, orchestrator: Orchestrator, outbox: Outbox, make_scan: ScanFactory
) -> tuple[Scan, Scan]:
    return await two_scans(orchestrator, outbox, make_scan)


async def test_the_diff_export_has_the_changes_and_both_scans(
    client: AsyncClient, db_session: AsyncSession, pair: tuple[Scan, Scan]
) -> None:
    first, second = pair
    url = f"/api/v1/projects/{first.project_id}/compare"
    params = {"base": first.id, "target": second.id}
    shown = (await client.get(url, params=params)).json()

    as_csv = parse_csv(await client.get(url, params=params | {"format": "csv"}))
    as_json = parse_json(await client.get(url, params=params | {"format": "json"}))

    expected = [(c["address"], c["kind"], c["port"]) for c in shown["changes"]]
    assert [(r["address"], r["kind"], int(r["port"]) if r["port"] else None) for r in as_csv] == (
        expected
    )
    assert [(r["address"], r["kind"], r["port"]) for r in as_json["rows"]] == expected
    base = await expected_metadata(db_session, first)
    target = await expected_metadata(db_session, second)
    for row in as_csv:
        assert (row["base_scan_id"], row["target_scan_id"]) == (str(first.id), str(second.id))
        assert (row["base_scan_time"], row["target_scan_time"]) == (
            base["scan_time"],
            target["scan_time"],
        )
        assert row["target_vantage_point"] == target["vantage_point"]
    metadata = as_json["metadata"]
    assert {key: metadata["base"][key] for key in base} == base
    assert {key: metadata["target"][key] for key in target} == target
    assert metadata["summary"] == shown["summary"]
    assert metadata["compared_ports"] == shown["compared_ports"]


async def test_a_stored_diff_is_exported_as_stored(
    client: AsyncClient, db_session: AsyncSession, pair: tuple[Scan, Scan]
) -> None:
    first, second = pair
    stored = await diff_phase.store(db_session, await compute_diff(db_session, first.id, second.id))
    await db_session.commit()

    for url in (f"/api/v1/diffs/{stored.id}", f"/api/v1/scans/{second.id}/diff"):
        as_json = parse_json(await client.get(url, params={"format": "json"}))
        as_csv = parse_csv(await client.get(url, params={"format": "csv"}))
        assert as_json["metadata"]["diff_id"] == stored.id
        assert len(as_json["rows"]) == len(as_csv) == 4
        assert {row["diff_id"] for row in as_csv} == {str(stored.id)}


# --- Traceroutes ---


@pytest.fixture
async def run(client: AsyncClient, db_session: AsyncSession, enriched_scan: Scan) -> TracerouteRun:
    """A finished ICMP traceroute: a private hop, a public hop, a silent hop, the target."""
    vantage = await db_session.scalar(select(VantagePoint))
    assert vantage is not None
    created = TracerouteRun(
        project_id=enriched_scan.project_id,
        attestation_id=enriched_scan.attestation_id,
        vantage_point_id=vantage.id,
        target=IPv4Address("8.8.8.8"),
        method="icmp",
        parameters={"port": None, "max_hops": 30, "probes_per_hop": 2, "timeout_ms": 1000},
        state=TracerouteState.COMPLETED,
        reached=True,
    )
    db_session.add(created)
    await db_session.flush()
    hops = {1: "192.168.1.1", 2: "193.0.6.1", 3: None, 4: "8.8.8.8"}
    for ttl, address in hops.items():
        for probe in (0, 1):
            db_session.add(
                TracerouteProbe(
                    run_id=created.id,
                    ttl=ttl,
                    probe=probe,
                    responder=IPv4Address(address) if address else None,
                    rtt_ms=1.5 * ttl + probe if address else None,
                    reply_kind=("echo_reply" if ttl == 4 else "time_exceeded") if address else None,
                )
            )
        if address:
            db_session.add(
                TracerouteResponder(
                    run_id=created.id,
                    address=IPv4Address(address),
                    unlocatable=address.startswith("192.168."),
                    ptr_names=["=cmd|' /C calc'!A0"] if ttl == 2 else [],
                    ptr_status="ok" if ttl == 2 else "no_name",
                )
            )
    for address, asn in (("193.0.6.1", 3333), ("8.8.8.8", 15169)):
        db_session.add(
            EnrichmentSnapshot(
                traceroute_run_id=created.id,
                address=IPv4Address(address),
                asn=asn,
                city="Amsterdam" if asn == 3333 else None,
                dataset_versions={"geolite2-asn": "2026-11-10"},
            )
        )
    await db_session.commit()
    return created


async def test_the_traceroute_export_has_a_row_per_answer_and_per_silent_hop(
    client: AsyncClient, run: TracerouteRun
) -> None:
    url = f"/api/v1/traceroutes/{run.id}"

    as_csv = parse_csv(await client.get(url, params={"format": "csv"}))
    as_json = parse_json(await client.get(url, params={"format": "json"}))

    assert [(r["ttl"], r["address"], r["silent"]) for r in as_csv] == [
        ("1", "192.168.1.1", "false"),
        ("2", "193.0.6.1", "false"),
        ("3", "", "true"),
        ("4", "8.8.8.8", "false"),
    ]
    assert len(as_json["rows"]) == 4
    second = as_csv[1]
    assert (second["asn"], second["city"], second["rtt_ms"]) == ("3333", "Amsterdam", "3.0;4.0")
    assert second["ptr_names"] == "'=cmd|' /C calc'!A0"
    for row in as_csv:
        assert (row["traceroute_id"], row["vantage_point"]) == (str(run.id), "test")
        assert json.loads(row["dataset_versions"]) == {"geolite2-asn": "2026-11-10"}
    metadata = as_json["metadata"]
    assert (metadata["traceroute_id"], metadata["target"], metadata["method"]) == (
        run.id,
        "8.8.8.8",
        "icmp",
    )
    assert metadata["vantage_point"] == "test"
    assert metadata["dataset_versions"] == {"geolite2-asn": "2026-11-10"}


# --- Authentication ---


async def test_exports_need_a_session(anonymous_client: AsyncClient, enriched_scan: Scan) -> None:
    for url in (
        f"/api/v1/scans/{enriched_scan.id}/hosts",
        f"/api/v1/projects/{enriched_scan.project_id}/hosts",
        f"/api/v1/projects/{enriched_scan.project_id}/compare?base=1&target=2",
        "/api/v1/diffs/1",
        f"/api/v1/scans/{enriched_scan.id}/diff",
        "/api/v1/traceroutes/1",
    ):
        for form in ("csv", "json"):
            separator = "&" if "?" in url else "?"
            response = await anonymous_client.get(f"{url}{separator}format={form}")
            assert response.status_code == 401, url
            assert "rows" not in response.text
