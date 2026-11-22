"""The classification phase of a scan, and its re-run (FR-CLS-1 to FR-CLS-7).

After enrichment, every discovered host of a scan is classified from stored
data only: its open ports, its reverse-DNS names, its current enrichment
snapshot, and whether it has been seen as an intermediate traceroute hop.
Each result is written with the key (scan, address, run number), so a phase
that runs again after a crash adds nothing twice.

Results are never changed afterwards. A newer ruleset does not touch them
(FR-CLS-4); a re-run with the current ruleset adds a result with the next run
number and moves the latest flag (FR-CLS-7).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from ipaddress import IPv4Address
from itertools import batched
from typing import Any

import structlog
from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ndm.db import project_view
from ndm.db.models import (
    FINAL_SCAN_STATES,
    ClassificationResult,
    EnrichmentSnapshot,
    HostObservation,
    PortObservation,
    Scan,
    TracerouteProbe,
    TracerouteRun,
)
from ndm.orchestrator.classification import ENRICHMENT_FIELDS, Ruleset, classify

log = structlog.get_logger(__name__)

BATCH_HOSTS = 500
Sessions = async_sessionmaker[AsyncSession]


async def seen_as_hop(db: AsyncSession, addresses: Sequence[IPv4Address]) -> set[IPv4Address]:
    """The addresses that answered a traceroute as an intermediate hop.

    An address that answered only as the target of a traceroute is not a hop:
    rows where the responder is the run's own target do not count.
    """
    if not addresses:
        return set()
    found = await db.scalars(
        select(TracerouteProbe.responder)
        .join(TracerouteRun, TracerouteRun.id == TracerouteProbe.run_id)
        .where(
            TracerouteProbe.responder.in_(list(addresses)),
            TracerouteProbe.responder != TracerouteRun.target,
        )
        .distinct()
    )
    return {address for address in found if address is not None}


@dataclass
class Classifier:
    ruleset: Ruleset
    threshold: float

    async def _inputs(
        self, db: AsyncSession, scan_id: int, addresses: Sequence[IPv4Address]
    ) -> dict[IPv4Address, dict[str, Any]]:
        """The four allowed inputs (FR-CLS-2) of each host, from what is stored."""
        inputs: dict[IPv4Address, dict[str, Any]] = {
            address: {"open_ports": set(), "ptr_names": [], "enrichment": {}, "seen_as_hop": False}
            for address in addresses
        }
        ports = await db.execute(
            select(PortObservation.address, PortObservation.port).where(
                PortObservation.scan_id == scan_id, PortObservation.address.in_(list(addresses))
            )
        )
        for address, port in ports:
            inputs[address]["open_ports"].add(port)
        names = await db.execute(
            select(HostObservation.address, HostObservation.ptr_names).where(
                HostObservation.scan_id == scan_id, HostObservation.address.in_(list(addresses))
            )
        )
        for address, ptr_names in names:
            inputs[address]["ptr_names"] = ptr_names
        snapshots = await db.scalars(
            select(EnrichmentSnapshot).where(
                EnrichmentSnapshot.scan_id == scan_id,
                EnrichmentSnapshot.address.in_(list(addresses)),
                EnrichmentSnapshot.latest,
            )
        )
        for snapshot in snapshots:
            inputs[snapshot.address]["enrichment"] = {
                name: getattr(snapshot, name) for name in ENRICHMENT_FIELDS
            }
        for address in await seen_as_hop(db, addresses):
            inputs[address]["seen_as_hop"] = True
        return inputs

    async def classify_hosts(
        self, db: AsyncSession, scan_id: int, addresses: Sequence[IPv4Address], *, run: int = 1
    ) -> None:
        """Store a result for each address. One that already has a result of
        this run is left alone. Does not commit."""
        if not addresses:
            return
        rows = []
        for address, given in (await self._inputs(db, scan_id, addresses)).items():
            result = classify(
                self.ruleset,
                open_ports=frozenset(given["open_ports"]),
                ptr_names=given["ptr_names"],
                enrichment=given["enrichment"],
                seen_as_hop=given["seen_as_hop"],
                threshold=self.threshold,
            )
            rows.append(
                {
                    "scan_id": scan_id,
                    "address": address,
                    "run": run,
                    "device_type": result.device_type,
                    "sub_role": result.sub_role,
                    "confidence": result.confidence,
                    "rules": result.rules,
                    "ruleset_version": result.ruleset_version,
                }
            )
        await db.execute(insert(ClassificationResult).on_conflict_do_nothing(), rows)
        await project_view.refresh(db, scan_id=scan_id, addresses=addresses)

    async def run_phase(self, sessions: Sessions, scan_id: int) -> None:
        """The classification phase: every discovered host without a result yet."""
        has_result = exists().where(
            ClassificationResult.scan_id == HostObservation.scan_id,
            ClassificationResult.address == HostObservation.address,
        )
        async with sessions() as db:
            missing = list(
                await db.scalars(
                    select(HostObservation.address)
                    .where(
                        HostObservation.scan_id == scan_id,
                        HostObservation.reachable,
                        ~has_result,
                    )
                    .order_by(HostObservation.address)
                )
            )
        for batch in batched(missing, BATCH_HOSTS, strict=False):
            async with sessions() as db:
                await self.classify_hosts(db, scan_id, batch)
                await db.commit()

    async def rerun_requested(self, sessions: Sessions) -> list[int]:
        """Classify again every finished scan the analyst asked for, with the
        current ruleset. Returns their ids."""
        async with sessions() as db:
            requested = list(
                await db.scalars(
                    select(Scan.id)
                    .where(
                        Scan.classification_requested_at.is_not(None),
                        Scan.state.in_(FINAL_SCAN_STATES),
                        Scan.deleted_at.is_(None),
                    )
                    .order_by(Scan.id)
                )
            )
        done = []
        for scan_id in requested:
            async with sessions() as db:
                try:
                    await self._rerun(db, scan_id)
                    done.append(scan_id)
                except Exception:
                    log.exception("classification_rerun_failed", scan_id=scan_id)
                    await db.rollback()
                await db.execute(
                    update(Scan).where(Scan.id == scan_id).values(classification_requested_at=None)
                )
                await db.commit()
        return done

    async def _rerun(self, db: AsyncSession, scan_id: int) -> None:
        previous = await db.scalar(
            select(func.max(ClassificationResult.run)).where(
                ClassificationResult.scan_id == scan_id
            )
        )
        run = (previous or 0) + 1
        addresses = list(
            await db.scalars(
                select(HostObservation.address).where(
                    HostObservation.scan_id == scan_id, HostObservation.reachable
                )
            )
        )
        await db.execute(
            update(ClassificationResult)
            .where(ClassificationResult.scan_id == scan_id, ClassificationResult.latest)
            .values(latest=False)
        )
        for batch in batched(addresses, BATCH_HOSTS, strict=False):
            await self.classify_hosts(db, scan_id, batch, run=run)
        log.info("classification_rerun", scan_id=scan_id, run=run, ruleset=self.ruleset.version)
