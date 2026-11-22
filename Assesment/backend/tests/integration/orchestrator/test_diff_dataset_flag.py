"""Check 4.3: an enrichment change between scans made with different dataset
versions carries the dataset-change flag (FR-HIS-5), so that a database update
is not mistaken for a network change."""

from datetime import date, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import ScanState
from ndm.orchestrator.diff import compute_diff
from ndm.orchestrator.tick import Orchestrator
from tests.integration.orchestrator.conftest import (
    Outbox,
    ScanFactory,
    run_to_end,
    scanner_reports,
)
from tests.tools import reference_data
from tests.tools.enrichment_scan import RECORDED, SCOPE, run_phases, scan_to_end


async def load(
    orchestrator: Orchestrator, reference: dict[str, dict[str, Any]], **options: Any
) -> None:
    assert orchestrator.enricher is not None
    await reference_data.load(
        orchestrator.sessions,
        orchestrator.settings,
        orchestrator.enricher.geolite,
        reference,
        **options,
    )


async def test_enrichment_change_from_a_new_dataset_release_carries_the_flag(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    enriching_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    await load(enriching_orchestrator, RECORDED.reference)
    first = await scan_to_end(enriching_orchestrator, outbox, make_scan)

    # A new GeoLite2 release moves 8.8.8.0/28 to another city; nothing on the network changed.
    moved = dict(RECORDED.reference)
    moved["8.8.8.0/28"] = moved["8.8.8.0/28"] | {"city": "Sunnyvale"}
    await load(
        enriching_orchestrator,
        moved,
        only=("geolite2",),
        built=date(2026, 11, 20),
        now=reference_data.LOADED_AT + timedelta(days=7),
    )
    outbox.chunks.clear()
    second = await make_scan(
        state=ScanState.QUEUED,
        targets=RECORDED.targets,
        scope=SCOPE,
        project_id=first.project_id,
        attestation_id=first.attestation_id,
    )
    await scanner_reports(enriching_orchestrator)
    await run_to_end(enriching_orchestrator, outbox, RECORDED.world)
    await run_phases(enriching_orchestrator)

    diff = await compute_diff(db_session, first.id, second.id)

    cities = [c for c in diff.changes if c.kind == "enrichment_changed" and c.field == "city"]
    assert {str(c.address) for c in cities} == {"8.8.8.1", "8.8.8.2", "8.8.8.3", "8.8.8.8"}
    assert all(
        (c.before, c.after, c.dataset_change) == ("Mountain View", "Sunnyvale", True)
        for c in cities
    )
    # Nothing else changed: no port, no host, no other field.
    assert [c for c in diff.changes if c not in cities] == []
