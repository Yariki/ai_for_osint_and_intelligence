"""TASK-078: the project-wide view reads a copy of each address's latest
observation on project_host. After every write that changes what the view
shows, the copy holds exactly what joining project_host to the observation
tables gives (the view before TASK-078)."""

from ipaddress import IPv4Network
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db import project_view
from ndm.db.models import Scan, ScanState
from ndm.orchestrator import phases
from ndm.orchestrator.classification import load_ruleset
from ndm.orchestrator.classification_phase import Classifier
from ndm.orchestrator.tick import Orchestrator, tick
from tests.integration.api.test_project_view import LATER_NETWORK, LATER_WORLD
from tests.integration.orchestrator.conftest import Outbox, ScanFactory, answer, scanner_reports
from tests.tools.enrichment_scan import SCOPE

FIELDS = """
    reachable, open_ports, closed_count, filtered_count, ptr_names,
    asn, as_holder, network_name, organization, rir, registration_country,
    country, city, ST_AsText(location), accuracy_radius_km,
    device_type, sub_role, confidence
"""

# What the project-wide view showed before TASK-078, joined at query time.
JOINED = """
    SELECT h.address, o.scan_id, o.reachable,
           coalesce((SELECT array_agg(p.port ORDER BY p.port) FROM port_observation p
                     WHERE p.scan_id = o.scan_id AND p.address = o.address), '{}'),
           o.closed_count, o.filtered_count, o.ptr_names,
           e.asn, e.as_holder, e.network_name, e.organization, e.rir, e.registration_country,
           e.country, e.city, ST_AsText(e.location), e.accuracy_radius_km,
           c.device_type, c.sub_role, c.confidence
    FROM project_host h
    JOIN host_observation o ON o.scan_id = h.latest_scan_id AND o.address = h.address
    JOIN scan s ON s.id = o.scan_id AND s.deleted_at IS NULL
    LEFT JOIN enrichment_snapshot e ON e.scan_id = o.scan_id AND e.address = o.address AND e.latest
    LEFT JOIN classification_result c
        ON c.scan_id = o.scan_id AND c.address = o.address AND c.latest
    WHERE h.project_id = :project
"""

COPIED = f"""
    SELECT h.address, h.latest_scan_id, {FIELDS}
    FROM project_host h JOIN scan s ON s.id = h.latest_scan_id AND s.deleted_at IS NULL
    WHERE h.project_id = :project AND h.reachable IS NOT NULL
"""


async def rows(db: AsyncSession, query: str, project: int) -> set[tuple[Any, ...]]:
    await db.commit()  # a fresh snapshot
    found = await db.execute(text(query), {"project": project})
    return {tuple(tuple(v) if isinstance(v, list) else v for v in row) for row in found}


async def assert_copy_is_current(db: AsyncSession, project: int) -> set[tuple[Any, ...]]:
    joined = await rows(db, JOINED, project)
    assert await rows(db, COPIED, project) == joined
    return joined


async def test_the_copy_follows_every_write(
    client: AsyncClient,
    classified_scan: Scan,
    enriching: Orchestrator,
    make_scan: ScanFactory,
    outbox: Outbox,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = classified_scan.project_id
    # A completed scan: findings, counts, reverse DNS, enrichment and classification.
    first = await assert_copy_is_current(db_session, project)
    assert any(row[7] is not None for row in first)  # enriched
    assert any(row[17] is not None for row in first)  # classified
    assert any(row[3] for row in first)  # open ports

    # A later scan of part of the project: at its start every target points at
    # it, unreachable; then after each chunk's findings, the end of probing and
    # each phase (reverse DNS and enrichment, then classification).
    outbox.chunks.clear()
    later = await make_scan(
        state=ScanState.QUEUED,
        targets=list(IPv4Network(LATER_NETWORK)),
        scope=SCOPE,
        project_id=project,
        attestation_id=classified_scan.attestation_id,
    )
    await scanner_reports(enriching)
    await tick(enriching)
    started = await assert_copy_is_current(db_session, project)
    assert {row[1] for row in started if str(row[0]) == "8.8.8.1"} == {later.id}
    answered = 0
    while True:
        await tick(enriching)
        if len(outbox.chunks) == answered:
            break  # probing is over: the counts are summed
        for chunk in outbox.chunks[answered:]:
            await answer(enriching, chunk, LATER_WORLD)
            await assert_copy_is_current(db_session, project)
        answered = len(outbox.chunks)
    probed = await assert_copy_is_current(db_session, project)
    # Of its open ports 22, 53 and 443, the scan probes 22 and 443.
    assert {row[3] for row in probed if str(row[0]) == "8.8.8.1"} == {(22, 443)}
    passes = 0
    while await phases.run_pending(enriching.sessions, enriching.publish_events):
        await assert_copy_is_current(db_session, project)
        passes += 1
    assert passes >= 2

    # Re-running enrichment, with a dataset that now names another
    # organization, and classification: the latest flags move.
    assert enriching.enricher is not None
    describe = enriching.enricher._describe
    monkeypatch.setattr(
        enriching.enricher,
        "_describe",
        lambda *given: describe(*given) | {"organization": "Renamed Organization"},
    )
    assert (await client.post(f"/api/v1/scans/{later.id}/enrich")).status_code == 202
    assert await enriching.enricher.rerun_requested(enriching.sessions) == [later.id]
    renamed = await assert_copy_is_current(db_session, project)
    assert {row[10] for row in renamed if row[1] == later.id and row[2]} == {"Renamed Organization"}
    assert (await client.post(f"/api/v1/scans/{later.id}/classify")).status_code == 202
    classifier = Classifier(load_ruleset(), 0.5)
    assert await classifier.rerun_requested(enriching.sessions) == [later.id]
    await assert_copy_is_current(db_session, project)

    # Deleting the later scan: its addresses show the first scan again.
    deleted = await client.delete(f"/api/v1/scans/{later.id}", params={"confirm": "true"})
    assert deleted.status_code == 204
    assert await assert_copy_is_current(db_session, project) == first

    # Deleting the first too: nothing is left to show.
    deleted = await client.delete(f"/api/v1/scans/{classified_scan.id}", params={"confirm": "true"})
    assert deleted.status_code == 204
    assert await assert_copy_is_current(db_session, project) == set()


async def test_a_refresh_needs_a_scan_or_a_project(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="a scan or a project"):
        await project_view.refresh(db_session)
