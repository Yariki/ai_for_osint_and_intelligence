"""Enrichment: what the reference datasets and reverse DNS say about a host
(architecture 11.1).

Local enrichment reads GeoLite2 and the registry tables. Nothing is asked of
any service per address (FR-ENR-2); the one exception is reverse DNS, which
goes to the configured resolver and can be switched off (FR-ENR-4).

A host is enriched when it is first seen, inside the recording of the finding
(I5), so it can be drawn on the map at once. The enrichment phase after
probing does reverse DNS and enriches whatever missed the first chance.

Every result is a snapshot that belongs to its scan and is never changed
(FR-ENR-5). A field whose dataset is missing or stale is listed as
unavailable, which is not the same as empty (FR-ENR-7). A re-run adds a
snapshot with the next run number.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from ipaddress import IPv4Address
from itertools import batched
from typing import Any

import structlog
from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ndm.core.settings import Settings
from ndm.db import project_view
from ndm.db.models import (
    FINAL_SCAN_STATES,
    EnrichmentSnapshot,
    GeoPoint,
    HostObservation,
    ReferenceDataset,
    Scan,
    ScanPhase,
    ScanState,
    TracerouteResponder,
)
from ndm.orchestrator import rdns
from ndm.orchestrator.datasets import rir_delegation, rir_lookup
from ndm.orchestrator.datasets.geolite import GeoLite
from ndm.orchestrator.datasets.manager import STATUS_OK
from ndm.orchestrator.datasets.rir_lookup import Registration

log = structlog.get_logger(__name__)

CITY = "geolite2-city"
ASN = "geolite2-asn"
DELEGATIONS = [f"rir-delegation-{rir}" for rir in rir_delegation.SOURCES]

BATCH_HOSTS = 500

Sessions = async_sessionmaker[AsyncSession]
Resolve = Callable[
    [Sequence[IPv4Address], tuple[str, int] | None], Awaitable[dict[IPv4Address, rdns.Answer]]
]


async def usable_versions(db: AsyncSession) -> dict[str, str]:
    """Name -> version of every dataset that may be used now."""
    rows = await db.execute(
        select(ReferenceDataset.name, ReferenceDataset.version).where(
            ReferenceDataset.status == STATUS_OK, ReferenceDataset.version.is_not(None)
        )
    )
    return {name: version for name, version in rows if version is not None}


@dataclass
class Enricher:
    settings: Settings
    geolite: GeoLite
    resolve: Resolve = rdns.resolve

    # --- Local enrichment ---------------------------------------------------------

    async def enrich(
        self, db: AsyncSession, scan_id: int, addresses: Sequence[IPv4Address], *, run: int = 1
    ) -> None:
        """Store a snapshot for each address.

        An address that already has a snapshot of this run is left alone, so
        calling this again, for a redelivered finding or a repeated phase,
        changes nothing. Does not commit.
        """
        wanted = sorted(set(addresses))
        if not wanted:
            return
        versions = await usable_versions(db)
        registrations = await rir_lookup.lookup(db, wanted)
        await db.execute(
            insert(EnrichmentSnapshot).on_conflict_do_nothing(),
            [
                {"scan_id": scan_id, "address": address, "run": run}
                | self._describe(address, registrations[address], versions)
                for address in wanted
            ],
        )
        await project_view.refresh(db, scan_id=scan_id, addresses=wanted)

    async def enrich_hops(
        self, db: AsyncSession, run_id: int, addresses: Sequence[IPv4Address]
    ) -> None:
        """Store a snapshot owned by a traceroute run for each responding hop
        (FR-TRC-4). A hop already enriched for the run is left alone. Does not commit."""
        wanted = sorted(set(addresses))
        if not wanted:
            return
        versions = await usable_versions(db)
        registrations = await rir_lookup.lookup(db, wanted)
        await db.execute(
            insert(EnrichmentSnapshot).on_conflict_do_nothing(),
            [
                {"traceroute_run_id": run_id, "address": address}
                | self._describe(address, registrations[address], versions)
                for address in wanted
            ],
        )

    async def resolve_hop_names(self, sessions: Sessions) -> int:
        """Reverse DNS for the public hops that have not been looked up yet.

        Done here, beside the recording of hops and not in it: a slow resolver
        must not hold up the results of scans. Returns how many were looked up.
        """
        async with sessions() as db:
            pending = (
                await db.execute(
                    select(TracerouteResponder.run_id, TracerouteResponder.address)
                    .where(
                        TracerouteResponder.ptr_status.is_(None),
                        TracerouteResponder.unlocatable.is_(False),
                    )
                    .order_by(TracerouteResponder.run_id, TracerouteResponder.address)
                    .limit(BATCH_HOSTS)
                )
            ).all()
        if not pending:
            return 0
        addresses = sorted({address for _, address in pending})
        if self.settings.ptr_enabled:
            answers = await self.resolve(addresses, self.settings.ptr_resolver_address)
        else:
            answers = {address: ([], rdns.STATUS_DISABLED) for address in addresses}
        async with sessions() as db:
            await db.execute(
                update(TracerouteResponder),
                [
                    {
                        "run_id": run_id,
                        "address": address,
                        "ptr_names": answers[address][0],
                        "ptr_status": answers[address][1],
                    }
                    for run_id, address in pending
                ],
            )
            await db.commit()
        return len(pending)

    def _describe(
        self, address: IPv4Address, registration: Registration, versions: dict[str, str]
    ) -> dict[str, Any]:
        """The fields of one snapshot, with what is unavailable and the versions used."""
        unavailable: list[str] = []
        used: dict[str, str] = {}

        # Location: GeoLite2 City.
        country = city = point = radius = None
        if CITY in versions and self.geolite.available(CITY):
            used[CITY] = versions[CITY]
            record = self.geolite.city(address) or {}
            location = record.get("location") or {}
            country = (record.get("country") or {}).get("iso_code")
            city = ((record.get("city") or {}).get("names") or {}).get("en")
            radius = location.get("accuracy_radius")
            if "latitude" in location and "longitude" in location:
                point = GeoPoint(location["latitude"], location["longitude"])
        else:
            unavailable += ["country", "city", "location", "accuracy_radius_km"]

        # ASN and AS holder: GeoLite2 ASN.
        asn = as_holder = None
        if ASN in versions and self.geolite.available(ASN):
            used[ASN] = versions[ASN]
            record = self.geolite.asn(address) or {}
            asn = record.get("autonomous_system_number")
            as_holder = record.get("autonomous_system_organization")
        else:
            unavailable += ["asn", "as_holder"]

        # Registry and registration country: the delegation statistics. Network
        # name and organization: the records of the registry the address is in.
        records_known = False
        if registration.rir is not None:
            delegation = f"rir-delegation-{registration.rir}"
            objects = f"rir-objects-{registration.rir}"
            if delegation in versions:
                used[delegation] = versions[delegation]
            if objects in versions:
                used[objects] = versions[objects]
                records_known = True
        elif any(name not in versions for name in DELEGATIONS):
            # The address is in no loaded delegation, and not every registry is loaded.
            unavailable += ["rir", "registration_country"]
        else:
            records_known = True  # every registry is loaded: nobody registered it
        if not records_known:
            unavailable.append("network_name")
            registration = replace(registration, network_name=None, organization=None)

        # The organization falls back to the AS holder (FR-ENR-3).
        organization, source = rir_lookup.organization(registration, as_holder)
        if organization is None and (not records_known or "as_holder" in unavailable):
            unavailable.append("organization")

        return {
            "asn": asn,
            "as_holder": as_holder,
            "network_name": registration.network_name,
            "organization": organization,
            "organization_source": source,
            "rir": registration.rir,
            "registration_country": registration.registration_country,
            "country": country,
            "city": city,
            "location": point,
            "accuracy_radius_km": radius,
            "unavailable": unavailable,
            "dataset_versions": used,
        }

    # --- The enrichment phase -------------------------------------------------------

    async def run_phase(self, sessions: Sessions, scan_id: int) -> None:
        """The phase after probing: reverse DNS for every reachable host, then
        local enrichment for the hosts that have no snapshot yet.

        Both steps continue where an interrupted run stopped.
        """
        await self._reverse_dns(sessions, scan_id)
        await self._enrich_missing(sessions, scan_id)

    async def _reverse_dns(self, sessions: Sessions, scan_id: int) -> None:
        async with sessions() as db:
            pending = list(
                await db.scalars(
                    select(HostObservation.address)
                    .where(
                        HostObservation.scan_id == scan_id,
                        HostObservation.reachable,
                        HostObservation.ptr_status.is_(None),
                    )
                    .order_by(HostObservation.address)
                )
            )
        for batch in batched(pending, BATCH_HOSTS, strict=False):
            if self.settings.ptr_enabled:
                answers = await self.resolve(batch, self.settings.ptr_resolver_address)
            else:
                # Switched off: no query is sent for any address.
                answers = {address: ([], rdns.STATUS_DISABLED) for address in batch}
            async with sessions() as db:
                if not await _in_enrichment(db, scan_id):
                    return  # cancelled meanwhile: its observations are final
                await db.execute(
                    update(HostObservation),
                    [
                        {
                            "scan_id": scan_id,
                            "address": address,
                            "ptr_names": names,
                            "ptr_status": status,
                        }
                        for address, (names, status) in answers.items()
                    ],
                )
                await project_view.refresh(db, scan_id=scan_id, addresses=list(answers))
                await db.commit()

    async def _enrich_missing(self, sessions: Sessions, scan_id: int) -> None:
        has_snapshot = exists().where(
            EnrichmentSnapshot.scan_id == HostObservation.scan_id,
            EnrichmentSnapshot.address == HostObservation.address,
        )
        async with sessions() as db:
            missing = list(
                await db.scalars(
                    select(HostObservation.address)
                    .where(
                        HostObservation.scan_id == scan_id,
                        HostObservation.reachable,
                        ~has_snapshot,
                    )
                    .order_by(HostObservation.address)
                )
            )
        for batch in batched(missing, BATCH_HOSTS, strict=False):
            async with sessions() as db:
                await self.enrich(db, scan_id, batch)
                await db.commit()

    # --- Re-run (FR-ENR-7) ----------------------------------------------------------

    async def rerun_requested(self, sessions: Sessions) -> list[int]:
        """Enrich again every finished scan the analyst asked for. Returns their ids.

        Each scan gets a snapshot with the next run number for every reachable
        host, and the latest flag moves to it. Earlier snapshots are unchanged.
        """
        async with sessions() as db:
            requested = list(
                await db.scalars(
                    select(Scan.id)
                    .where(
                        Scan.enrichment_requested_at.is_not(None),
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
                    # The request is dropped, so that a scan that cannot be
                    # enriched is not tried again every few seconds.
                    log.exception("enrichment_rerun_failed", scan_id=scan_id)
                    await db.rollback()
                await db.execute(
                    update(Scan).where(Scan.id == scan_id).values(enrichment_requested_at=None)
                )
                await db.commit()
        return done

    async def _rerun(self, db: AsyncSession, scan_id: int) -> None:
        previous = await db.scalar(
            select(func.max(EnrichmentSnapshot.run)).where(EnrichmentSnapshot.scan_id == scan_id)
        )
        run = (previous or 0) + 1
        addresses = list(
            await db.scalars(
                select(HostObservation.address).where(
                    HostObservation.scan_id == scan_id, HostObservation.reachable
                )
            )
        )
        # The flag first: a scan has one latest snapshot per address.
        await db.execute(
            update(EnrichmentSnapshot)
            .where(EnrichmentSnapshot.scan_id == scan_id, EnrichmentSnapshot.latest)
            .values(latest=False)
        )
        for batch in batched(addresses, BATCH_HOSTS, strict=False):
            await self.enrich(db, scan_id, batch, run=run)
        log.info("enrichment_rerun", scan_id=scan_id, run=run, hosts=len(addresses))


async def _in_enrichment(db: AsyncSession, scan_id: int) -> bool:
    """Lock the scan and say whether it is still in the enrichment phase."""
    scan = await db.scalar(select(Scan).where(Scan.id == scan_id).with_for_update())
    return (
        scan is not None and scan.state is ScanState.RUNNING and scan.phase is ScanPhase.ENRICHMENT
    )
