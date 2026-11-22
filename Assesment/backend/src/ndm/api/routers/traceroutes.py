"""Traceroutes: start one for a host of the project, follow it, list them.

The target must be inside the current project scope, public, and not on the
never-scan list; otherwise the request is rejected and the rejection is in the
audit log (FR-TRC-1, check 3.6). A created run is committed together with its
audit entry, and only then is the orchestrator told.
"""

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.api import commands, export
from ndm.api.deps import CurrentSessionDep, DbDep, SettingsDep
from ndm.api.errors import ApiError
from ndm.api.pagination import Page, PageParamsDep
from ndm.api.routers.scans import _project
from ndm.api.schemas.common import ErrorDetail
from ndm.api.schemas.traceroutes import (
    UDP_FIRST_PORT,
    HopOut,
    HopResponderOut,
    ProbeOut,
    TracerouteCreate,
    TracerouteDetailOut,
    TracerouteOut,
    TracerouteParameters,
)
from ndm.core import scope as scope_rules
from ndm.core.messages import TracerouteRequested
from ndm.db.audit import record_audit
from ndm.db.models import (
    EnrichmentSnapshot,
    PortObservation,
    Project,
    ProjectHost,
    ProjectState,
    ScopeEntry,
    TracerouteProbe,
    TracerouteResponder,
    TracerouteRun,
    VantagePoint,
)

log = structlog.get_logger(__name__)
router = APIRouter(tags=["traceroutes"])


async def _scope(db: AsyncSession, project: Project) -> list[scope_rules.ScopeEntry]:
    if project.current_attestation_id is None:
        return []
    stored = await db.scalars(
        select(ScopeEntry).where(ScopeEntry.attestation_id == project.current_attestation_id)
    )
    return [
        scope_rules.ScopeEntry(e.kind.value, e.original_text, e.start_address, e.end_address)
        for e in stored
    ]


async def _open_port(db: AsyncSession, project: Project, address: Any) -> int | None:
    """The lowest open port of the host in the latest scan of the project that targeted it."""
    latest = select(ProjectHost.latest_scan_id).where(
        ProjectHost.project_id == project.id, ProjectHost.address == address
    )
    port: int | None = await db.scalar(
        select(func.min(PortObservation.port)).where(
            PortObservation.scan_id == latest.scalar_subquery(), PortObservation.address == address
        )
    )
    return port


async def _out(db: AsyncSession, run: TracerouteRun) -> TracerouteOut:
    vantage = await db.get(VantagePoint, run.vantage_point_id) if run.vantage_point_id else None
    return TracerouteOut(
        id=run.id,
        project_id=run.project_id,
        target=str(run.target),
        method=run.method,
        parameters=TracerouteParameters(**run.parameters),
        state=run.state.value,
        reached=run.reached,
        failure_reason=run.failure_reason,
        vantage_point=vantage.name if vantage else None,
        created_at=run.created_at,
        dispatched_at=run.dispatched_at,
        finished_at=run.finished_at,
    )


async def _run(db: AsyncSession, run_id: int) -> TracerouteRun:
    """A run, or 404. A run of a deleted project does not exist for any endpoint."""
    run = await db.scalar(
        select(TracerouteRun)
        .join(Project, Project.id == TracerouteRun.project_id)
        .where(TracerouteRun.id == run_id, Project.deleted_at.is_(None))
    )
    if run is None:
        raise ApiError(404, "not_found", "The traceroute does not exist.")
    return run


@router.post("/projects/{project_id}/traceroutes", status_code=201)
async def create_traceroute(
    project_id: int,
    body: TracerouteCreate,
    request: Request,
    current: CurrentSessionDep,
    db: DbDep,
    settings: SettingsDep,
) -> TracerouteOut:
    project = await _project(db, project_id)
    if project.state is not ProjectState.ACTIVE:
        raise ApiError(409, "project_archived", "The project is archived and read-only.")
    try:
        target = scope_rules.parse_entry(body.target)
        if target.start != target.end:
            raise scope_rules.ScopeError(
                "malformed", "A traceroute goes to one address.", body.target
            )
    except scope_rules.ScopeError as error:
        raise ApiError(
            422,
            "validation_error",
            "The request is not valid.",
            [ErrorDetail(field="target", code=error.code, message=error.message)],
        ) from error

    try:
        scope_rules.check_target(
            target,
            scope=await _scope(db, project),
            never_scan=request.app.state.never_scan,
            allow_non_public=settings.allow_non_public_targets,
        )
    except scope_rules.ScopeError as error:
        # Committed before the error is raised: the rejected attempt is the action.
        await record_audit(
            db,
            actor=current.account.username,
            action="traceroute.rejected",
            object_type="project",
            object_id=project.id,
            details={"point": "api", "target": body.target, "reason": error.code},
        )
        await db.commit()
        raise ApiError(
            422,
            "target_rejected",
            "The traceroute was not started.",
            [ErrorDetail(field="target", code=error.code, message=error.message)],
        ) from error
    assert project.current_attestation_id is not None

    port = body.port
    if body.method == "tcp" and port is None:
        port = await _open_port(db, project, target.start)
        if port is None:
            raise ApiError(
                422,
                "validation_error",
                "The request is not valid.",
                [
                    ErrorDetail(
                        field="port",
                        code="no_open_port",
                        message="The host has no known open port. Name the port to use.",
                    )
                ],
            )
    elif body.method == "udp" and port is None:
        port = UDP_FIRST_PORT
    elif body.method == "icmp":
        port = None

    run = TracerouteRun(
        project_id=project.id,
        attestation_id=project.current_attestation_id,
        target=target.start,
        method=body.method,
        parameters={
            "port": port,
            "max_hops": body.max_hops,
            "probes_per_hop": body.probes_per_hop,
            "timeout_ms": body.timeout_ms,
        },
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)
    await record_audit(
        db,
        actor=current.account.username,
        action="traceroute.run",
        object_type="traceroute",
        object_id=run.id,
        details={
            "project_id": project.id,
            "target": str(run.target),
            "method": run.method,
            **run.parameters,
        },
    )
    result = await _out(db, run)
    # The run and its audit entry together; then the command.
    await db.commit()
    await commands.send(request, TracerouteRequested(run_id=run.id))
    log.info("traceroute_created", run_id=run.id, project_id=project.id)
    return result


@router.get("/projects/{project_id}/traceroutes")
async def list_traceroutes(
    project_id: int, db: DbDep, page: PageParamsDep, target: str | None = None
) -> Page[TracerouteOut]:
    """The traceroutes of a project, newest first; with ``target``, of one host
    (its traceroute history, FR-TRC-7)."""
    project = await _project(db, project_id)
    conditions: list[Any] = [TracerouteRun.project_id == project.id]
    if target is not None:
        try:
            conditions.append(TracerouteRun.target == scope_rules.parse_address(target))
        except scope_rules.ScopeError as error:
            raise ApiError(422, "validation_error", error.message) from error
    total = await db.scalar(select(func.count()).select_from(TracerouteRun).where(*conditions))
    runs = await db.scalars(
        select(TracerouteRun)
        .where(*conditions)
        .order_by(TracerouteRun.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return Page(
        items=[await _out(db, run) for run in runs],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/traceroutes/{run_id}", response_model=TracerouteDetailOut)
async def get_traceroute(
    run_id: int,
    db: DbDep,
    form: Annotated[export.ExportFormat | None, Query(alias="format")] = None,
) -> TracerouteDetailOut | Response:
    """A traceroute hop by hop: every responding address with its per-probe
    times, the loss, and the hops that did not answer (FR-TRC-3). With
    ``format``, as a file (FR-EXP-3)."""
    run = await _run(db, run_id)
    probes = list(
        await db.scalars(
            select(TracerouteProbe)
            .where(TracerouteProbe.run_id == run.id)
            .order_by(TracerouteProbe.ttl, TracerouteProbe.probe)
        )
    )
    responders = {
        row.address: row
        for row in await db.scalars(
            select(TracerouteResponder).where(TracerouteResponder.run_id == run.id)
        )
    }
    snapshots = {
        row.address: row
        for row in await db.scalars(
            select(EnrichmentSnapshot).where(EnrichmentSnapshot.traceroute_run_id == run.id)
        )
    }

    hops: list[HopOut] = []
    for ttl in sorted({probe.ttl for probe in probes}):
        of_hop = [probe for probe in probes if probe.ttl == ttl]
        addresses = sorted({p.responder for p in of_hop if p.responder is not None})
        answered: list[HopResponderOut] = []
        for address in addresses:
            responder = responders.get(address)
            snapshot = snapshots.get(address)
            point = snapshot.location if snapshot else None
            answered.append(
                HopResponderOut(
                    address=str(address),
                    rtt_ms=[p.rtt_ms if p.responder == address else None for p in of_hop],
                    unlocatable=responder.unlocatable if responder else True,
                    ptr_names=responder.ptr_names if responder else [],
                    ptr_status=responder.ptr_status if responder else None,
                    asn=snapshot.asn if snapshot else None,
                    as_holder=snapshot.as_holder if snapshot else None,
                    organization=snapshot.organization if snapshot else None,
                    country=snapshot.country if snapshot else None,
                    city=snapshot.city if snapshot else None,
                    latitude=point.latitude if point else None,
                    longitude=point.longitude if point else None,
                    accuracy_radius_km=snapshot.accuracy_radius_km if snapshot else None,
                )
            )
        lost = sum(p.responder is None for p in of_hop)
        hops.append(
            HopOut(
                ttl=ttl,
                responders=answered,
                probes=[
                    ProbeOut(
                        probe=p.probe,
                        address=str(p.responder) if p.responder else None,
                        rtt_ms=p.rtt_ms,
                        kind=p.reply_kind,
                    )
                    for p in of_hop
                ],
                lost=lost,
                silent=lost == len(of_hop),
            )
        )
    detail = TracerouteDetailOut(**(await _out(db, run)).model_dump(), hops=hops)
    if form is None:
        return detail
    # The versions of the reference data the hops were enriched with.
    versions: dict[str, Any] = {}
    for snapshot in snapshots.values():
        versions |= snapshot.dataset_versions or {}
    return export.response(export.traceroute(detail, versions), form)
