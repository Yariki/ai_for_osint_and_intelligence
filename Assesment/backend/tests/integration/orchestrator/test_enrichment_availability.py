"""Check 2.3 (FR-ENR-7): scanning does not need the datasets, a field without
its dataset is marked unavailable, and a re-run adds a snapshot."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.settings import Settings
from ndm.db.models import ReferenceDataset, ScanState
from ndm.orchestrator.tick import Orchestrator
from tests.integration.orchestrator.conftest import Outbox, ScanFactory
from tests.tools import reference_data
from tests.tools.enrichment_scan import (
    ALL_UNAVAILABLE,
    REACHABLE,
    RECORDED,
    latest,
    reachable,
    scan_to_end,
    snapshots,
    stored,
)

NOW = datetime(2026, 11, 16, 9, tzinfo=UTC)


async def load(orchestrator: Orchestrator, *only: str) -> list[str]:
    assert orchestrator.enricher is not None
    return await reference_data.load(
        orchestrator.sessions,
        orchestrator.settings,
        orchestrator.enricher.geolite,
        RECORDED.reference,
        only=only or None,
    )


async def request_rerun(db: AsyncSession, orchestrator: Orchestrator, scan_id: int) -> list[int]:
    """The analyst asks for a re-run: the API sets the request, the orchestrator carries it out."""
    from ndm.db.models import Scan

    await db.execute(update(Scan).where(Scan.id == scan_id).values(enrichment_requested_at=NOW))
    await db.commit()
    assert orchestrator.enricher is not None
    return await orchestrator.enricher.rerun_requested(orchestrator.sessions)


# --- Without any dataset ---


async def test_with_no_datasets_a_scan_completes_and_every_field_is_marked_unavailable(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    scan = await scan_to_end(enriching_orchestrator, outbox, make_scan)

    scan = await stored(db_session, scan)
    assert (scan.state, scan.partial, scan.failed_phase) == (ScanState.COMPLETED, False, None)
    assert scan.dataset_versions == {}
    rows = await latest(db_session, scan)
    # Every reachable host has its snapshot, and the snapshot says what it could not know.
    assert set(rows) == await reachable(db_session, scan)
    assert len(rows) == REACHABLE
    for row in rows.values():
        assert set(row["unavailable"]) == ALL_UNAVAILABLE
        assert row["dataset_versions"] == {}
        for field in ALL_UNAVAILABLE | {"organization_source"}:
            assert row[field] is None, field


async def test_rerun_after_loading_adds_a_second_snapshot_and_leaves_the_first_unchanged(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    scan = await scan_to_end(enriching_orchestrator, outbox, make_scan)
    first = await snapshots(db_session, scan)
    await load(enriching_orchestrator)

    assert await request_rerun(db_session, enriching_orchestrator, scan.id) == [scan.id]

    after = await snapshots(db_session, scan)
    assert len(after) == 2 * REACHABLE
    # The first snapshots are as they were, except that they are not the latest any more.
    assert [row for row in after if row["run"] == 1] == [row | {"latest": False} for row in first]
    second = {row["address"]: row for row in after if row["run"] == 2}
    assert all(row["latest"] for row in second.values())
    assert second["8.8.8.8"]["unavailable"] == []
    assert (second["8.8.8.8"]["country"], second["8.8.8.8"]["asn"]) == ("US", 64496)
    # The request is done, and the scan itself was not touched.
    scan = await stored(db_session, scan)
    assert (scan.enrichment_requested_at, scan.state) == (None, ScanState.COMPLETED)
    assert scan.dataset_versions == {}

    # A third run adds a third snapshot.
    await request_rerun(db_session, enriching_orchestrator, scan.id)
    runs = [row["run"] for row in await snapshots(db_session, scan) if row["address"] == "8.8.8.8"]
    assert runs == [1, 2, 3]
    assert (await latest(db_session, scan))["8.8.8.8"]["run"] == 3


async def test_rerun_is_not_carried_out_for_a_scan_that_has_not_ended(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
) -> None:
    scan = await make_scan()  # running

    assert await request_rerun(db_session, enriching_orchestrator, scan.id) == []


# --- With some datasets ---


async def test_with_every_dataset_nothing_is_unavailable(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    await load(enriching_orchestrator)

    scan = await scan_to_end(enriching_orchestrator, outbox, make_scan)

    rows = await latest(db_session, scan)
    assert len(rows) == REACHABLE
    assert {
        address: row["unavailable"] for address, row in rows.items() if row["unavailable"]
    } == {}
    # A host whose network has no location: the location is empty, not unavailable.
    unlocated = rows["200.3.13.2"]
    assert (unlocated["location"], unlocated["country"], unlocated["city"]) == (None, None, None)
    assert unlocated["asn"] == 64500


@pytest.mark.parametrize(
    ("loaded", "address", "unavailable", "known"),
    [
        # Only the location data: no ASN, no registry.
        (
            ("geolite2-city",),
            "8.8.8.8",
            ALL_UNAVAILABLE - {"country", "city", "location", "accuracy_radius_km"},
            {"country": "US", "city": "Mountain View", "organization": None},
        ),
        # Only the ASN data: the organization falls back to the AS holder.
        (
            ("geolite2-asn",),
            "8.8.8.8",
            ALL_UNAVAILABLE - {"asn", "as_holder", "organization"},
            {"asn": 64496, "organization": "EXAMPLE-DNS", "organization_source": "as_holder"},
        ),
        # Delegation statistics without the registry's records: the registry is
        # known, the network name is not, and there is no AS holder to fall back to.
        (
            ("rir-delegation",),
            "193.0.6.1",
            {"country", "city", "location", "accuracy_radius_km", "asn", "as_holder"}
            | {"network_name", "organization"},
            {"rir": "ripe", "registration_country": "NL", "organization": None},
        ),
        # Everything of the registries, no GeoLite2.
        (
            ("rir-",),
            "193.0.6.1",
            {"country", "city", "location", "accuracy_radius_km", "asn", "as_holder"},
            {
                "rir": "ripe",
                "network_name": "EXAMPLE-UNIVERSITY",
                "organization": "University of Example",
                "organization_source": "registry",
            },
        ),
    ],
)
async def test_fields_of_a_missing_dataset_are_unavailable_and_the_others_are_filled(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
    loaded: tuple[str, ...],
    address: str,
    unavailable: set[str],
    known: dict[str, object],
) -> None:
    await load(enriching_orchestrator, *loaded)

    scan = await scan_to_end(enriching_orchestrator, outbox, make_scan)

    row = (await latest(db_session, scan))[address]
    assert set(row["unavailable"]) == unavailable
    assert {field: row[field] for field in known} == known
    # The snapshot names the versions of exactly the datasets it used.
    assert all(name.startswith(loaded) for name in row["dataset_versions"])
    assert row["dataset_versions"]


async def test_without_the_registrys_records_the_organization_is_the_as_holder(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
    enrichment_settings: Settings,
) -> None:
    """ARIN's records come from the operator. Without them: FR-ENR-3's fallback."""
    await load(enriching_orchestrator, "geolite2", "rir-delegation", "rir-objects-ripe")

    scan = await scan_to_end(enriching_orchestrator, outbox, make_scan)

    row = (await latest(db_session, scan))["8.8.8.8"]
    assert (row["rir"], row["registration_country"]) == ("arin", "US")
    assert (row["organization"], row["organization_source"]) == ("EXAMPLE-DNS", "as_holder")
    assert row["network_name"] is None
    assert row["unavailable"] == ["network_name"]
    assert "rir-objects-arin" not in row["dataset_versions"]
    # In RIPE's space the records are there, and they are used.
    ripe = (await latest(db_session, scan))["193.0.6.1"]
    assert (ripe["organization"], ripe["organization_source"]) == (
        "University of Example",
        "registry",
    )


async def test_stale_dataset_is_not_used_although_its_file_is_there(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    await load(enriching_orchestrator)
    # The licence window of the location data has passed.
    await db_session.execute(
        update(ReferenceDataset)
        .where(ReferenceDataset.name == "geolite2-city")
        .values(status="stale")
    )
    await db_session.commit()

    scan = await scan_to_end(enriching_orchestrator, outbox, make_scan)

    row = (await latest(db_session, scan))["8.8.8.8"]
    assert set(row["unavailable"]) == {"country", "city", "location", "accuracy_radius_km"}
    assert (row["country"], row["location"]) == (None, None)
    assert "geolite2-city" not in row["dataset_versions"]
    assert row["asn"] == 64496
    assert (await stored(db_session, scan)).state is ScanState.COMPLETED


async def test_dataset_update_that_fails_later_does_not_take_the_data_away(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    await load(enriching_orchestrator)
    # A later update fails: the reason is recorded, the version stays in use.
    await db_session.execute(
        update(ReferenceDataset)
        .where(ReferenceDataset.name == "geolite2-asn")
        .values(detail="MaxMind answered with status 503.", checked_at=NOW + timedelta(days=1))
    )
    await db_session.commit()

    scan = await scan_to_end(enriching_orchestrator, outbox, make_scan)

    assert (await latest(db_session, scan))["8.8.8.8"]["unavailable"] == []
