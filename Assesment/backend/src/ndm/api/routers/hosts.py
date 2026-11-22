"""Hosts: the table rows and the map points of a scan or of a whole project,
and the detail of one host."""

import dataclasses
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.api import export
from ndm.api.deps import CurrentSessionDep, DbDep
from ndm.api.errors import ApiError
from ndm.api.pagination import Page, PageParamsDep
from ndm.api.queries.hosts import (
    ChangeStatus,
    HostFilters,
    SortKey,
    Source,
    all_points,
    page_of_hosts,
)
from ndm.api.routers.comparison import previous_scan
from ndm.api.routers.scans import _project, _scan
from ndm.api.schemas.common import ErrorDetail
from ndm.api.schemas.hosts import (
    AnnotationsIn,
    AnnotationsOut,
    ClassificationOut,
    EnrichmentOut,
    EvidenceOut,
    HostDetailOut,
    HostRowOut,
    MapPointOut,
    MapPointsOut,
    OpenPortOut,
    OverrideIn,
    ReverseDnsOut,
    TimelineEntryOut,
    UnlocatedHostOut,
)
from ndm.core.ports import service_name
from ndm.core.scope import ScopeError, parse_address
from ndm.db.audit import record_audit
from ndm.db.models import (
    ClassificationResult,
    EnrichmentSnapshot,
    HostObservation,
    PortObservation,
    ProjectHost,
    Scan,
)
from ndm.orchestrator.classification import UNKNOWN, load_ruleset
from ndm.orchestrator.diff import compute_diff

router = APIRouter(tags=["hosts"])


def host_filters(
    reachable: bool | None = None,
    open_port: Annotated[int | None, Query(ge=1, le=65535)] = None,
    asn: Annotated[int | None, Query(ge=0, le=4_294_967_295)] = None,
    organization: Annotated[str | None, Query(max_length=200)] = None,
    country: Annotated[str | None, Query(pattern="^[A-Za-z]{2}$")] = None,
    ptr: Annotated[str | None, Query(max_length=253)] = None,
    device_type: Annotated[str | None, Query(pattern="^[a-z_]{1,32}$")] = None,
    tag: Annotated[str | None, Query(max_length=64)] = None,
    change: ChangeStatus | None = None,
) -> HostFilters:
    """The filters of the host table and the map, from the query string.

    ``reachable=true`` gives the discovered hosts only. ``organization`` and
    ``ptr`` match a part of the text, in any case. ``change`` is the change
    status relative to the previous scan; it needs a scan.
    """
    return HostFilters(
        reachable=reachable,
        open_port=open_port,
        asn=asn,
        organization=(organization or "").strip() or None,
        country=country,
        ptr=(ptr or "").strip() or None,
        device_type=device_type,
        tag=(tag or "").strip() or None,
        change=change,
    )


FiltersDep = Annotated[HostFilters, Depends(host_filters)]
# ``format=csv`` or ``format=json`` turns a list into a download of every row (FR-EXP-1).
FormatQuery = Annotated[export.ExportFormat | None, Query(alias="format")]

# The change kinds behind each change status. "changed" is any change at all.
_STATUS_KINDS = {"appeared": {"host_appeared"}, "disappeared": {"host_disappeared"}}


async def with_change_status(
    db: AsyncSession, scan: Scan | None, filters: HostFilters
) -> HostFilters:
    """Resolve the change-status filter into the addresses that have that status.

    The scan is compared with the previous one by the diff function (FR-MAP-6,
    architecture 11.3). Only addresses targeted by both scans have a status;
    without a previous scan, no address has one.
    """
    if filters.change is None:
        return filters
    if scan is None:
        raise ApiError(
            422,
            "validation_error",
            "The request is not valid.",
            [
                ErrorDetail(
                    field="change",
                    code="needs_a_scan",
                    message="The change status is relative to a scan; choose a scan first.",
                )
            ],
        )
    base = await previous_scan(db, scan)
    addresses: frozenset[Any] = frozenset()
    if base is not None:
        diff = await compute_diff(db, base.id, scan.id)
        changed = {change.address for change in diff.changes}
        if filters.change == "changed":
            addresses = frozenset(changed)
        elif filters.change == "unchanged":
            addresses = frozenset(set(base.targets) & set(scan.targets)) - changed
        else:
            kinds = _STATUS_KINDS[filters.change]
            addresses = frozenset(c.address for c in diff.changes if c.kind in kinds)
    return dataclasses.replace(filters, addresses=addresses)


Order = Literal["asc", "desc"]


def _row(row: Any) -> HostRowOut:
    point = row.location
    return HostRowOut(
        address=str(row.address),
        scan_id=row.scan_id,
        reachable=row.reachable,
        open_port_count=row.open_port_count,
        closed_count=row.closed_count,
        filtered_count=row.filtered_count,
        ptr_names=row.ptr_names,
        asn=row.asn,
        as_holder=row.as_holder,
        organization=row.organization,
        country=row.country,
        city=row.city,
        latitude=point.latitude if point else None,
        longitude=point.longitude if point else None,
        accuracy_radius_km=row.accuracy_radius_km,
        device_type=row.device_type,
        sub_role=row.sub_role,
        confidence=row.confidence,
        type_override=row.type_override,
        effective_type=row.effective_type,
        tags=row.tags or [],
    )


async def _page(
    db: AsyncSession,
    source: Source,
    filters: HostFilters,
    sort: str,
    order: Order,
    limit: int,
    offset: int,
) -> Page[HostRowOut]:
    rows, total = await page_of_hosts(
        db, source, filters, sort=sort, descending=order == "desc", limit=limit, offset=offset
    )
    return Page(items=[_row(row) for row in rows], total=total, limit=limit, offset=offset)


async def _points(db: AsyncSession, source: Source, filters: HostFilters) -> MapPointsOut:
    located: list[MapPointOut] = []
    unlocated: list[UnlocatedHostOut] = []
    for row in await all_points(db, source, filters):
        if row.location is None:
            # Never dropped: a host without coordinates is listed beside the map (FR-MAP-3).
            unlocated.append(
                UnlocatedHostOut(
                    address=str(row.address),
                    scan_id=row.scan_id,
                    open_port_count=row.open_port_count,
                    effective_type=row.effective_type,
                )
            )
        else:
            located.append(
                MapPointOut(
                    address=str(row.address),
                    scan_id=row.scan_id,
                    open_port_count=row.open_port_count,
                    latitude=row.location.latitude,
                    longitude=row.location.longitude,
                    accuracy_radius_km=row.accuracy_radius_km,
                    effective_type=row.effective_type,
                )
            )
    return MapPointsOut(located=located, unlocated=unlocated)


# --- One scan ----------------------------------------------------------------------


@router.get("/scans/{scan_id}/hosts", response_model=Page[HostRowOut])
async def list_hosts(
    scan_id: int,
    db: DbDep,
    page: PageParamsDep,
    filters: FiltersDep,
    sort: SortKey = "address",
    order: Order = "asc",
    form: FormatQuery = None,
) -> Page[HostRowOut] | Response:
    """The targets of a scan with what was found, filtered, sorted and paged;
    with ``format``, all of them as a file."""
    scan = await _scan(db, scan_id)
    source = Source(project_id=scan.project_id, scan_id=scan.id)
    filters = await with_change_status(db, scan, filters)
    if form is not None:
        rows = await export.hosts(
            db, source, filters, sort=sort, descending=order == "desc", name=f"scan-{scan.id}-hosts"
        )
        return export.response(rows, form)
    return await _page(db, source, filters, sort, order, page.limit, page.offset)


@router.get("/scans/{scan_id}/map-points")
async def scan_map_points(scan_id: int, db: DbDep, filters: FiltersDep) -> MapPointsOut:
    """The hosts of the table of a scan, for the map: all of them, for the same filters."""
    scan = await _scan(db, scan_id)
    filters = await with_change_status(db, scan, filters)
    return await _points(db, Source(project_id=scan.project_id, scan_id=scan.id), filters)


# --- The whole project: the latest state of every address (FR-MAP-5) -----------------


@router.get("/projects/{project_id}/hosts", response_model=Page[HostRowOut])
async def list_project_hosts(
    project_id: int,
    db: DbDep,
    page: PageParamsDep,
    filters: FiltersDep,
    sort: SortKey = "address",
    order: Order = "asc",
    form: FormatQuery = None,
) -> Page[HostRowOut] | Response:
    """Every address of the project as the latest scan that targeted it saw it.
    An export carries the metadata of each scan its rows come from."""
    project = await _project(db, project_id)
    source = Source(project_id=project.id)
    filters = await with_change_status(db, None, filters)
    if form is not None:
        name = f"project-{project.id}-hosts"
        rows = await export.hosts(
            db, source, filters, sort=sort, descending=order == "desc", name=name
        )
        return export.response(rows, form)
    return await _page(db, source, filters, sort, order, page.limit, page.offset)


@router.get("/projects/{project_id}/map-points")
async def project_map_points(project_id: int, db: DbDep, filters: FiltersDep) -> MapPointsOut:
    project = await _project(db, project_id)
    filters = await with_change_status(db, None, filters)
    return await _points(db, Source(project_id=project.id), filters)


# --- One host ----------------------------------------------------------------------


def _probed(port: int, ranges: Any) -> bool:
    return any((span.lower or 0) <= port < (span.upper or 0) for span in ranges)


async def _timeline(db: AsyncSession, scan: Scan, address: Any) -> list[TimelineEntryOut]:
    """Every scan of the project that targeted the address, oldest first.

    The port changes of an entry are against the scan before in which the host
    answered, and only for ports that both scans probed: a port that one of
    them did not look at has not changed.
    """
    observed = (
        await db.execute(
            select(Scan, HostObservation.reachable)
            .join(HostObservation, HostObservation.scan_id == Scan.id)
            .where(
                HostObservation.address == address,
                Scan.project_id == scan.project_id,
                Scan.deleted_at.is_(None),
            )
            .order_by(Scan.id)
        )
    ).all()
    ports: dict[int, set[int]] = {entry.id: set() for entry, _ in observed}
    for scan_id, port in await db.execute(
        select(PortObservation.scan_id, PortObservation.port).where(
            PortObservation.address == address, PortObservation.scan_id.in_(list(ports))
        )
    ):
        ports[scan_id].add(port)

    timeline = []
    previous: Scan | None = None
    for entry, reachable in observed:
        opened: list[int] = []
        closed: list[int] = []
        if previous is not None and reachable:
            both = [
                port
                for port in ports[entry.id] ^ ports[previous.id]
                if _probed(port, entry.probed_ports) and _probed(port, previous.probed_ports)
            ]
            opened = sorted(port for port in both if port in ports[entry.id])
            closed = sorted(port for port in both if port in ports[previous.id])
        timeline.append(
            TimelineEntryOut(
                scan_id=entry.id,
                state=entry.state.value,
                started_at=entry.started_at,
                finished_at=entry.finished_at,
                reachable=reachable,
                open_ports=sorted(ports[entry.id]),
                opened=opened,
                closed=closed,
            )
        )
        if reachable:
            previous = entry
    return timeline


def _enrichment(snapshot: EnrichmentSnapshot) -> EnrichmentOut:
    point = snapshot.location
    return EnrichmentOut(
        run=snapshot.run,
        created_at=snapshot.created_at,
        asn=snapshot.asn,
        as_holder=snapshot.as_holder,
        network_name=snapshot.network_name,
        organization=snapshot.organization,
        organization_source=snapshot.organization_source,
        rir=snapshot.rir,
        registration_country=snapshot.registration_country,
        country=snapshot.country,
        city=snapshot.city,
        latitude=point.latitude if point else None,
        longitude=point.longitude if point else None,
        accuracy_radius_km=snapshot.accuracy_radius_km,
        unavailable=snapshot.unavailable,
        dataset_versions=snapshot.dataset_versions,
    )


@router.get("/scans/{scan_id}/hosts/{address}")
async def get_host(scan_id: int, address: str, db: DbDep) -> HostDetailOut:
    """One host of a scan: evidence, open ports, enrichment with its dataset
    versions, reverse DNS, and the timeline of the address across the scans of
    the project (FR-MAP-9)."""
    scan = await _scan(db, scan_id)
    try:
        parsed = parse_address(address)
    except ScopeError as error:
        raise ApiError(422, "validation_error", error.message) from error
    observation = await db.get(HostObservation, (scan.id, parsed))
    if observation is None:
        raise ApiError(404, "not_found", "The address is not a target of this scan.")

    ports = await db.scalars(
        select(PortObservation.port)
        .where(PortObservation.scan_id == scan.id, PortObservation.address == parsed)
        .order_by(PortObservation.port)
    )
    snapshot = await db.scalar(
        select(EnrichmentSnapshot).where(
            EnrichmentSnapshot.scan_id == scan.id,
            EnrichmentSnapshot.address == parsed,
            EnrichmentSnapshot.latest,
        )
    )
    in_project = await db.get(ProjectHost, (scan.project_id, parsed))
    classified = await db.scalar(
        select(ClassificationResult).where(
            ClassificationResult.scan_id == scan.id,
            ClassificationResult.address == parsed,
            ClassificationResult.latest,
        )
    )
    override = in_project.type_override if in_project else None
    return HostDetailOut(
        scan_id=scan.id,
        address=str(observation.address),
        reachable=observation.reachable,
        evidence=sorted(
            (
                EvidenceOut(probe=probe, at=reply["at"], rtt_ms=reply["rtt_ms"])
                for probe, reply in observation.evidence.items()
            ),
            key=lambda item: (item.at, item.probe),
        ),
        open_ports=[OpenPortOut(port=port, nominal_service=service_name(port)) for port in ports],
        closed_count=observation.closed_count,
        filtered_count=observation.filtered_count,
        enrichment=_enrichment(snapshot) if snapshot is not None else None,
        reverse_dns=ReverseDnsOut(names=observation.ptr_names, status=observation.ptr_status),
        first_seen_at=in_project.first_seen_at if in_project else None,
        last_seen_at=in_project.last_seen_at if in_project else None,
        timeline=await _timeline(db, scan, parsed),
        classification=ClassificationOut(
            run=classified.run,
            created_at=classified.created_at,
            device_type=classified.device_type,
            sub_role=classified.sub_role,
            confidence=classified.confidence,
            rules=classified.rules,
            ruleset_version=classified.ruleset_version,
        )
        if classified is not None
        else None,
        type_override=override,
        effective_type=override or (classified.device_type if classified else None),
        tags=in_project.tags if in_project else [],
        notes=in_project.notes if in_project else None,
    )


# --- The analyst's data about an address in a project (FR-CLS-5, FR-CLS-6) ----------


@lru_cache
def device_types() -> frozenset[str]:
    """The types an override may name: those of the ruleset, and ``unknown``."""
    return frozenset(load_ruleset().taxonomy) | {UNKNOWN}


async def _project_host(db: AsyncSession, project_id: int, address: str) -> ProjectHost:
    project = await _project(db, project_id)
    try:
        parsed = parse_address(address)
    except ScopeError as error:
        raise ApiError(422, "validation_error", error.message) from error
    host = await db.scalar(
        select(ProjectHost)
        .where(ProjectHost.project_id == project.id, ProjectHost.address == parsed)
        .with_for_update()
    )
    if host is None:
        raise ApiError(404, "not_found", "No scan of the project has targeted this address.")
    return host


def _annotations(host: ProjectHost) -> AnnotationsOut:
    return AnnotationsOut(
        address=str(host.address),
        type_override=host.type_override,
        override_at=host.override_at,
        tags=host.tags,
        notes=host.notes,
    )


@router.put("/projects/{project_id}/hosts/{address}/override")
async def set_override(
    project_id: int, address: str, body: OverrideIn, db: DbDep, current: CurrentSessionDep
) -> AnnotationsOut:
    """Set the device type of the address in this project, in every scan of
    it, later scans included. The automatic result is kept and still shown."""
    if body.device_type not in device_types():
        raise ApiError(
            422,
            "validation_error",
            "The request is not valid.",
            [
                ErrorDetail(
                    field="device_type",
                    code="unknown_type",
                    message=f"Use one of: {', '.join(sorted(device_types()))}.",
                )
            ],
        )
    host = await _project_host(db, project_id, address)
    previous = host.type_override
    host.type_override = body.device_type
    host.override_at = datetime.now(UTC)
    await record_audit(
        db,
        actor=current.account.username,
        action="host.override_set",
        object_type="host",
        object_id=f"{project_id}/{host.address}",
        details={"device_type": body.device_type, "previous": previous},
    )
    result = _annotations(host)
    await db.commit()
    return result


@router.delete("/projects/{project_id}/hosts/{address}/override")
async def clear_override(
    project_id: int, address: str, db: DbDep, current: CurrentSessionDep
) -> AnnotationsOut:
    """Clear the override: the automatic result is in effect again."""
    host = await _project_host(db, project_id, address)
    if host.type_override is not None:
        await record_audit(
            db,
            actor=current.account.username,
            action="host.override_cleared",
            object_type="host",
            object_id=f"{project_id}/{host.address}",
            details={"previous": host.type_override},
        )
        host.type_override = None
        host.override_at = None
    result = _annotations(host)
    await db.commit()
    return result


@router.put("/projects/{project_id}/hosts/{address}/annotations")
async def set_annotations(
    project_id: int, address: str, body: AnnotationsIn, db: DbDep
) -> AnnotationsOut:
    """Replace the tags and the notes of the address in this project."""
    tags = sorted({tag.strip() for tag in body.tags if tag.strip()})
    if any(len(tag) > 64 for tag in tags):
        raise ApiError(
            422,
            "validation_error",
            "The request is not valid.",
            [
                ErrorDetail(
                    field="tags", code="too_long", message="A tag has at most 64 characters."
                )
            ],
        )
    host = await _project_host(db, project_id, address)
    host.tags = tags
    host.notes = (body.notes or "").strip() or None
    result = _annotations(host)
    await db.commit()
    return result
