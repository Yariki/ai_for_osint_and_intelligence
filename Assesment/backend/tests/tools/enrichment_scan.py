"""Helpers for enrichment tests: run the replayed scan of public addresses, and
read back what enrichment stored."""

from ipaddress import IPv4Address
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import EnrichmentSnapshot, HostObservation, Scan, ScanState
from ndm.orchestrator import phases
from ndm.orchestrator.tick import Orchestrator
from tests.integration.orchestrator.conftest import (
    Outbox,
    ScanFactory,
    run_to_end,
    scanner_reports,
)
from tests.tools import replay_findings

RECORDED = replay_findings.load()
# A scope that covers every range of the recorded scan.
SCOPE = ("1.0.0.0", "200.3.13.255")
# The hosts of the recording that the test scan's probes find.
REACHABLE = 12

FIELDS = (
    "run",
    "latest",
    "asn",
    "as_holder",
    "network_name",
    "organization",
    "organization_source",
    "rir",
    "registration_country",
    "country",
    "city",
    "location",
    "accuracy_radius_km",
    "unavailable",
    "dataset_versions",
)
# Every field a dataset fills. With no dataset at all, all of them are unavailable.
ALL_UNAVAILABLE = {
    "country",
    "city",
    "location",
    "accuracy_radius_km",
    "asn",
    "as_holder",
    "rir",
    "registration_country",
    "network_name",
    "organization",
}


async def probe(orchestrator: Orchestrator, outbox: Outbox, make_scan: ScanFactory) -> Scan:
    """Replay the probing of the recorded scan. The scan then waits in the first
    phase after probing, or is complete if there is none."""
    scan = await make_scan(state=ScanState.QUEUED, targets=RECORDED.targets, scope=SCOPE)
    await scanner_reports(orchestrator)
    await run_to_end(orchestrator, outbox, RECORDED.world)
    return scan


async def run_phases(orchestrator: Orchestrator) -> None:
    while await phases.run_pending(orchestrator.sessions, orchestrator.publish_events):
        pass


async def scan_to_end(orchestrator: Orchestrator, outbox: Outbox, make_scan: ScanFactory) -> Scan:
    scan = await probe(orchestrator, outbox, make_scan)
    await run_phases(orchestrator)
    return scan


async def stored(db: AsyncSession, scan: Scan) -> Scan:
    await db.commit()
    return await db.get_one(Scan, scan.id, populate_existing=True)


async def snapshots(db: AsyncSession, scan: Scan) -> list[dict[str, Any]]:
    """Every snapshot of a scan, oldest run first, as plain values."""
    await db.commit()
    rows = await db.scalars(
        select(EnrichmentSnapshot)
        .where(EnrichmentSnapshot.scan_id == scan.id)
        .order_by(EnrichmentSnapshot.address, EnrichmentSnapshot.run)
        .execution_options(populate_existing=True)
    )
    return [
        {"address": str(row.address)} | {field: getattr(row, field) for field in FIELDS}
        for row in rows
    ]


async def latest(db: AsyncSession, scan: Scan) -> dict[str, dict[str, Any]]:
    """The current snapshot of each host, by address."""
    return {row["address"]: row for row in await snapshots(db, scan) if row["latest"]}


async def reachable(db: AsyncSession, scan: Scan) -> set[str]:
    await db.commit()
    addresses = await db.scalars(
        select(HostObservation.address).where(
            HostObservation.scan_id == scan.id, HostObservation.reachable
        )
    )
    return {str(address) for address in addresses}


async def reverse_dns(db: AsyncSession, scan: Scan) -> dict[str, tuple[list[str], str | None]]:
    await db.commit()
    rows = await db.execute(
        select(HostObservation.address, HostObservation.ptr_names, HostObservation.ptr_status)
        .where(HostObservation.scan_id == scan.id, HostObservation.reachable)
        .execution_options(populate_existing=True)
    )
    return {str(address): (names, status) for address, names, status in rows}


def ip(address: str) -> IPv4Address:
    return IPv4Address(address)
