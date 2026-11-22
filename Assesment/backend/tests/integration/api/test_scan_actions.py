"""Scan actions: retry the failed phase, and run enrichment again."""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.messages import EnrichmentRerunRequested, PhaseRetryRequested
from ndm.db.models import Scan, ScanPhase, ScanState
from ndm.orchestrator.tick import Orchestrator
from tests.integration.api.conftest import audit_actions
from tests.integration.api.test_datasets import RecordingPublisher
from tests.integration.orchestrator.conftest import Outbox, ScanFactory
from tests.tools.enrichment_scan import REACHABLE, probe, run_phases


@pytest.fixture
def published(app: FastAPI) -> list[Any]:
    publisher = RecordingPublisher()
    app.state.commands = publisher
    return publisher.commands


async def get_scan(client: AsyncClient, scan: Scan) -> dict[str, Any]:
    body: dict[str, Any] = (await client.get(f"/api/v1/scans/{scan.id}")).json()
    return body


@pytest.fixture
async def failed_in_enrichment(
    client: AsyncClient,
    enriching: Orchestrator,
    make_scan: ScanFactory,
    outbox: Outbox,
    monkeypatch: pytest.MonkeyPatch,
) -> Scan:
    """A scan whose probing completed and whose enrichment phase then failed."""
    enricher = enriching.enricher
    assert enricher is not None

    async def breaks(*arguments: Any) -> None:
        raise RuntimeError("the registry tables are locked")

    scan = await probe(enriching, outbox, make_scan)
    with monkeypatch.context() as patch:
        patch.setattr(enricher, "_enrich_missing", breaks)
        await run_phases(enriching)
    return scan


# --- The scan shows what the actions need ---


async def test_scan_shows_its_failed_phase_and_its_dataset_versions(
    client: AsyncClient, failed_in_enrichment: Scan, enriched_scan: Scan
) -> None:
    failed = await get_scan(client, failed_in_enrichment)
    completed = await get_scan(client, enriched_scan)

    assert (failed["state"], failed["failed_phase"], failed["failure_reason"]) == (
        "failed",
        "enrichment",
        "enrichment_failed",
    )
    assert (failed["retry_requested"], failed["enrichment_requested"]) == (False, False)
    # Probing was complete: the totals are those of a finished scan.
    assert failed["chunks_done"] == failed["chunks_total"] > 0
    assert (completed["state"], completed["failed_phase"]) == ("completed", None)
    assert completed["dataset_versions"]["geolite2-city"] == "2026-11-13"


# --- Retry the failed phase ---


async def test_retry_puts_the_scan_back_into_its_phase_and_completes_it(
    client: AsyncClient,
    failed_in_enrichment: Scan,
    enriching: Orchestrator,
    published: list[Any],
    db_session: AsyncSession,
) -> None:
    scan = failed_in_enrichment

    response = await client.post(f"/api/v1/scans/{scan.id}/retry-phase")

    assert response.status_code == 202
    assert (response.json()["state"], response.json()["retry_requested"]) == ("failed", True)
    assert published == [PhaseRetryRequested(scan_id=scan.id, message_id=published[0].message_id)]
    assert "scan.retry_phase" in await audit_actions(db_session)

    # The orchestrator carries the request out.
    await run_phases(enriching)

    after = await get_scan(client, scan)
    assert (after["state"], after["partial"], after["failed_phase"]) == ("completed", False, None)
    assert (after["retry_requested"], after["failure_reason"]) == (False, None)
    hosts = (await client.get(f"/api/v1/scans/{scan.id}/hosts", params={"asn": 64496})).json()
    assert hosts["total"] == 4


async def test_retry_asked_twice_is_one_request(
    client: AsyncClient, failed_in_enrichment: Scan, db_session: AsyncSession
) -> None:
    for _ in range(2):
        assert (
            await client.post(f"/api/v1/scans/{failed_in_enrichment.id}/retry-phase")
        ).status_code == 202

    assert (await audit_actions(db_session)).count("scan.retry_phase") == 1


async def test_retry_is_refused_for_a_scan_that_has_not_failed_in_a_phase(
    client: AsyncClient,
    enriched_scan: Scan,
    make_scan: ScanFactory,
    published: list[Any],
    db_session: AsyncSession,
) -> None:
    running = await make_scan()
    lost = await make_scan(state=ScanState.FAILED, failure_reason="chunk_lost")
    cancelled = await make_scan(state=ScanState.CANCELLED)

    for scan in (enriched_scan, running, lost, cancelled):
        response = await client.post(f"/api/v1/scans/{scan.id}/retry-phase")

        assert response.status_code == 409, scan.state
        assert response.json()["error"]["code"] == "not_retryable"
    assert published == []
    assert "scan.retry_phase" not in await audit_actions(db_session)


# --- Run enrichment again ---


async def test_rerun_of_enrichment_adds_a_snapshot(
    client: AsyncClient,
    enriched_scan: Scan,
    enriching: Orchestrator,
    published: list[Any],
    db_session: AsyncSession,
) -> None:
    response = await client.post(f"/api/v1/scans/{enriched_scan.id}/enrich")

    assert response.status_code == 202
    assert response.json()["enrichment_requested"] is True
    assert published == [
        EnrichmentRerunRequested(scan_id=enriched_scan.id, message_id=published[0].message_id)
    ]
    assert "scan.enrich" in await audit_actions(db_session)

    assert enriching.enricher is not None
    assert await enriching.enricher.rerun_requested(enriching.sessions) == [enriched_scan.id]

    after = await get_scan(client, enriched_scan)
    assert (after["state"], after["enrichment_requested"]) == ("completed", False)
    host = (await client.get(f"/api/v1/scans/{enriched_scan.id}/hosts/8.8.8.8")).json()
    assert host["enrichment"]["run"] == 2
    # The table still has one row per host.
    rows = (
        await client.get(f"/api/v1/scans/{enriched_scan.id}/hosts", params={"reachable": "true"})
    ).json()
    assert rows["total"] == REACHABLE


async def test_rerun_is_refused_while_the_scan_has_not_ended(
    client: AsyncClient, make_scan: ScanFactory, published: list[Any]
) -> None:
    for scan in (
        await make_scan(),
        await make_scan(state=ScanState.QUEUED),
        await make_scan(phase=ScanPhase.ENRICHMENT),
    ):
        response = await client.post(f"/api/v1/scans/{scan.id}/enrich")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "scan_not_finished"
    assert published == []


async def test_rerun_is_possible_for_a_cancelled_and_for_a_failed_scan(
    client: AsyncClient, make_scan: ScanFactory, failed_in_enrichment: Scan
) -> None:
    cancelled = await make_scan(state=ScanState.CANCELLED)

    for scan in (cancelled, failed_in_enrichment):
        assert (await client.post(f"/api/v1/scans/{scan.id}/enrich")).status_code == 202


async def test_actions_on_a_scan_that_does_not_exist(client: AsyncClient) -> None:
    for action in ("retry-phase", "enrich"):
        response = await client.post(f"/api/v1/scans/999999/{action}")
        assert response.status_code == 404
