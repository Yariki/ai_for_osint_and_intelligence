"""Scans: list, create, estimate, detail, cancel, delete.

The API never probes. It validates a scan, stores it as ``queued`` and tells
the orchestrator that there is something new.

Creation is the first of the places where the scope is enforced (FR-PRJ-7):
every target must be inside the current project scope, public, and not on the
never-scan list. A rejected attempt is written to the audit log.
"""

import secrets
from datetime import UTC, datetime
from ipaddress import IPv4Address

import structlog
from fastapi import APIRouter, Request
from sqlalchemy import func, select
from sqlalchemy import text as sql
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.api.commands import CommandPublisher
from ndm.api.deps import CurrentSessionDep, DbDep, SettingsDep
from ndm.api.errors import ApiError
from ndm.api.pagination import Page, PageParamsDep
from ndm.api.schemas.common import ErrorDetail
from ndm.api.schemas.scans import (
    MAX_TARGETS,
    EstimateOut,
    EstimateRequest,
    ScanCreate,
    ScanOut,
    to_ranges,
)
from ndm.core import scope as scope_rules
from ndm.core.estimate import estimate
from ndm.core.messages import (
    CancelRequested,
    ClassificationRerunRequested,
    Discovery,
    EnrichmentRerunRequested,
    PhaseRetryRequested,
    ScanCreated,
)
from ndm.db import project_view
from ndm.db.audit import record_audit
from ndm.db.models import (
    FINAL_SCAN_STATES,
    ChunkState,
    HostObservation,
    PortObservation,
    Project,
    ProjectState,
    Scan,
    ScanChunk,
    ScanState,
    ScopeEntry,
    VantagePoint,
)
from ndm.orchestrator.planning import ScanParameters

log = structlog.get_logger(__name__)
router = APIRouter(tags=["scans"])


def _commands(request: Request) -> CommandPublisher:
    publisher: CommandPublisher = request.app.state.commands
    return publisher


# --- Targets ---------------------------------------------------------------------


def _parse_targets(texts: list[str]) -> list[scope_rules.ScopeEntry]:
    """Parse the target entries strictly. Answers 422 with the entry concerned."""
    entries: list[scope_rules.ScopeEntry] = []
    details: list[ErrorDetail] = []
    for index, text in enumerate(texts):
        try:
            entries.append(scope_rules.parse_entry(text))
        except scope_rules.ScopeError as error:
            details.append(
                ErrorDetail(field=f"targets.{index}", code=error.code, message=error.message)
            )
    if details:
        raise ApiError(422, "targets_rejected", "The targets are not valid.", details)
    return entries


def _expand(entries: list[scope_rules.ScopeEntry]) -> list[IPv4Address]:
    """The distinct addresses of the entries, at most 256 of them (FR-CFG-1)."""
    if scope_rules.count_addresses(entries) > MAX_TARGETS:
        raise ApiError(
            422,
            "too_many_targets",
            f"A scan probes at most {MAX_TARGETS} addresses; a /24 is exactly that many.",
        )
    addresses = {
        IPv4Address(number)
        for entry in entries
        for number in range(int(entry.start), int(entry.end) + 1)
    }
    return sorted(addresses)


async def _project(db: AsyncSession, project_id: int) -> Project:
    project = await db.scalar(
        select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
    )
    if project is None:
        raise ApiError(404, "not_found", "The project does not exist.")
    return project


async def _scan(db: AsyncSession, scan_id: int, *, for_update: bool = False) -> Scan:
    """Load a scan, or answer 404. A deleted scan, or one of a deleted project,
    does not exist for any endpoint (I3)."""
    query = (
        select(Scan)
        .join(Project, Project.id == Scan.project_id)
        .where(Scan.id == scan_id, Scan.deleted_at.is_(None), Project.deleted_at.is_(None))
    )
    if for_update:
        query = query.with_for_update(of=Scan)
    scan = await db.scalar(query)
    if scan is None:
        raise ApiError(404, "not_found", "The scan does not exist.")
    return scan


# --- Output ------------------------------------------------------------------------


async def _scan_out(db: AsyncSession, scan: Scan) -> ScanOut:
    counters = scan.counters or {}
    if scan.state in FINAL_SCAN_STATES or "chunks_total" in counters:
        # Probing is over and the chunk rows are gone; their totals were kept on the scan.
        chunks_total, chunks_done = counters.get("chunks_total", 0), counters.get("chunks_done", 0)
    else:
        of_scan = ScanChunk.scan_id == scan.id
        chunks_total = await db.scalar(select(func.count()).select_from(ScanChunk).where(of_scan))
        chunks_done = await db.scalar(
            select(func.count())
            .select_from(ScanChunk)
            .where(of_scan, ScanChunk.state == ChunkState.DONE)
        )
    hosts_reachable = await db.scalar(
        select(func.count())
        .select_from(HostObservation)
        .where(HostObservation.scan_id == scan.id, HostObservation.reachable)
    )
    open_ports = await db.scalar(
        select(func.count()).select_from(PortObservation).where(PortObservation.scan_id == scan.id)
    )
    vantage_point = None
    if scan.vantage_point_id is not None:
        vantage_point = await db.scalar(
            select(VantagePoint.name).where(VantagePoint.id == scan.vantage_point_id)
        )
    return ScanOut(
        id=scan.id,
        project_id=scan.project_id,
        state=scan.state.value,
        phase=scan.phase.value if scan.phase else None,
        partial=scan.partial,
        failure_reason=scan.failure_reason,
        failed_phase=scan.failed_phase.value if scan.failed_phase else None,
        technique_requested=scan.technique_requested,
        technique_used=scan.technique_used,
        icmp_discovery_unavailable=scan.icmp_discovery_unavailable,
        targets=[str(address) for address in scan.targets],
        port_profile=scan.parameters.get("port_profile", "custom"),
        port_count=sum((span.upper or 0) - (span.lower or 0) for span in scan.probed_ports),
        parameters=scan.parameters,
        attestation_id=scan.attestation_id,
        vantage_point=vantage_point,
        created_at=scan.created_at,
        started_at=scan.started_at,
        finished_at=scan.finished_at,
        cancel_requested=scan.cancel_requested_at is not None,
        retry_requested=scan.retry_requested_at is not None,
        enrichment_requested=scan.enrichment_requested_at is not None,
        classification_requested=scan.classification_requested_at is not None,
        dataset_versions=scan.dataset_versions,
        chunks_done=chunks_done or 0,
        chunks_total=chunks_total or 0,
        probes_sent=counters.get("probes_sent", 0),
        receive_drops=counters.get("receive_drops", 0),
        hosts_reachable=hosts_reachable or 0,
        open_ports=open_ports or 0,
    )


# --- Endpoints ---------------------------------------------------------------------


@router.post("/scans/estimate")
async def estimate_scan(body: EstimateRequest) -> EstimateOut:
    """Probes and duration a configuration implies, shown before launch (FR-CFG-9).

    The estimate assumes that every target is port-scanned.
    """
    hosts = len(_expand(_parse_targets(body.targets)))
    ports = len(body.ports())
    result = estimate(hosts, ports, body.pps, body.retries)
    return EstimateOut(
        hosts=hosts,
        ports=ports,
        probes=result.probes,
        max_probes=result.max_probes,
        seconds=result.seconds,
        max_seconds=result.max_seconds,
    )


@router.get("/projects/{project_id}/scans")
async def list_scans(project_id: int, db: DbDep, page: PageParamsDep) -> Page[ScanOut]:
    """The scans of a project, newest first (FR-HIS-1)."""
    project = await _project(db, project_id)
    visible = (Scan.project_id == project.id, Scan.deleted_at.is_(None))
    total = await db.scalar(select(func.count()).select_from(Scan).where(*visible))
    scans = await db.scalars(
        select(Scan).where(*visible).order_by(Scan.id.desc()).limit(page.limit).offset(page.offset)
    )
    return Page(
        items=[await _scan_out(db, scan) for scan in scans.all()],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/projects/{project_id}/scans", status_code=201)
async def create_scan(
    project_id: int,
    body: ScanCreate,
    request: Request,
    current: CurrentSessionDep,
    db: DbDep,
    settings: SettingsDep,
) -> ScanOut:
    project = await _project(db, project_id)
    if project.state is not ProjectState.ACTIVE:
        raise ApiError(409, "project_archived", "The project is archived and read-only.")

    # Parameters above the operator ceiling.
    if body.pps > settings.global_rate_pps:
        raise ApiError(
            422,
            "validation_error",
            "The request is not valid.",
            [
                ErrorDetail(
                    field="pps",
                    code="above_ceiling",
                    message=f"The rate ceiling is {settings.global_rate_pps} packets per second.",
                )
            ],
        )

    entries = _parse_targets(body.targets)
    targets = _expand(entries)

    # The scope check. It fails closed: a project without a scope has no targets
    # that are in scope.
    scope: list[scope_rules.ScopeEntry] = []
    if project.current_attestation_id is not None:
        stored = await db.scalars(
            select(ScopeEntry).where(ScopeEntry.attestation_id == project.current_attestation_id)
        )
        scope = [
            scope_rules.ScopeEntry(
                entry.kind.value, entry.original_text, entry.start_address, entry.end_address
            )
            for entry in stored
        ]
    rejected: list[tuple[int, scope_rules.ScopeError]] = []
    for index, entry in enumerate(entries):
        try:
            scope_rules.check_target(
                entry,
                scope=scope,
                never_scan=request.app.state.never_scan,
                allow_non_public=settings.allow_non_public_targets,
            )
        except scope_rules.ScopeError as error:
            rejected.append((index, error))
    if rejected:
        # Committed before the error is raised: the rejected attempt is the action.
        await record_audit(
            db,
            actor=current.account.username,
            action="scan.rejected",
            object_type="project",
            object_id=project.id,
            details={
                "point": "api",
                "rejected": [
                    {"target": entries[index].text, "reason": error.code}
                    for index, error in rejected
                ],
            },
        )
        await db.commit()
        raise ApiError(
            422,
            "targets_rejected",
            "The scan was not created.",
            [
                ErrorDetail(field=f"targets.{index}", code=error.code, message=error.message)
                for index, error in rejected
            ],
        )
    # Every target is inside the scope, so the project has one.
    assert project.current_attestation_id is not None

    # A forced SYN scan needs a scanner that can do it (FR-CFG-5).
    if body.technique == "syn":
        point = await db.scalar(
            select(VantagePoint).where(VantagePoint.name == settings.vantage_point)
        )
        if point is None or point.capabilities.get("syn") is not True:
            raise ApiError(
                409,
                "syn_unavailable",
                "The scanner has no SYN capability. Use the automatic or the connect technique.",
            )

    discovery = (
        Discovery(icmp_echo=body.discovery.icmp_echo, tcp_ports=body.discovery.tcp_ports)
        if body.discovery is not None
        else None
    )
    parameters = ScanParameters(
        discovery=discovery,
        scan_all_targets=body.scan_all_targets,
        pps=body.pps,
        timeout_ms=body.timeout_ms,
        retries=body.retries,
        hosts_in_parallel=body.hosts_in_parallel,
    )
    scan = Scan(
        project_id=project.id,
        attestation_id=project.current_attestation_id,
        targets=targets,
        probed_ports=to_ranges(body.ports()),
        parameters={**parameters.model_dump(mode="json"), "port_profile": body.port_profile},
        technique_requested=body.technique,
        seed=secrets.randbits(31),
    )
    db.add(scan)
    await db.flush()
    await db.refresh(scan)
    await record_audit(
        db,
        actor=current.account.username,
        action="scan.launch",
        object_type="scan",
        object_id=scan.id,
        details={
            "project_id": project.id,
            "targets": len(targets),
            "port_profile": body.port_profile,
            "technique": body.technique,
            "attestation_id": scan.attestation_id,
        },
    )
    result = await _scan_out(db, scan)
    await db.commit()
    # After the commit. If this is lost, the orchestrator's tick finds the scan.
    await _commands(request).publish(ScanCreated(scan_id=scan.id))
    log.info("scan_created", scan_id=scan.id, project_id=project.id)
    return result


@router.get("/scans/{scan_id}")
async def get_scan(scan_id: int, db: DbDep) -> ScanOut:
    return await _scan_out(db, await _scan(db, scan_id))


@router.post("/scans/{scan_id}/cancel")
async def cancel_scan(
    scan_id: int, request: Request, current: CurrentSessionDep, db: DbDep
) -> ScanOut:
    """Ask for a queued or running scan to stop. Its results so far are kept (FR-SCN-5)."""
    scan = await _scan(db, scan_id, for_update=True)
    if scan.state in FINAL_SCAN_STATES:
        raise ApiError(409, "scan_finished", "The scan has already ended.")
    if scan.cancel_requested_at is None:
        scan.cancel_requested_at = datetime.now(UTC)
        await record_audit(
            db,
            actor=current.account.username,
            action="scan.cancel",
            object_type="scan",
            object_id=scan.id,
        )
    result = await _scan_out(db, scan)
    await db.commit()
    await _commands(request).publish(CancelRequested(scan_id=scan.id))
    return result


@router.post("/scans/{scan_id}/retry-phase", status_code=202)
async def retry_failed_phase(
    scan_id: int, request: Request, current: CurrentSessionDep, db: DbDep
) -> ScanOut:
    """Run the failed phase of a scan again. Nothing is probed again: the scan
    goes on from the phase that failed (NFR-REL-3)."""
    scan = await _scan(db, scan_id, for_update=True)
    if scan.state is not ScanState.FAILED or scan.failed_phase is None:
        raise ApiError(
            409,
            "not_retryable",
            "Only a scan that failed in a phase after probing can be retried.",
        )
    if scan.retry_requested_at is None:
        scan.retry_requested_at = datetime.now(UTC)
        await record_audit(
            db,
            actor=current.account.username,
            action="scan.retry_phase",
            object_type="scan",
            object_id=scan.id,
            details={"phase": scan.failed_phase.value},
        )
    result = await _scan_out(db, scan)
    await db.commit()
    await _commands(request).publish(PhaseRetryRequested(scan_id=scan.id))
    return result


@router.post("/scans/{scan_id}/enrich", status_code=202)
async def rerun_enrichment(
    scan_id: int, request: Request, current: CurrentSessionDep, db: DbDep
) -> ScanOut:
    """Enrich the hosts of a finished scan again, with the datasets as they are
    now. The result is an additional snapshot; earlier ones are kept (FR-ENR-7)."""
    scan = await _scan(db, scan_id, for_update=True)
    if scan.state not in FINAL_SCAN_STATES:
        raise ApiError(
            409, "scan_not_finished", "Enrichment can be run again when the scan has ended."
        )
    if scan.enrichment_requested_at is None:
        scan.enrichment_requested_at = datetime.now(UTC)
        await record_audit(
            db,
            actor=current.account.username,
            action="scan.enrich",
            object_type="scan",
            object_id=scan.id,
        )
    result = await _scan_out(db, scan)
    await db.commit()
    await _commands(request).publish(EnrichmentRerunRequested(scan_id=scan.id))
    return result


@router.post("/scans/{scan_id}/classify", status_code=202)
async def rerun_classification(
    scan_id: int, request: Request, current: CurrentSessionDep, db: DbDep
) -> ScanOut:
    """Classify the hosts of a finished scan again with the current ruleset.
    The result is stored beside the original (FR-CLS-7)."""
    scan = await _scan(db, scan_id, for_update=True)
    if scan.state not in FINAL_SCAN_STATES:
        raise ApiError(
            409, "scan_not_finished", "Classification can be run again when the scan has ended."
        )
    if scan.classification_requested_at is None:
        scan.classification_requested_at = datetime.now(UTC)
        await record_audit(
            db,
            actor=current.account.username,
            action="scan.classify",
            object_type="scan",
            object_id=scan.id,
        )
    result = await _scan_out(db, scan)
    await db.commit()
    await _commands(request).publish(ClassificationRerunRequested(scan_id=scan.id))
    return result


@router.delete("/scans/{scan_id}", status_code=204)
async def delete_scan(
    scan_id: int,
    request: Request,
    current: CurrentSessionDep,
    db: DbDep,
    confirm: bool = False,
) -> None:
    """Delete a scan. It is hidden at once and purged later; the audit entry stays (FR-HIS-9)."""
    if not confirm:
        raise ApiError(400, "confirmation_required", "Deleting a scan needs explicit confirmation.")
    scan = await _scan(db, scan_id, for_update=True)
    scan.deleted_at = datetime.now(UTC)
    # The project-wide view shows each address as its latest scan saw it. Where
    # this scan was the latest, that is now the scan before it, if there is one.
    moved = await db.scalars(
        sql(
            """
            UPDATE project_host AS h
            SET latest_scan_id = (
                SELECT max(o.scan_id)
                FROM host_observation AS o JOIN scan AS s ON s.id = o.scan_id
                WHERE o.address = h.address AND s.project_id = h.project_id
                  AND s.deleted_at IS NULL AND s.id <> :scan
            )
            WHERE h.project_id = :project AND h.latest_scan_id = :scan
            RETURNING h.address
            """
        ),
        {"scan": scan.id, "project": scan.project_id},
    )
    await project_view.refresh(db, project_id=scan.project_id, addresses=list(moved))
    await record_audit(
        db,
        actor=current.account.username,
        action="scan.delete",
        object_type="scan",
        object_id=scan.id,
        details={"project_id": scan.project_id, "state": scan.state.value},
    )
    await db.commit()
    # A scan that is still running is stopped by the orchestrator.
    await _commands(request).publish(CancelRequested(scan_id=scan.id))
