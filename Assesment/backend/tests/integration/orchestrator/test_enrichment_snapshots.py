"""Checks 2.4 and 2.5: one snapshot per host and scan with its dataset versions,
untouched by later dataset updates; and a failing enrichment phase that costs
no observation and can be retried."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import AuditEvent, GeoPoint, ReferenceDataset, ScanPhase, ScanState
from ndm.orchestrator.enrichment import Enricher
from ndm.orchestrator.tick import Orchestrator, tick
from tests.integration.orchestrator.conftest import Outbox, ScanFactory, snapshot
from tests.tools import reference_data
from tests.tools.enrichment_scan import (
    REACHABLE,
    RECORDED,
    latest,
    probe,
    reachable,
    reverse_dns,
    run_phases,
    scan_to_end,
    snapshots,
    stored,
)


@pytest.fixture(autouse=True)
async def datasets(enriching_orchestrator: Orchestrator) -> None:
    await load(enriching_orchestrator, RECORDED.reference)


async def load(orchestrator: Orchestrator, reference: dict[str, Any], **options: Any) -> list[str]:
    assert orchestrator.enricher is not None
    return await reference_data.load(
        orchestrator.sessions,
        orchestrator.settings,
        orchestrator.enricher.geolite,
        reference,
        **options,
    )


def enricher_of(orchestrator: Orchestrator) -> Enricher:
    assert orchestrator.enricher is not None
    return orchestrator.enricher


def observed(everything: dict[str, Any]) -> dict[str, Any]:
    return {key: everything[key] for key in ("observations", "ports", "hosts", "project_hosts")}


# --- Check 2.4: reproducible snapshots (FR-ENR-1, FR-ENR-5) ---


async def test_each_reachable_host_has_one_snapshot_with_its_dataset_versions(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    scan = await scan_to_end(enriching_orchestrator, outbox, make_scan)

    rows = await snapshots(db_session, scan)
    assert sorted(row["address"] for row in rows) == sorted(await reachable(db_session, scan))
    assert len(rows) == REACHABLE
    assert all((row["run"], row["latest"]) == (1, True) for row in rows)

    by_address = {row["address"]: row for row in rows}
    arin_file = await db_session.scalar(
        select(ReferenceDataset.version).where(ReferenceDataset.name == "rir-objects-arin")
    )
    assert by_address["8.8.8.8"] == {
        "address": "8.8.8.8",
        "run": 1,
        "latest": True,
        "asn": 64496,
        "as_holder": "EXAMPLE-DNS",
        "network_name": "EXAMPLE-DNS",
        "organization": "Example DNS LLC",
        "organization_source": "registry",
        "rir": "arin",
        "registration_country": "US",
        "country": "US",
        "city": "Mountain View",
        "location": GeoPoint(latitude=37.4056, longitude=-122.0775),
        "accuracy_radius_km": 1000,
        "unavailable": [],
        "dataset_versions": {
            "geolite2-city": "2026-11-13",
            "geolite2-asn": "2026-11-13",
            "rir-delegation-arin": "2026-11-16",
            "rir-objects-arin": arin_file,
        },
    }
    # One host per registry: each is answered from its own registry's data.
    summary = {
        address: (row["rir"], row["registration_country"], row["network_name"], row["organization"])
        for address, row in by_address.items()
    }
    assert summary["193.0.6.1"] == ("ripe", "NL", "EXAMPLE-UNIVERSITY", "University of Example")
    assert summary["1.0.0.1"] == (
        "apnic",
        "AU",
        "EXAMPLE-LIBRARY-AU",
        "Example Library of Australia",
    )
    assert summary["41.0.0.4"] == ("afrinic", "ZA", "EXAMPLE-ZA-MOBILE", "Example Mobile (Pty) Ltd")
    assert summary["200.3.13.2"] == ("lacnic", "UY", None, "Cliente de Ejemplo")
    assert by_address["193.0.6.1"]["dataset_versions"] == {
        "geolite2-city": "2026-11-13",
        "geolite2-asn": "2026-11-13",
        "rir-delegation-ripe": "2026-11-15",
        "rir-objects-ripe": "2026-11-15T23:20:53Z",
    }
    # Hosts of one network share one point (FR-MAP-2 starts here).
    points = {row["location"] for a, row in by_address.items() if a.startswith("8.8.8.")}
    assert points == {GeoPoint(latitude=37.4056, longitude=-122.0775)}


async def test_the_scan_records_the_dataset_versions_current_when_it_started(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    scan = await scan_to_end(enriching_orchestrator, outbox, make_scan)

    versions = (await stored(db_session, scan)).dataset_versions
    assert versions["geolite2-city"] == "2026-11-13"
    assert versions["rir-delegation-lacnic"] == "2026-11-13"
    assert len(versions) == 12  # two GeoLite2 databases, five registries twice


async def test_dataset_update_does_not_change_the_snapshots_of_earlier_scans(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    earlier = await scan_to_end(enriching_orchestrator, outbox, make_scan)
    before = await snapshots(db_session, earlier)
    versions_before = (await stored(db_session, earlier)).dataset_versions

    # A new GeoLite2 release moves 8.8.8.0/28 to another city.
    moved = dict(RECORDED.reference)
    moved["8.8.8.0/28"] = moved["8.8.8.0/28"] | {
        "city": "Sunnyvale",
        "latitude": 37.3688,
        "longitude": -122.0363,
        "accuracy_radius_km": 20,
    }
    changed = await load(
        enriching_orchestrator,
        moved,
        only=("geolite2",),
        built=date(2026, 11, 20),
        now=reference_data.LOADED_AT + timedelta(days=7),
    )
    assert changed == ["geolite2-city", "geolite2-asn"]

    outbox.chunks.clear()
    later = await scan_to_end(enriching_orchestrator, outbox, make_scan)

    # The earlier scan is exactly as it was (FR-ENR-5)...
    assert await snapshots(db_session, earlier) == before
    assert (await stored(db_session, earlier)).dataset_versions == versions_before
    # ...and the later one has the new data, under the new version.
    new = (await latest(db_session, later))["8.8.8.8"]
    assert (new["city"], new["accuracy_radius_km"]) == ("Sunnyvale", 20)
    assert new["dataset_versions"]["geolite2-city"] == "2026-11-20"
    assert (await stored(db_session, later)).dataset_versions["geolite2-city"] == "2026-11-20"
    old = next(row for row in before if row["address"] == "8.8.8.8")
    assert (old["city"], old["dataset_versions"]["geolite2-city"]) == (
        "Mountain View",
        "2026-11-13",
    )
    # The update itself is in the audit log.
    updates = await db_session.scalars(
        select(AuditEvent.details).where(
            AuditEvent.action == "dataset.update", AuditEvent.object_id == "geolite2-city"
        )
    )
    assert [details["version"] for details in updates] == ["2026-11-13", "2026-11-20"]


# --- Check 2.5: a failure in enrichment is contained (NFR-REL-3) ---


async def test_failure_in_enrichment_fails_the_scan_keeps_the_observations_and_can_be_retried(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enricher = enricher_of(enriching_orchestrator)
    real = enricher._enrich_missing

    async def breaks(*arguments: Any) -> None:
        raise RuntimeError("the registry tables are locked")

    scan = await probe(enriching_orchestrator, outbox, make_scan)
    before = observed(await snapshot(db_session, scan))
    inline = await snapshots(db_session, scan)
    probed = len(outbox.chunks)
    monkeypatch.setattr(enricher, "_enrich_missing", breaks)

    await run_phases(enriching_orchestrator)

    scan = await stored(db_session, scan)
    assert (scan.state, scan.phase) == (ScanState.FAILED, None)
    assert (scan.failed_phase, scan.failure_reason) == (ScanPhase.ENRICHMENT, "enrichment_failed")
    # Everything the scan observed is intact, and so is what was enriched before.
    assert observed(await snapshot(db_session, scan)) == before
    assert before["ports"]
    assert await snapshots(db_session, scan) == inline

    # The analyst retries the failed phase, with the cause gone.
    monkeypatch.setattr(enricher, "_enrich_missing", real)
    scan.retry_requested_at = datetime.now(UTC)
    await db_session.commit()
    await run_phases(enriching_orchestrator)

    scan = await stored(db_session, scan)
    assert (scan.state, scan.partial, scan.failed_phase) == (ScanState.COMPLETED, False, None)
    assert observed(await snapshot(db_session, scan)) == before
    assert len(await snapshots(db_session, scan)) == REACHABLE
    assert len(outbox.chunks) == probed  # nothing was probed again


async def test_phase_that_was_interrupted_is_run_again_and_ends_in_the_same_state(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    orchestrator: Orchestrator,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No inline enrichment here, so that the phase has every host to do."""
    enricher = enricher_of(enriching_orchestrator)
    monkeypatch.setattr("ndm.orchestrator.enrichment.BATCH_HOSTS", 5)
    scan = await probe(orchestrator, outbox, make_scan)
    assert await snapshots(db_session, scan) == []
    real = enricher.enrich
    batches = 0

    async def dies_in_the_second_batch(*arguments: Any, **options: Any) -> None:
        nonlocal batches
        batches += 1
        if batches == 2:
            raise KeyboardInterrupt  # the process is killed
        await real(*arguments, **options)

    monkeypatch.setattr(enricher, "enrich", dies_in_the_second_batch)
    with pytest.raises(KeyboardInterrupt):
        await run_phases(enriching_orchestrator)
    assert len(await snapshots(db_session, scan)) == 5
    assert (await stored(db_session, scan)).phase is ScanPhase.ENRICHMENT

    # The orchestrator comes back and runs the phase again.
    monkeypatch.setattr(enricher, "enrich", real)
    await run_phases(enriching_orchestrator)

    assert (await stored(db_session, scan)).state is ScanState.COMPLETED
    rows = await snapshots(db_session, scan)
    assert len(rows) == REACHABLE
    assert len({row["address"] for row in rows}) == REACHABLE


# --- Reverse DNS inside the phase (FR-ENR-4), with a resolver that is a function ---


class Resolver:
    """Stands in for ``rdns.resolve``: answers from a table and keeps what it was asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.nameservers: list[tuple[str, int] | None] = []

    async def __call__(self, addresses: Any, nameserver: Any) -> dict[Any, tuple[list[str], str]]:
        self.asked += [str(address) for address in addresses]
        self.nameservers.append(nameserver)
        names = {
            "8.8.8.8": (["dns.example.net"], "ok"),
            "193.0.6.1": (["web.example.nl", "www.example.nl"], "ok"),
            "1.0.0.1": ([], "failed"),
        }
        return {address: names.get(str(address), ([], "no_name")) for address in addresses}


def with_reverse_dns(orchestrator: Orchestrator, resolver: Any, **settings: Any) -> None:
    enricher = enricher_of(orchestrator)
    enricher.resolve = resolver
    enricher.settings = enricher.settings.model_copy(update={"ptr_enabled": True} | settings)


async def test_names_of_every_reachable_host_are_stored_with_the_scan(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    resolver = Resolver()
    with_reverse_dns(enriching_orchestrator, resolver, ptr_resolver="9.9.9.9:5353")

    scan = await scan_to_end(enriching_orchestrator, outbox, make_scan)

    names = await reverse_dns(db_session, scan)
    assert sorted(resolver.asked) == sorted(await reachable(db_session, scan))
    assert set(resolver.nameservers) == {("9.9.9.9", 5353)}
    assert names["8.8.8.8"] == (["dns.example.net"], "ok")
    assert names["193.0.6.1"] == (["web.example.nl", "www.example.nl"], "ok")
    assert names["1.0.0.1"] == ([], "failed")
    assert names["8.8.8.1"] == ([], "no_name")
    assert (await stored(db_session, scan)).state is ScanState.COMPLETED
    # A target that did not answer is not looked up: only discovered hosts are (FR-ENR-4).
    assert "41.0.0.10" not in resolver.asked and len(resolver.asked) == REACHABLE


async def test_with_the_switch_off_the_resolver_is_never_asked(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    resolver = Resolver()
    enricher_of(enriching_orchestrator).resolve = resolver  # the switch is off in these settings

    scan = await scan_to_end(enriching_orchestrator, outbox, make_scan)

    assert resolver.asked == []
    statuses = {status for _, status in (await reverse_dns(db_session, scan)).values()}
    assert statuses == {"disabled"}
    assert (await stored(db_session, scan)).state is ScanState.COMPLETED


async def test_lookups_already_made_are_not_repeated_when_the_phase_runs_again(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = Resolver()
    with_reverse_dns(enriching_orchestrator, resolver)
    enricher = enricher_of(enriching_orchestrator)
    real = enricher._enrich_missing

    async def breaks(*arguments: Any) -> None:
        raise RuntimeError("after reverse DNS")

    monkeypatch.setattr(enricher, "_enrich_missing", breaks)
    scan = await scan_to_end(enriching_orchestrator, outbox, make_scan)
    assert len(resolver.asked) == REACHABLE

    monkeypatch.setattr(enricher, "_enrich_missing", real)
    scan = await stored(db_session, scan)
    scan.retry_requested_at = datetime.now(UTC)
    await db_session.commit()
    await run_phases(enriching_orchestrator)

    assert (await stored(db_session, scan)).state is ScanState.COMPLETED
    assert len(resolver.asked) == REACHABLE


async def test_scan_cancelled_during_reverse_dns_gets_no_names_written_afterwards(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    resolver = Resolver()

    async def cancelled_while_asking(addresses: Any, nameserver: Any) -> Any:
        async with enriching_orchestrator.sessions() as db:
            from ndm.db.models import Scan

            running = await db.get_one(Scan, scan.id)
            running.cancel_requested_at = datetime.now(UTC)
            await db.commit()
        await tick(enriching_orchestrator)
        return await resolver(addresses, nameserver)

    with_reverse_dns(enriching_orchestrator, cancelled_while_asking)
    scan = await probe(enriching_orchestrator, outbox, make_scan)

    await run_phases(enriching_orchestrator)

    assert (await stored(db_session, scan)).state is ScanState.CANCELLED
    # The observations of a scan that has ended are not written to any more (FR-SCN-9).
    assert {status for _, status in (await reverse_dns(db_session, scan)).values()} == {None}


async def test_rerun_of_enrichment_asks_no_resolver_and_leaves_the_names_alone(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    resolver = Resolver()
    with_reverse_dns(enriching_orchestrator, resolver)
    scan = await scan_to_end(enriching_orchestrator, outbox, make_scan)
    names = await reverse_dns(db_session, scan)
    asked = len(resolver.asked)

    scan = await stored(db_session, scan)
    scan.enrichment_requested_at = datetime.now(UTC)
    await db_session.commit()
    await enricher_of(enriching_orchestrator).rerun_requested(enriching_orchestrator.sessions)

    assert len(resolver.asked) == asked
    assert await reverse_dns(db_session, scan) == names
    assert len(await snapshots(db_session, scan)) == 2 * REACHABLE
