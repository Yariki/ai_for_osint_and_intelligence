"""Two scans of one project with known differences, for the comparison tests.

Between the first and the second scan, on the targets 8.8.8.1 to 8.8.8.4:
8.8.8.1 closed 22 and opened 80, 8.8.8.2 did not change, 8.8.8.3 appeared and
8.8.8.4 disappeared.
"""

from typing import Any

from ndm.db.models import Scan, ScanState
from ndm.orchestrator.tick import Orchestrator
from tests.integration.orchestrator.conftest import (
    Outbox,
    ScanFactory,
    run_to_end,
    scanner_reports,
)

BEFORE = {
    "8.8.8.1": {"ping": True, "open_ports": [22, 443], "closed": "reset"},
    "8.8.8.2": {"ping": True, "open_ports": [443], "closed": "reset"},
    "8.8.8.4": {"ping": True, "open_ports": [80], "closed": "reset"},
}
AFTER = {
    "8.8.8.1": {"ping": True, "open_ports": [80, 443], "closed": "reset"},
    "8.8.8.2": {"ping": True, "open_ports": [443], "closed": "reset"},
    "8.8.8.3": {"ping": True, "open_ports": [], "closed": "reset"},
}
STATUS = {
    "changed": ["8.8.8.1", "8.8.8.3", "8.8.8.4"],
    "appeared": ["8.8.8.3"],
    "disappeared": ["8.8.8.4"],
    "unchanged": ["8.8.8.2"],
}


async def scan_of(
    orchestrator: Orchestrator,
    outbox: Outbox,
    make_scan: ScanFactory,
    world: dict[str, Any],
    like: Scan | None = None,
    **values: Any,
) -> Scan:
    """A scan run to its end; in the project of ``like`` if given."""
    outbox.chunks.clear()
    if like is not None:
        values |= {"project_id": like.project_id, "attestation_id": like.attestation_id}
    scan = await make_scan(state=ScanState.QUEUED, **values)
    await scanner_reports(orchestrator)
    await run_to_end(orchestrator, outbox, world)
    return scan


async def two_scans(
    orchestrator: Orchestrator, outbox: Outbox, make_scan: ScanFactory
) -> tuple[Scan, Scan]:
    first = await scan_of(orchestrator, outbox, make_scan, BEFORE)
    second = await scan_of(orchestrator, outbox, make_scan, AFTER, like=first)
    return first, second
