"""Recording what the scanner reports (architecture section 6.4).

Delivery is at least once (NFR-REL-1), so every write here gives the same
database whether a message arrives once, twice or out of order:

- a host observation is updated on its key (scan, address): reachability is
  OR-ed, and evidence is merged per probe, keeping the earliest reply;
- an open port is inserted with ON CONFLICT DO NOTHING;
- a chunk result is stored only if the chunk is not already done.

Observations are accepted only while the scan is in a probing phase. That is
how a completed scan stays immutable (FR-SCN-9): late and duplicate messages
are discarded. Only the targets of the scan have observation rows, so a
finding for any other address is discarded too.

The functions do not commit. ``record_result`` returns the UI events to
publish after the caller's commit.
"""

from datetime import UTC, datetime
from ipaddress import IPv4Address
from typing import Any

import structlog
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.messages import ChunkDone, ChunkRejected, Findings
from ndm.core.scope import ScopeEntry, non_public_range
from ndm.db import project_view
from ndm.db.audit import SYSTEM_ACTOR, record_audit
from ndm.db.models import (
    FINAL_SCAN_STATES,
    ChunkState,
    Host,
    HostObservation,
    PortObservation,
    ProjectHost,
    Scan,
    ScanChunk,
    ScanPhase,
    ScanState,
)
from ndm.orchestrator.enrichment import Enricher
from ndm.orchestrator.events import Event, hosts_changed, scan_progress

log = structlog.get_logger(__name__)

PROBING_PHASES = (ScanPhase.DISCOVERY, ScanPhase.PORT_SCAN)

Result = Findings | ChunkDone | ChunkRejected


async def _probing_scan(db: AsyncSession, scan_id: int) -> Scan | None:
    """The scan, locked, if it accepts observations now; otherwise None."""
    scan = await db.scalar(select(Scan).where(Scan.id == scan_id).with_for_update())
    if scan is None or scan.deleted_at is not None:
        return None
    if scan.state not in (ScanState.RUNNING, ScanState.INTERRUPTED):
        return None
    if scan.phase not in PROBING_PHASES:
        return None
    return scan


def _earliest(evidence: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Merge evidence per probe. Of two replies to the same probe the earlier
    one is kept, so the result does not depend on the order of arrival."""
    merged = dict(evidence)
    for probe, reply in new.items():
        known = merged.get(probe)
        if known is None or (reply["at"], reply["rtt_ms"]) < (known["at"], known["rtt_ms"]):
            merged[probe] = reply
    return merged


async def _record_findings(
    db: AsyncSession, scan: Scan, message: Findings, enricher: Enricher | None
) -> list[Event]:
    by_address = {host.address: host for host in message.hosts}
    observations = (
        await db.scalars(
            select(HostObservation)
            .where(
                HostObservation.scan_id == scan.id,
                HostObservation.address.in_(list(by_address)),
            )
            .with_for_update()
        )
    ).all()

    ports: list[dict[str, Any]] = []
    seen: dict[IPv4Address, tuple[datetime, datetime]] = {}
    first_seen: list[IPv4Address] = []
    for observation in observations:
        finding = by_address[observation.address]
        evidence = {
            probe: reply.model_dump(mode="json") for probe, reply in finding.evidence.items()
        }
        if not evidence and not finding.open_ports:
            continue
        if not observation.reachable:
            first_seen.append(observation.address)
        observation.reachable = True
        observation.evidence = _earliest(observation.evidence, evidence)
        ports += [
            {"scan_id": scan.id, "address": observation.address, "port": port}
            for port in finding.open_ports
        ]
        times = [reply.at for reply in finding.evidence.values()]
        if times:
            seen[observation.address] = (min(times), max(times))

    if ports:
        await db.execute(insert(PortObservation).on_conflict_do_nothing(), ports)
    await _remember_hosts(db, scan, seen)

    # A finding shows that the scanner is back.
    if scan.state is ScanState.INTERRUPTED:
        scan.state = ScanState.RUNNING
    await db.flush()

    # A host is enriched when it is first seen, so it can be drawn at once (I5).
    # Enrichment must never cost an observation: a failure is undone alone,
    # and the enrichment phase gives the host its snapshot later.
    if enricher is not None and first_seen:
        try:
            async with db.begin_nested():
                await enricher.enrich(db, scan.id, first_seen)
        except Exception:
            log.exception("inline_enrichment_failed", scan_id=scan.id)

    changed = [observation.address for observation in observations]
    if not changed:
        return []
    await project_view.refresh(db, scan_id=scan.id, addresses=changed)
    return [await hosts_changed(db, scan, changed)]


async def _remember_hosts(
    db: AsyncSession, scan: Scan, seen: dict[IPv4Address, tuple[datetime, datetime]]
) -> None:
    """Keep the global host registry and the project's hosts up to date (I1).

    First seen only moves back and last seen only moves forward, so repeating
    or reordering messages changes nothing.
    """
    if not seen:
        return
    hosts = insert(Host)
    await db.execute(
        hosts.on_conflict_do_update(
            index_elements=[Host.address],
            set_={
                "first_seen_at": func.least(Host.first_seen_at, hosts.excluded.first_seen_at),
                "last_seen_at": func.greatest(Host.last_seen_at, hosts.excluded.last_seen_at),
            },
        ),
        [
            {
                "address": address,
                "first_seen_at": first,
                "last_seen_at": last,
                "is_public": non_public_range(ScopeEntry("address", str(address), address, address))
                is None,
            }
            for address, (first, last) in seen.items()
        ],
    )
    project_hosts = insert(ProjectHost)
    await db.execute(
        project_hosts.on_conflict_do_update(
            index_elements=[ProjectHost.project_id, ProjectHost.address],
            set_={
                "first_seen_at": func.least(
                    ProjectHost.first_seen_at, project_hosts.excluded.first_seen_at
                ),
                "last_seen_at": func.greatest(
                    ProjectHost.last_seen_at, project_hosts.excluded.last_seen_at
                ),
                "latest_scan_id": func.greatest(
                    ProjectHost.latest_scan_id, project_hosts.excluded.latest_scan_id
                ),
            },
        ),
        [
            {
                "project_id": scan.project_id,
                "address": address,
                "first_seen_at": first,
                "last_seen_at": last,
                "latest_scan_id": scan.id,
            }
            for address, (first, last) in seen.items()
        ],
    )


async def _record_chunk_done(db: AsyncSession, scan: Scan, message: ChunkDone) -> list[Event]:
    stored = await db.execute(
        update(ScanChunk)
        .where(
            ScanChunk.scan_id == scan.id,
            ScanChunk.chunk_seq == message.chunk_seq,
            ScanChunk.state != ChunkState.DONE,
        )
        .values(
            state=ChunkState.DONE,
            result=message.model_dump(
                mode="json", include={"probes_sent", "receive_drops", "counts"}
            ),
        )
    )
    if stored.rowcount == 0:  # type: ignore[attr-defined]
        return []  # already done, or no such chunk
    if scan.state is ScanState.INTERRUPTED:
        scan.state = ScanState.RUNNING
    await db.flush()
    return [await scan_progress(db, scan)]


async def _record_chunk_rejected(
    db: AsyncSession, scan: Scan, message: ChunkRejected
) -> list[Event]:
    """The scanner's own scope check refused a chunk: the scan fails, with an audit entry."""
    reason = f"chunk_rejected: {message.reason}"
    await finish_scan(db, scan, ScanState.FAILED, reason=reason)
    await record_audit(
        db,
        actor=SYSTEM_ACTOR,
        action="scan.rejected",
        object_type="scan",
        object_id=scan.id,
        details={"point": "scanner", "chunk_seq": message.chunk_seq, "reason": message.reason},
    )
    log.warning("scan_failed", scan_id=scan.id, reason=reason)
    return [await scan_progress(db, scan)]


async def record_result(
    db: AsyncSession, message: Result, enricher: Enricher | None = None
) -> list[Event]:
    """Apply one result message of the scanner. Returns the events to publish
    after the commit; an empty list if the message changed nothing.

    With an enricher, a host that is seen for the first time gets its
    enrichment snapshot in the same transaction.
    """
    scan = await _probing_scan(db, message.scan_id)
    if scan is None:
        log.info("result_discarded", scan_id=message.scan_id, message_type=message.type)
        return []
    if isinstance(message, Findings):
        return await _record_findings(db, scan, message, enricher)
    if isinstance(message, ChunkDone):
        return await _record_chunk_done(db, scan, message)
    return await _record_chunk_rejected(db, scan, message)


# --- Ending a scan ---------------------------------------------------------------


async def sum_chunk_counts(db: AsyncSession, scan_id: int) -> None:
    """Put the closed and filtered counts of all finished chunks onto the host
    observations, and the totals onto the scan.

    The sums are set, not added, so calling this again gives the same numbers.
    """
    await db.execute(
        text(
            """
            UPDATE host_observation AS o
            SET closed_count = c.closed, filtered_count = c.filtered
            FROM (
                SELECT (item->>'address')::inet AS address,
                       sum((item->>'closed')::int) AS closed,
                       sum((item->>'filtered')::int) AS filtered
                FROM scan_chunk, jsonb_array_elements(result->'counts') AS item
                WHERE scan_id = :scan_id AND state = 'done'
                GROUP BY 1
            ) AS c
            WHERE o.scan_id = :scan_id AND o.address = c.address
            """
        ),
        {"scan_id": scan_id},
    )
    await project_view.refresh(db, scan_id=scan_id)
    totals = (
        await db.execute(
            text(
                """
                SELECT count(*) AS chunks_total,
                       count(*) FILTER (WHERE state = 'done') AS chunks_done,
                       coalesce(sum((result->>'probes_sent')::bigint), 0) AS probes_sent,
                       coalesce(sum((result->>'receive_drops')::bigint), 0) AS receive_drops
                FROM scan_chunk WHERE scan_id = :scan_id
                """
            ),
            {"scan_id": scan_id},
        )
    ).one()
    scan = await db.get_one(Scan, scan_id)
    scan.counters = {**scan.counters, **{key: int(value) for key, value in totals._mapping.items()}}
    await db.flush()


async def close_probing(db: AsyncSession, scan: Scan) -> None:
    """Probing is over: keep the totals on the scan and delete its chunk rows."""
    await sum_chunk_counts(db, scan.id)
    await db.execute(delete(ScanChunk).where(ScanChunk.scan_id == scan.id))


async def finish_scan(
    db: AsyncSession, scan: Scan, state: ScanState, *, reason: str | None = None
) -> None:
    """Move a scan to a final state. Its results are kept.

    The counts of the finished chunks are summed up, the totals stay on the
    scan, and the chunk rows are deleted. A scan that did not complete is
    marked partial.
    """
    assert state in FINAL_SCAN_STATES
    # A scan in a phase after probing has been through this already.
    if scan.phase is None or scan.phase in PROBING_PHASES:
        await close_probing(db, scan)
    scan.state = state
    scan.phase = None
    scan.partial = state is not ScanState.COMPLETED
    scan.failure_reason = reason
    scan.finished_at = datetime.now(UTC)
    await db.flush()
