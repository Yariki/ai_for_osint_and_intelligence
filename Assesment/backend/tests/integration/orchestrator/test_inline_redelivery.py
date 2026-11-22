"""Check 2.6 (I5): a host is enriched when it is first seen, inside the
recording of the finding, and redelivered findings add nothing."""

import random
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.messages import Chunk, Findings, HostsChanged
from ndm.db.models import ScanState
from ndm.orchestrator.recording import record_result
from ndm.orchestrator.tick import Orchestrator, tick
from tests.integration.orchestrator.conftest import (
    Outbox,
    ScanFactory,
    scanner_reports,
    snapshot,
)
from tests.tools import reference_data
from tests.tools.enrichment_scan import (
    REACHABLE,
    RECORDED,
    SCOPE,
    latest,
    reachable,
    run_phases,
    snapshots,
    stored,
)
from tests.tools.stub_scanner import respond


@pytest.fixture(autouse=True)
async def datasets(enriching_orchestrator: Orchestrator) -> None:
    assert enriching_orchestrator.enricher is not None
    await reference_data.load(
        enriching_orchestrator.sessions,
        enriching_orchestrator.settings,
        enriching_orchestrator.enricher.geolite,
        RECORDED.reference,
    )


async def first_chunk(orchestrator: Orchestrator, outbox: Outbox, make_scan: ScanFactory) -> Any:
    """A scan whose discovery chunk has been published and not yet answered."""
    scan = await make_scan(state=ScanState.QUEUED, targets=RECORDED.targets, scope=SCOPE)
    await scanner_reports(orchestrator)
    await tick(orchestrator)
    [chunk] = outbox.chunks
    return scan, chunk


async def deliver(orchestrator: Orchestrator, results: list[Any]) -> None:
    async with orchestrator.sessions() as db:
        for result in results:
            await record_result(db, result, orchestrator.enricher)
            await db.commit()


def findings_of(chunk: Chunk) -> list[Findings]:
    return [result for result in respond(chunk, RECORDED.world) if isinstance(result, Findings)]


async def test_host_has_its_snapshot_as_soon_as_its_finding_is_recorded(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    scan, chunk = await first_chunk(enriching_orchestrator, outbox, make_scan)
    first = findings_of(chunk)[0]

    await deliver(enriching_orchestrator, [first])

    # Long before the enrichment phase: the scan is still in discovery.
    assert (await stored(db_session, scan)).phase is not None
    rows = await latest(db_session, scan)
    assert set(rows) == {str(host.address) for host in first.hosts}
    assert all(row["unavailable"] == [] for row in rows.values())


async def test_the_event_of_a_new_host_carries_its_place_on_the_map(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    scan, chunk = await first_chunk(enriching_orchestrator, outbox, make_scan)
    async with enriching_orchestrator.sessions() as db:
        events = []
        for finding in findings_of(chunk):
            events += await record_result(db, finding, enriching_orchestrator.enricher)
            await db.commit()

    rows = {
        str(host.address): host
        for event in events
        if isinstance(event, HostsChanged)
        for host in event.hosts
    }
    assert (rows["8.8.8.8"].latitude, rows["8.8.8.8"].longitude) == (37.4056, -122.0775)
    assert (rows["193.0.6.1"].latitude, rows["193.0.6.1"].longitude) == (52.3759, 4.8975)
    # A host without a known location is in the event too, without coordinates.
    assert (rows["200.3.13.2"].latitude, rows["200.3.13.2"].longitude) == (None, None)
    assert rows["200.3.13.2"].reachable


async def test_redelivered_findings_create_no_second_snapshot(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    scan, chunk = await first_chunk(enriching_orchestrator, outbox, make_scan)
    results = respond(chunk, RECORDED.world)

    await deliver(enriching_orchestrator, results)
    once = await snapshots(db_session, scan)
    observed = await snapshot(db_session, scan)

    # The broker delivers everything again, twice, the second time out of order.
    await deliver(enriching_orchestrator, results)
    shuffled = list(results)
    random.Random(7).shuffle(shuffled)
    await deliver(enriching_orchestrator, shuffled)

    assert await snapshots(db_session, scan) == once
    assert len(once) == len(await reachable(db_session, scan))
    after = await snapshot(db_session, scan)
    assert after["observations"] == observed["observations"]


async def test_findings_of_later_chunks_do_not_enrich_a_known_host_again(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enricher = enriching_orchestrator.enricher
    assert enricher is not None
    calls: list[list[str]] = []
    real = enricher.enrich

    async def counted(db: AsyncSession, scan_id: int, addresses: Any, **options: Any) -> None:
        calls.append(sorted(str(address) for address in addresses))
        await real(db, scan_id, addresses, **options)

    monkeypatch.setattr(enricher, "enrich", counted)
    scan, chunk = await first_chunk(enriching_orchestrator, outbox, make_scan)
    await deliver(enriching_orchestrator, respond(chunk, RECORDED.world))
    inline = [address for call in calls for address in call]

    # The port scan finds open ports on hosts that discovery has already found.
    await tick(enriching_orchestrator)
    for port_chunk in outbox.chunks[1:]:
        await deliver(enriching_orchestrator, respond(port_chunk, RECORDED.world))

    assert sorted(address for call in calls for address in call) == sorted(inline)
    assert len(inline) == len(set(inline)) == REACHABLE
    assert len(await snapshots(db_session, scan)) == REACHABLE


async def test_failing_enrichment_does_not_cost_the_observation(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enricher = enriching_orchestrator.enricher
    assert enricher is not None
    real = enricher.enrich

    async def broken(*arguments: Any, **options: Any) -> None:
        # It writes something first, so that there is something to undo.
        await real(*arguments, **options)
        raise RuntimeError("the dataset reader broke")

    monkeypatch.setattr(enricher, "enrich", broken)
    scan, chunk = await first_chunk(enriching_orchestrator, outbox, make_scan)

    await deliver(enriching_orchestrator, respond(chunk, RECORDED.world))

    # The hosts are recorded; what enrichment had written was undone alone.
    assert len(await reachable(db_session, scan)) == REACHABLE
    assert await snapshots(db_session, scan) == []

    # The enrichment phase gives them their snapshots.
    monkeypatch.setattr(enricher, "enrich", real)
    await tick(enriching_orchestrator)
    for port_chunk in outbox.chunks[1:]:
        await deliver(enriching_orchestrator, respond(port_chunk, RECORDED.world))
    await tick(enriching_orchestrator)
    await run_phases(enriching_orchestrator)

    scan = await stored(db_session, scan)
    assert scan.state is ScanState.COMPLETED
    assert set(await latest(db_session, scan)) == await reachable(db_session, scan)


async def test_without_an_enricher_findings_are_recorded_as_before(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan, chunk = await first_chunk(orchestrator, outbox, make_scan)

    await deliver(orchestrator, respond(chunk, RECORDED.world))

    assert len(await reachable(db_session, scan)) == REACHABLE
    assert await snapshots(db_session, scan) == []
