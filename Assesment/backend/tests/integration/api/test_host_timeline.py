"""Host detail with enrichment, reverse DNS and the timeline of the address
across the scans of its project (FR-MAP-9)."""

from ipaddress import IPv4Address
from typing import Any

from httpx import AsyncClient
from sqlalchemy.dialects.postgresql import Range

from ndm.db.models import Scan, ScanState
from ndm.orchestrator.tick import Orchestrator
from tests.integration.orchestrator.conftest import Outbox, ScanFactory, run_to_end, scanner_reports
from tests.tools.enrichment_scan import SCOPE, run_phases

ADDRESS = "8.8.8.1"
NEIGHBOUR = "8.8.8.2"


def world(*ports: int, up: bool = True) -> dict[str, Any]:
    return {ADDRESS: {"ping": True, "open_ports": list(ports), "closed": "reset"}} if up else {}


async def scan_of(
    orchestrator: Orchestrator,
    make_scan: ScanFactory,
    outbox: Outbox,
    answers: dict[str, Any],
    *,
    targets: tuple[str, ...] = (ADDRESS, NEIGHBOUR),
    after: Scan | None = None,
    **values: Any,
) -> Scan:
    """One more scan, in the project of ``after`` if given, run to its end."""
    outbox.chunks.clear()
    if after is not None:
        values |= {"project_id": after.project_id, "attestation_id": after.attestation_id}
    scan = await make_scan(
        state=ScanState.QUEUED,
        targets=[IPv4Address(target) for target in targets],
        scope=SCOPE,
        **values,
    )
    await scanner_reports(orchestrator)
    await run_to_end(orchestrator, outbox, answers)
    await run_phases(orchestrator)
    return scan


async def detail(client: AsyncClient, scan: Scan, address: str = ADDRESS) -> dict[str, Any]:
    response = await client.get(f"/api/v1/scans/{scan.id}/hosts/{address}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def entries(host: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [
        (e["scan_id"], e["reachable"], e["open_ports"], e["opened"], e["closed"])
        for e in host["timeline"]
    ]


# --- Enrichment and reverse DNS in the detail ---


async def test_detail_has_enrichment_with_dataset_versions_and_reverse_dns(
    client: AsyncClient, enriched_scan: Scan
) -> None:
    host = await detail(client, enriched_scan, "8.8.8.8")

    enrichment = host["enrichment"]
    assert enrichment["created_at"]
    assert {k: v for k, v in enrichment.items() if k not in ("created_at", "dataset_versions")} == {
        "run": 1,
        "asn": 64496,
        "as_holder": "EXAMPLE-DNS",
        "network_name": "EXAMPLE-DNS",
        "organization": "Example DNS LLC",
        "organization_source": "registry",
        "rir": "arin",
        "registration_country": "US",
        "country": "US",
        "city": "Mountain View",
        "latitude": 37.4056,
        "longitude": -122.0775,
        "accuracy_radius_km": 1000,
        "unavailable": [],
    }
    assert enrichment["dataset_versions"]["geolite2-city"] == "2026-11-13"
    assert set(enrichment["dataset_versions"]) == {
        "geolite2-city",
        "geolite2-asn",
        "rir-delegation-arin",
        "rir-objects-arin",
    }
    assert host["reverse_dns"] == {"names": ["dns.example.net"], "status": "ok"}
    assert host["first_seen_at"] and host["last_seen_at"]
    # What the detail had before is still there.
    assert [port["port"] for port in host["open_ports"]] == [443]
    assert host["evidence"]


async def test_detail_of_a_host_without_a_name_and_of_a_target_that_did_not_answer(
    client: AsyncClient, enriched_scan: Scan
) -> None:
    unnamed = await detail(client, enriched_scan, "8.8.8.2")
    silent = await detail(client, enriched_scan, "8.8.8.4")

    assert unnamed["reverse_dns"] == {"names": [], "status": "no_name"}
    assert unnamed["enrichment"]["country"] == "US"
    # Nothing was looked up for a target that did not answer.
    assert silent["enrichment"] is None
    assert silent["reverse_dns"] == {"names": [], "status": None}
    assert (silent["first_seen_at"], silent["last_seen_at"]) == (None, None)
    assert entries(silent) == [(enriched_scan.id, False, [], [], [])]


async def test_unavailable_fields_are_named_in_the_detail(
    client: AsyncClient,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    """No dataset is loaded here: the fields are unknown, and the detail says so."""
    scan = await scan_of(enriching_orchestrator, make_scan, outbox, world(22))

    enrichment = (await detail(client, scan))["enrichment"]

    assert enrichment["country"] is None
    assert {"country", "asn", "organization", "rir"} <= set(enrichment["unavailable"])
    assert enrichment["dataset_versions"] == {}


async def test_detail_shows_the_latest_run_of_enrichment(
    client: AsyncClient, enriched_scan: Scan, enriching: Orchestrator, db_session: Any
) -> None:
    from datetime import UTC, datetime

    from sqlalchemy import update

    await db_session.execute(
        update(Scan)
        .where(Scan.id == enriched_scan.id)
        .values(enrichment_requested_at=datetime.now(UTC))
    )
    await db_session.commit()
    assert enriching.enricher is not None
    await enriching.enricher.rerun_requested(enriching.sessions)

    assert (await detail(client, enriched_scan, "8.8.8.8"))["enrichment"]["run"] == 2


# --- The timeline ---


async def test_timeline_lists_every_scan_that_targeted_the_address_in_order(
    client: AsyncClient, enriching: Orchestrator, make_scan: ScanFactory, outbox: Outbox
) -> None:
    first = await scan_of(enriching, make_scan, outbox, world(22, 80))
    second = await scan_of(enriching, make_scan, outbox, world(22, 443), after=first)
    # The host does not answer in the third scan, and is back in the fourth.
    third = await scan_of(enriching, make_scan, outbox, world(up=False), after=first)
    fourth = await scan_of(enriching, make_scan, outbox, world(22, 80, 443), after=first)
    # A scan of the project that did not target the address is not part of its timeline.
    await scan_of(enriching, make_scan, outbox, {}, targets=(NEIGHBOUR,), after=first)

    host = await detail(client, fourth)

    assert entries(host) == [
        (first.id, True, [22, 80], [], []),
        (second.id, True, [22, 443], [443], [80]),
        # Not reachable: no port changes are reported, the host just did not answer.
        (third.id, False, [], [], []),
        # Compared with the last scan in which it answered.
        (fourth.id, True, [22, 80, 443], [80], []),
    ]
    assert [entry["state"] for entry in host["timeline"]] == ["completed"] * 4
    assert all(entry["started_at"] and entry["finished_at"] for entry in host["timeline"])
    # The timeline is the same from whichever scan the host is opened.
    assert (await detail(client, first))["timeline"] == host["timeline"]
    assert host["first_seen_at"] < host["last_seen_at"]


async def test_port_that_one_of_two_scans_did_not_probe_is_not_reported_as_changed(
    client: AsyncClient, enriching: Orchestrator, make_scan: ScanFactory, outbox: Outbox
) -> None:
    answers = world(22, 80, 8080)
    narrow = await scan_of(enriching, make_scan, outbox, answers, probed_ports=[Range(22, 23)])
    wide = await scan_of(enriching, make_scan, outbox, answers, after=narrow)

    host = await detail(client, wide)

    # 80 and 8080 were found open by the second scan only because the first did not look.
    assert entries(host) == [
        (narrow.id, True, [22], [], []),
        (wide.id, True, [22, 80, 8080], [], []),
    ]


async def test_timeline_holds_no_scan_of_another_project_and_no_deleted_scan(
    client: AsyncClient, enriching: Orchestrator, make_scan: ScanFactory, outbox: Outbox
) -> None:
    first = await scan_of(enriching, make_scan, outbox, world(22))
    second = await scan_of(enriching, make_scan, outbox, world(22, 80), after=first)
    third = await scan_of(enriching, make_scan, outbox, world(80), after=first)
    elsewhere = await scan_of(enriching, make_scan, outbox, world(3389))
    assert elsewhere.project_id != first.project_id

    await client.delete(f"/api/v1/scans/{second.id}", params={"confirm": "true"})
    host = await detail(client, third)

    # With the second scan gone, the third is compared with the first.
    assert entries(host) == [
        (first.id, True, [22], [], []),
        (third.id, True, [80], [80], [22]),
    ]
    assert entries(await detail(client, elsewhere)) == [(elsewhere.id, True, [], [], [])]
