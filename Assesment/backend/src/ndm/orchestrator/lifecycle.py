"""Scan lifecycle: from the queue to a final state (architecture sections 6.1 and 6.2).

    queued -> running -> completed
                 |  ^
                 |  +--> interrupted  (returns to running without analyst action)
                 +--> cancelled       (results kept, partial)
                 +--> failed          (results kept, partial)

A running scan probes first (discovery, port scan) and then goes through the
phases after probing, which ``ndm.orchestrator.phases`` runs. A scan that
failed in one of those can be put back into it by the analyst.

Every function works on database state alone and does not commit.
"""

from datetime import UTC, datetime
from ipaddress import IPv4Address

import structlog
from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.messages import AlertCreated, Technique
from ndm.db import project_view
from ndm.db.models import (
    Alert,
    ChunkState,
    HostObservation,
    ProjectHost,
    Scan,
    ScanChunk,
    ScanPhase,
    ScanState,
    Schedule,
    VantagePoint,
)
from ndm.orchestrator import alerts, phases
from ndm.orchestrator.enrichment import usable_versions
from ndm.orchestrator.planning import ChunkSpec, ScanParameters, plan_discovery, plan_port_scan
from ndm.orchestrator.recording import PROBING_PHASES, close_probing, finish_scan

log = structlog.get_logger(__name__)

ACTIVE_STATES = (ScanState.RUNNING, ScanState.INTERRUPTED)
NOT_FINAL = (ScanState.QUEUED, ScanState.RUNNING, ScanState.INTERRUPTED)


def probed_ports(scan: Scan) -> list[int]:
    """The ports of a scan, from its stored multirange."""
    return [port for span in scan.probed_ports for port in range(span.lower or 0, span.upper or 0)]


def _add_chunks(db: AsyncSession, scan: Scan, chunks: list[ChunkSpec]) -> None:
    for chunk in chunks:
        db.add(
            ScanChunk(
                scan_id=scan.id,
                chunk_seq=chunk.chunk_seq,
                phase=ScanPhase(chunk.phase.value),
                spec=chunk.model_dump(mode="json"),
            )
        )


# --- Queue ------------------------------------------------------------------------


async def promote_queued(db: AsyncSession, vantage: VantagePoint, ceiling: int) -> list[Scan]:
    """Start the oldest queued scans while fewer than ``ceiling`` are running or
    interrupted (FR-SCN-6). Manual and scheduled scans share this queue."""
    active = await db.scalar(
        select(func.count())
        .select_from(Scan)
        .where(Scan.state.in_(ACTIVE_STATES), Scan.deleted_at.is_(None))
    )
    started: list[Scan] = []
    while (active or 0) + len(started) < ceiling:
        scan = await db.scalar(
            select(Scan)
            .where(
                Scan.state == ScanState.QUEUED,
                Scan.deleted_at.is_(None),
                Scan.cancel_requested_at.is_(None),
            )
            .order_by(Scan.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if scan is None:
            break
        await _start(db, scan, vantage)
        if scan.state is ScanState.RUNNING:
            started.append(scan)
    return started


async def _start(db: AsyncSession, scan: Scan, vantage: VantagePoint) -> None:
    """Promote one scan to running and create its first chunks."""
    # "automatic" is resolved once, here, from what the scanner can do, and
    # the result is recorded on the scan (FR-CFG-5).
    syn_capable = vantage.capabilities.get("syn") is True
    if scan.technique_requested == Technique.SYN.value and not syn_capable:
        await finish_scan(db, scan, ScanState.FAILED, reason="syn_unavailable")
        return
    wants_syn = scan.technique_requested != Technique.CONNECT.value
    scan.technique_used = (Technique.SYN if wants_syn and syn_capable else Technique.CONNECT).value
    # Without echo requests, discovery rests on the TCP probes alone; the scan
    # says so, as the product owner decided for check 1B.11.
    discovery = scan.parameters.get("discovery")
    scan.icmp_discovery_unavailable = bool(
        discovery and discovery.get("icmp_echo") and vantage.capabilities.get("icmp") is not True
    )
    scan.vantage_point_id = vantage.id
    scan.state = ScanState.RUNNING
    scan.started_at = datetime.now(UTC)
    # The reference data the scan starts with, for the record (FR-SCN-8).
    scan.dataset_versions = await usable_versions(db)

    # One observation row per target, all unreachable. Findings only ever
    # update these rows, so nothing outside the target list can be recorded.
    await db.execute(
        insert(HostObservation).on_conflict_do_nothing(),
        [
            {"scan_id": scan.id, "address": address, "vantage_point_id": vantage.id}
            for address in scan.targets
        ],
    )
    # The project remembers the latest scan that targeted each address.
    project_hosts = insert(ProjectHost)
    await db.execute(
        project_hosts.on_conflict_do_update(
            index_elements=[ProjectHost.project_id, ProjectHost.address],
            set_={
                "latest_scan_id": func.greatest(
                    ProjectHost.latest_scan_id, project_hosts.excluded.latest_scan_id
                )
            },
        ),
        [
            {"project_id": scan.project_id, "address": address, "latest_scan_id": scan.id}
            for address in scan.targets
        ],
    )
    await project_view.refresh(db, scan_id=scan.id)

    parameters = ScanParameters.model_validate(scan.parameters)
    if parameters.discovery is not None:
        scan.phase = ScanPhase.DISCOVERY
        _add_chunks(db, scan, [plan_discovery(scan.targets, parameters.discovery, seed=scan.seed)])
    else:
        # Discovery is skipped: every target is port-scanned.
        scan.phase = ScanPhase.PORT_SCAN
        _add_chunks(db, scan, _port_scan_chunks(scan, parameters, list(scan.targets), first_seq=0))
    await db.flush()
    log.info("scan_started", scan_id=scan.id, technique=scan.technique_used)


def _port_scan_chunks(
    scan: Scan, parameters: ScanParameters, hosts: list[IPv4Address], first_seq: int
) -> list[ChunkSpec]:
    return plan_port_scan(
        hosts,
        probed_ports(scan),
        seed=scan.seed,
        hosts_in_parallel=parameters.hosts_in_parallel,
        first_seq=first_seq,
    )


# --- Phases -----------------------------------------------------------------------


async def advance(db: AsyncSession, scan: Scan) -> bool:
    """Move a running scan on when every chunk of its phase is done.

    After discovery the port-scan chunks are created, for the hosts that
    answered or for all targets (FR-CFG-3). After the port scan come the
    phases after probing; if there is none, the scan is complete. Returns True
    if the scan changed.
    """
    if scan.phase not in PROBING_PHASES:
        return False  # in a phase after probing: the phase runner moves it on
    open_chunks = await db.scalar(
        select(func.count())
        .select_from(ScanChunk)
        .where(ScanChunk.scan_id == scan.id, ScanChunk.state != ChunkState.DONE)
    )
    if open_chunks:
        return False

    if scan.phase is ScanPhase.DISCOVERY:
        parameters = ScanParameters.model_validate(scan.parameters)
        if parameters.scan_all_targets:
            hosts = list(scan.targets)
        else:
            hosts = list(
                await db.scalars(
                    select(HostObservation.address)
                    .where(HostObservation.scan_id == scan.id, HostObservation.reachable)
                    .order_by(HostObservation.address)
                )
            )
        chunks = _port_scan_chunks(scan, parameters, hosts, first_seq=1)
        if chunks:
            scan.phase = ScanPhase.PORT_SCAN
            _add_chunks(db, scan, chunks)
            await db.flush()
            return True

    # Nothing left to probe.
    following = phases.first_phase(scan)
    if following is None:
        await finish_scan(db, scan, ScanState.COMPLETED)
        log.info("scan_completed", scan_id=scan.id)
        return True
    await close_probing(db, scan)
    # The phases need no scanner, so an interrupted scan is running again.
    scan.state = ScanState.RUNNING
    scan.phase = following
    await db.flush()
    return True


# --- Interruption and cancellation -------------------------------------------------


async def mark_interrupted(db: AsyncSession) -> list[Scan]:
    """Mark every scan that is probing as interrupted.

    Called when the scanner's heartbeat has been missing, and at start-up for
    every scan found running. A scan returns to running with its next finding
    or chunk result. A scan in a phase after probing does not depend on the
    scanner and is left as it is.
    """
    scans = (
        await db.scalars(
            select(Scan).where(
                Scan.state == ScanState.RUNNING,
                Scan.phase.in_(PROBING_PHASES),
                Scan.deleted_at.is_(None),
            )
        )
    ).all()
    if scans:
        await db.execute(
            update(Scan)
            .where(Scan.id.in_([scan.id for scan in scans]))
            .values(state=ScanState.INTERRUPTED)
        )
        for scan in scans:
            log.warning("scan_interrupted", scan_id=scan.id)
    return list(scans)


async def cancel_requested(db: AsyncSession) -> list[Scan]:
    """End every scan whose cancellation or deletion was asked for.

    The results collected so far are kept and the scan is marked partial
    (FR-SCN-5). Returns the scans, so that the scanner can be told to stop.
    """
    scans = (
        await db.scalars(
            select(Scan)
            .where(
                Scan.state.in_(NOT_FINAL),
                Scan.cancel_requested_at.is_not(None) | Scan.deleted_at.is_not(None),
            )
            .with_for_update()
        )
    ).all()
    for scan in scans:
        await finish_scan(db, scan, ScanState.CANCELLED)
        log.info("scan_cancelled", scan_id=scan.id)
    return list(scans)


# --- Scheduled runs ----------------------------------------------------------------


async def alert_failed_scheduled_runs(db: AsyncSession) -> list[AlertCreated]:
    """Raise one alert for each scheduled scan that has failed and has none yet
    (FR-HIS-7). Does not commit.

    A scan fails in several places (a lost chunk, a phase, a dead letter, a
    changed scope); this one pass over the database covers them all.
    """
    alerted = exists().where(
        Alert.type == alerts.SCHEDULED_RUN_FAILED,
        Alert.link["scan_id"].as_integer() == Scan.id,
    )
    failed = await db.execute(
        select(Scan, Schedule.name)
        .join(Schedule, Schedule.id == Scan.schedule_id)
        .where(Scan.state == ScanState.FAILED, Scan.deleted_at.is_(None), ~alerted)
        .order_by(Scan.id)
    )
    raised = []
    for scan, name in failed:
        raised.append(
            await alerts.raise_alert(
                db,
                alerts.SCHEDULED_RUN_FAILED,
                f"{name}: scheduled scan {scan.id} failed ({scan.failure_reason}).",
                project_id=scan.project_id,
                link={"scan_id": scan.id, "schedule_id": scan.schedule_id},
            )
        )
    return raised
