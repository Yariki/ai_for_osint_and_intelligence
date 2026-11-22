"""Chunk dispatch (architecture section 6.3).

One chunk per scan is in flight. Before each chunk the scope is checked again:
a target is probed only if it is inside the project scope at the moment of
execution (FR-PRJ-7, I4). The check fails closed.

Delivery is at least once and no work is lost:

- a chunk is published with the broker's confirmation, and only then is it
  committed as dispatched. A crash between the two leaves it pending, so it is
  published again;
- a chunk that stays dispatched beyond its lease is published again;
- after five attempts the scan fails.
"""

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core import scope as scope_rules
from ndm.core.messages import Chunk, ChunkPhase, Technique
from ndm.db.audit import SYSTEM_ACTOR, record_audit
from ndm.db.models import (
    ChunkState,
    Project,
    ProjectState,
    Scan,
    ScanChunk,
    ScanState,
    ScopeEntry,
)
from ndm.orchestrator.planning import ChunkSpec, ScanParameters
from ndm.orchestrator.recording import finish_scan

log = structlog.get_logger(__name__)

MAX_ATTEMPTS = 5
MIN_LEASE = timedelta(minutes=5)

PublishChunk = Callable[[Chunk], Awaitable[None]]


def lease(spec: ChunkSpec, pps: int) -> timedelta:
    """How long a dispatched chunk may stay unanswered: the larger of 5 minutes
    and three times its expected duration."""
    expected = timedelta(seconds=max(spec.probes, len(spec.targets)) / pps)
    return max(MIN_LEASE, 3 * expected)


async def current_scope(db: AsyncSession, project: Project) -> list[scope_rules.ScopeEntry]:
    if project.current_attestation_id is None:
        return []
    entries = await db.scalars(
        select(ScopeEntry).where(ScopeEntry.attestation_id == project.current_attestation_id)
    )
    return [
        scope_rules.ScopeEntry(
            entry.kind.value, entry.original_text, entry.start_address, entry.end_address
        )
        for entry in entries
    ]


async def _scope_failure(
    project: Project | None,
    scope: Sequence[scope_rules.ScopeEntry],
    spec: ChunkSpec,
    never_scan: Sequence[scope_rules.ScopeEntry],
    allow_non_public: bool,
) -> str | None:
    """Why this chunk must not be sent, or None if it may."""
    if project is None or project.deleted_at is not None:
        return "project_deleted"
    if project.state is not ProjectState.ACTIVE:
        return "project_archived"
    for address in spec.targets:
        target = scope_rules.ScopeEntry("address", str(address), address, address)
        try:
            scope_rules.check_target(
                target, scope=scope, never_scan=never_scan, allow_non_public=allow_non_public
            )
        except scope_rules.ScopeError as error:
            return error.code
    return None


async def dispatch(
    db: AsyncSession,
    scan: Scan,
    publish: PublishChunk,
    *,
    never_scan: Sequence[scope_rules.ScopeEntry],
    allow_non_public: bool,
    now: datetime,
) -> bool:
    """Send the next chunk of a scan, or the one in flight again if its lease is over.

    Commits when it has published a chunk. Returns True if the scan changed.
    """
    parameters = ScanParameters.model_validate(scan.parameters)
    in_flight = await db.scalar(
        select(ScanChunk).where(
            ScanChunk.scan_id == scan.id, ScanChunk.state == ChunkState.DISPATCHED
        )
    )
    if in_flight is not None:
        assert in_flight.dispatched_at is not None
        spec = ChunkSpec.model_validate(in_flight.spec)
        if now - in_flight.dispatched_at <= lease(spec, parameters.pps):
            return False
        if in_flight.attempts >= MAX_ATTEMPTS:
            await finish_scan(db, scan, ScanState.FAILED, reason="chunk_lost")
            log.error("scan_failed", scan_id=scan.id, reason="chunk_lost", chunk_seq=spec.chunk_seq)
            await db.commit()
            return True
        row = in_flight
    else:
        pending = await db.scalar(
            select(ScanChunk)
            .where(ScanChunk.scan_id == scan.id, ScanChunk.state == ChunkState.PENDING)
            .order_by(ScanChunk.chunk_seq)
            .limit(1)
        )
        if pending is None:
            return False
        row = pending
        spec = ChunkSpec.model_validate(row.spec)

    # The scope as it is now, not as it was when the scan was created.
    project = await db.get(Project, scan.project_id, populate_existing=True)
    scope = await current_scope(db, project) if project is not None else []
    failure = await _scope_failure(project, scope, spec, never_scan, allow_non_public)
    if failure is not None:
        await finish_scan(db, scan, ScanState.FAILED, reason="scope_changed")
        await record_audit(
            db,
            actor=SYSTEM_ACTOR,
            action="scan.rejected",
            object_type="scan",
            object_id=scan.id,
            details={"point": "orchestrator", "chunk_seq": spec.chunk_seq, "reason": failure},
        )
        log.warning("scan_failed", scan_id=scan.id, reason="scope_changed", cause=failure)
        await db.commit()
        return True

    assert project is not None and project.current_attestation_id is not None
    chunk = Chunk(
        scan_id=scan.id,
        chunk_seq=spec.chunk_seq,
        phase=ChunkPhase(spec.phase.value),
        technique=Technique(scan.technique_used or Technique.CONNECT.value),
        targets=spec.targets,
        ports=spec.ports,
        discovery=spec.discovery,
        pps=parameters.pps,
        timeout_ms=parameters.timeout_ms,
        retries=parameters.retries,
        seed=scan.seed,
        attestation_id=project.current_attestation_id,
        scope=[(entry.start, entry.end) for entry in scope],
    )
    # Publish first, with confirmation. If the process dies before the commit
    # below, the chunk is still pending and is simply published again.
    await publish(chunk)
    row.state = ChunkState.DISPATCHED
    row.dispatched_at = now
    row.attempts += 1
    await db.commit()
    log.info("chunk_dispatched", scan_id=scan.id, chunk_seq=spec.chunk_seq, attempt=row.attempts)
    return True
