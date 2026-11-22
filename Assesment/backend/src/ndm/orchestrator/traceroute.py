"""Traceroute in the orchestrator (architecture section 7).

- **Dispatch.** A requested run is sent to the scanner's traceroute queue with
  the project scope attached, after the target has been checked against the
  scope as it is now. At most ``NDM_MAX_CONCURRENT_TRACEROUTES`` runs are in
  flight; further ones wait. Traceroutes have their own queue and ceiling, so
  a running scan never delays one.
- **Recording.** One message per TTL. Each probe is stored once, keyed by run,
  TTL and probe number, so a repeated message changes nothing. Every
  responding address goes into the global host table (I2). A public one is
  enriched with a snapshot owned by the run; a private or CGNAT one is stored
  and marked unlocatable (FR-TRC-4). Reverse DNS of hops is looked up apart
  from recording, by ``Enricher.resolve_hop_names``.
- Nothing here ever makes a hop a destination: only the target is (I2).
"""

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core import scope as scope_rules
from ndm.core.messages import (
    HopRow,
    TracerouteDone,
    TracerouteHop,
    TracerouteHopEvent,
    TracerouteJob,
    TracerouteMethod,
    TracerouteRejected,
)
from ndm.db.audit import SYSTEM_ACTOR, record_audit
from ndm.db.models import (
    EnrichmentSnapshot,
    Host,
    Project,
    ProjectState,
    ScopeEntry,
    TracerouteProbe,
    TracerouteResponder,
    TracerouteRun,
    TracerouteState,
    VantagePoint,
)
from ndm.orchestrator.enrichment import Enricher

log = structlog.get_logger(__name__)

# Probes per second of one traceroute. Every probe also counts against the
# scanner's global rate.
PPS = 50
MAX_ATTEMPTS = 3
LEASE_MARGIN = timedelta(minutes=2)

TracerouteMessage = TracerouteHop | TracerouteDone | TracerouteRejected
PublishJob = Callable[[TracerouteJob], Awaitable[None]]


def is_public(address: IPv4Address) -> bool:
    return (
        scope_rules.non_public_range(
            scope_rules.ScopeEntry("address", str(address), address, address)
        )
        is None
    )


def lease(run: TracerouteRun) -> timedelta:
    """How long a dispatched run may stay without its end: every TTL timing out,
    the probes at the run's rate, and a margin."""
    hops = int(run.parameters.get("max_hops", 30))
    probes = int(run.parameters.get("probes_per_hop", 3))
    timeout = int(run.parameters.get("timeout_ms", 1000))
    return (
        timedelta(milliseconds=hops * timeout)
        + timedelta(seconds=hops * probes / PPS)
        + LEASE_MARGIN
    )


async def _current_scope(db: AsyncSession, project: Project) -> list[scope_rules.ScopeEntry]:
    if project.current_attestation_id is None:
        return []
    entries = await db.scalars(
        select(ScopeEntry).where(ScopeEntry.attestation_id == project.current_attestation_id)
    )
    return [
        scope_rules.ScopeEntry(e.kind.value, e.original_text, e.start_address, e.end_address)
        for e in entries
    ]


async def _fail(db: AsyncSession, run: TracerouteRun, reason: str, now: datetime) -> None:
    run.state = TracerouteState.FAILED
    run.failure_reason = reason
    run.finished_at = now
    await db.flush()


async def dispatch(
    db: AsyncSession,
    publish: PublishJob,
    *,
    vantage: VantagePoint,
    ceiling: int,
    never_scan: Sequence[scope_rules.ScopeEntry],
    allow_non_public: bool,
    now: datetime | None = None,
) -> list[TracerouteRun]:
    """Send waiting runs while fewer than ``ceiling`` are in flight, and send
    again a run whose lease is over. Commits after each publish. Returns the
    runs that changed."""
    now = now or datetime.now(UTC)
    changed: list[TracerouteRun] = []

    # Runs whose job or result was lost: send again, or give up.
    for run in await db.scalars(
        select(TracerouteRun)
        .where(TracerouteRun.state == TracerouteState.RUNNING)
        .order_by(TracerouteRun.id)
        .with_for_update(skip_locked=True)
    ):
        assert run.dispatched_at is not None
        if now - run.dispatched_at <= lease(run):
            continue
        if run.attempts >= MAX_ATTEMPTS:
            await _fail(db, run, "traceroute_lost", now)
            await db.commit()
        else:
            await _send(db, run, publish, vantage, never_scan, allow_non_public, now)
        changed.append(run)
    await db.commit()

    while True:
        running = await db.scalar(
            select(func.count())
            .select_from(TracerouteRun)
            .where(TracerouteRun.state == TracerouteState.RUNNING)
        )
        if (running or 0) >= ceiling:
            break
        waiting = await db.scalar(
            select(TracerouteRun)
            .where(TracerouteRun.state == TracerouteState.QUEUED)
            .order_by(TracerouteRun.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if waiting is None:
            break
        await _send(db, waiting, publish, vantage, never_scan, allow_non_public, now)
        changed.append(waiting)
    return changed


async def _send(
    db: AsyncSession,
    run: TracerouteRun,
    publish: PublishJob,
    vantage: VantagePoint,
    never_scan: Sequence[scope_rules.ScopeEntry],
    allow_non_public: bool,
    now: datetime,
) -> None:
    """Check the target against the scope as it is now, then publish and commit."""
    project = await db.get(Project, run.project_id, populate_existing=True)
    scope = await _current_scope(db, project) if project is not None else []
    failure: str | None = None
    if project is None or project.deleted_at is not None:
        failure = "project_deleted"
    elif project.state is not ProjectState.ACTIVE:
        failure = "project_archived"
    else:
        target = scope_rules.ScopeEntry("address", str(run.target), run.target, run.target)
        try:
            scope_rules.check_target(
                target, scope=scope, never_scan=never_scan, allow_non_public=allow_non_public
            )
        except scope_rules.ScopeError as error:
            failure = error.code
    if failure is not None:
        await _fail(db, run, "scope_changed", now)
        await record_audit(
            db,
            actor=SYSTEM_ACTOR,
            action="traceroute.rejected",
            object_type="traceroute",
            object_id=run.id,
            details={"point": "orchestrator", "reason": failure, "target": str(run.target)},
        )
        await db.commit()
        log.warning("traceroute_rejected", run_id=run.id, reason=failure)
        return

    assert project is not None and project.current_attestation_id is not None
    parameters = run.parameters
    job = TracerouteJob(
        run_id=run.id,
        target=run.target,
        method=TracerouteMethod(run.method),
        port=parameters.get("port"),
        max_hops=parameters["max_hops"],
        probes_per_hop=parameters["probes_per_hop"],
        timeout_ms=parameters["timeout_ms"],
        pps=PPS,
        attestation_id=project.current_attestation_id,
        scope=[(entry.start, entry.end) for entry in scope],
    )
    # Publish first, then commit: a crash in between leaves the run queued.
    await publish(job)
    run.state = TracerouteState.RUNNING
    run.vantage_point_id = vantage.id
    run.dispatched_at = now
    run.attempts += 1
    await db.commit()
    log.info("traceroute_dispatched", run_id=run.id, attempt=run.attempts)


# --- Recording ----------------------------------------------------------------------


async def _running(db: AsyncSession, run_id: int) -> TracerouteRun | None:
    run = await db.scalar(select(TracerouteRun).where(TracerouteRun.id == run_id).with_for_update())
    if run is None or run.state is not TracerouteState.RUNNING:
        return None
    return run


async def hop_event(db: AsyncSession, run: TracerouteRun, ttl: int | None) -> TracerouteHopEvent:
    """A hop as it is stored now, or with ``ttl`` None, the end of the run."""
    responders: list[HopRow] = []
    lost = 0
    if ttl is not None:
        probes = list(
            await db.scalars(
                select(TracerouteProbe)
                .where(TracerouteProbe.run_id == run.id, TracerouteProbe.ttl == ttl)
                .order_by(TracerouteProbe.probe)
            )
        )
        lost = sum(probe.responder is None for probe in probes)
        addresses = sorted({p.responder for p in probes if p.responder is not None})
        rows = await db.execute(
            select(EnrichmentSnapshot.address, EnrichmentSnapshot.location).where(
                EnrichmentSnapshot.traceroute_run_id == run.id,
                EnrichmentSnapshot.address.in_(addresses),
            )
        )
        points = {address: location for address, location in rows}
        for address in addresses:
            point = points.get(address)
            responders.append(
                HopRow(
                    address=address,
                    rtt_ms=[p.rtt_ms if p.responder == address else None for p in probes],
                    unlocatable=not is_public(address),
                    latitude=point.latitude if point else None,
                    longitude=point.longitude if point else None,
                )
            )
    return TracerouteHopEvent(
        project_id=run.project_id,
        run_id=run.id,
        target=run.target,
        state=run.state.value,
        ttl=ttl,
        responders=responders,
        lost=lost,
    )


async def _record_hop(
    db: AsyncSession, run: TracerouteRun, message: TracerouteHop, enricher: Enricher | None
) -> list[TracerouteHopEvent]:
    now = datetime.now(UTC)
    await db.execute(
        insert(TracerouteProbe).on_conflict_do_nothing(),
        [
            {
                "run_id": run.id,
                "ttl": message.ttl,
                "probe": reply.probe,
                "responder": reply.address,
                "rtt_ms": reply.rtt_ms,
                "reply_kind": reply.kind,
            }
            for reply in message.replies
        ],
    )
    addresses = sorted({reply.address for reply in message.replies if reply.address is not None})
    if addresses:
        # Every responding address is in the global host table (I2).
        hosts = insert(Host)
        await db.execute(
            hosts.on_conflict_do_update(
                index_elements=[Host.address],
                set_={
                    "last_seen_at": func.greatest(Host.last_seen_at, hosts.excluded.last_seen_at)
                },
            ),
            [
                {"address": a, "first_seen_at": now, "last_seen_at": now, "is_public": is_public(a)}
                for a in addresses
            ],
        )
        await db.execute(
            insert(TracerouteResponder).on_conflict_do_nothing(),
            [
                {
                    "run_id": run.id,
                    "address": address,
                    "unlocatable": not is_public(address),
                    # A private address is not sent to any resolver.
                    "ptr_status": None if is_public(address) else "not_public",
                }
                for address in addresses
            ],
        )
        public = [address for address in addresses if is_public(address)]
        if enricher is not None and public:
            await enricher.enrich_hops(db, run.id, public)
    await db.flush()
    return [await hop_event(db, run, message.ttl)]


async def record_traceroute(
    db: AsyncSession, message: TracerouteMessage, enricher: Enricher | None = None
) -> list[TracerouteHopEvent]:
    """Apply one traceroute message of the scanner. Returns the events to
    publish after the commit. A message for a run that is not running is discarded."""
    run = await _running(db, message.run_id)
    if run is None:
        log.info("traceroute_result_discarded", run_id=message.run_id, message_type=message.type)
        return []
    if isinstance(message, TracerouteHop):
        return await _record_hop(db, run, message, enricher)

    now = datetime.now(UTC)
    if isinstance(message, TracerouteDone):
        run.state = TracerouteState.COMPLETED
        run.reached = message.reached
        run.finished_at = now
        log.info("traceroute_completed", run_id=run.id, reached=message.reached)
    else:
        await _fail(db, run, f"rejected: {message.reason}", now)
        await record_audit(
            db,
            actor=SYSTEM_ACTOR,
            action="traceroute.rejected",
            object_type="traceroute",
            object_id=run.id,
            details={"point": "scanner", "reason": message.reason, "target": str(run.target)},
        )
        log.warning("traceroute_rejected", run_id=run.id, reason=message.reason)
    await db.flush()
    return [await hop_event(db, run, None)]


async def fail_lost(db: AsyncSession, run_id: int, reason: str) -> TracerouteRun | None:
    """Fail a run whose job or result was dead-lettered. Does not commit."""
    run = await db.scalar(select(TracerouteRun).where(TracerouteRun.id == run_id).with_for_update())
    if run is None or run.state in (TracerouteState.COMPLETED, TracerouteState.FAILED):
        return None
    await _fail(db, run, reason, datetime.now(UTC))
    return run
