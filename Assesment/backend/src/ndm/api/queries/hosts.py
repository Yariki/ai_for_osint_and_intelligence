"""The host query: one builder for the table rows, the map points and the export.

They are the same selection of hosts, shown different ways. They are built from
the same source and the same filters here, so a filter can never apply to one
and not to the other (FR-MAP-6, FR-MAP-8).

The source is either one scan, or the latest known state of a whole project:
for every address, its observation in the latest scan that targeted it
(FR-MAP-5). For one scan, a host row is its observation joined to its current
enrichment snapshot and its current classification, if it has them. For the
project, it is `project_host` alone, which holds a copy of the same fields of
the latest observation (`ndm.db.project_view`): joining to the observations
there is too slow at the sizing envelope (TASK-078). Either way the row
carries what the analyst noted about the address in the project: the type
override, tags and notes (FR-CLS-5, FR-CLS-6).

The change status of a host is not a column: it comes from the diff of the
scan with the previous one. The router computes that diff and passes the
addresses with the asked-for status; here they are one more condition.
"""

from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import IPv4Address
from typing import Any, Literal

from sqlalchemy import ColumnElement, Select, and_, any_, exists, func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import (
    ClassificationResult,
    EnrichmentSnapshot,
    HostObservation,
    PortObservation,
    ProjectHost,
    Scan,
)

SortKey = Literal[
    "address",
    "reachable",
    "open_ports",
    "closed",
    "filtered",
    "country",
    "asn",
    "organization",
    "device_type",
]

ChangeStatus = Literal["changed", "appeared", "disappeared", "unchanged"]


@dataclass(frozen=True)
class Fields:
    """Where each field of a host row is read from."""

    address: Any
    scan_id: Any
    reachable: Any
    open_port_count: Any
    open_ports: Any
    closed_count: Any
    filtered_count: Any
    ptr_names: Any
    asn: Any
    as_holder: Any
    network_name: Any
    organization: Any
    rir: Any
    registration_country: Any
    country: Any
    city: Any
    location: Any
    accuracy_radius_km: Any
    device_type: Any
    sub_role: Any
    confidence: Any
    has_open_port: Callable[[int], ColumnElement[bool]]

    @property
    def effective_type(self) -> Any:
        """The device type the analyst sees: the override if there is one,
        otherwise the automatic result (FR-CLS-5). Filters and sorting use this one."""
        return func.coalesce(ProjectHost.type_override, self.device_type)


# One scan: the observation and what is joined to it.
SCAN_FIELDS = Fields(
    address=HostObservation.address,
    scan_id=HostObservation.scan_id,
    reachable=HostObservation.reachable,
    open_port_count=select(func.count())
    .select_from(PortObservation)
    .where(
        PortObservation.scan_id == HostObservation.scan_id,
        PortObservation.address == HostObservation.address,
    )
    .scalar_subquery(),
    open_ports=select(
        func.array_agg(aggregate_order_by(PortObservation.port, PortObservation.port))
    )
    .where(
        PortObservation.scan_id == HostObservation.scan_id,
        PortObservation.address == HostObservation.address,
    )
    .scalar_subquery(),
    closed_count=HostObservation.closed_count,
    filtered_count=HostObservation.filtered_count,
    ptr_names=HostObservation.ptr_names,
    asn=EnrichmentSnapshot.asn,
    as_holder=EnrichmentSnapshot.as_holder,
    network_name=EnrichmentSnapshot.network_name,
    organization=EnrichmentSnapshot.organization,
    rir=EnrichmentSnapshot.rir,
    registration_country=EnrichmentSnapshot.registration_country,
    country=EnrichmentSnapshot.country,
    city=EnrichmentSnapshot.city,
    location=EnrichmentSnapshot.location,
    accuracy_radius_km=EnrichmentSnapshot.accuracy_radius_km,
    device_type=ClassificationResult.device_type,
    sub_role=ClassificationResult.sub_role,
    confidence=ClassificationResult.confidence,
    has_open_port=lambda port: exists().where(
        PortObservation.scan_id == HostObservation.scan_id,
        PortObservation.address == HostObservation.address,
        PortObservation.port == port,
    ),
)

# The project: the copy of the latest observation on project_host.
PROJECT_FIELDS = Fields(
    address=ProjectHost.address,
    scan_id=ProjectHost.latest_scan_id,
    reachable=ProjectHost.reachable,
    open_port_count=func.cardinality(ProjectHost.open_ports),
    open_ports=ProjectHost.open_ports,
    closed_count=ProjectHost.closed_count,
    filtered_count=ProjectHost.filtered_count,
    ptr_names=ProjectHost.ptr_names,
    asn=ProjectHost.asn,
    as_holder=ProjectHost.as_holder,
    network_name=ProjectHost.network_name,
    organization=ProjectHost.organization,
    rir=ProjectHost.rir,
    registration_country=ProjectHost.registration_country,
    country=ProjectHost.country,
    city=ProjectHost.city,
    location=ProjectHost.location,
    accuracy_radius_km=ProjectHost.accuracy_radius_km,
    device_type=ProjectHost.device_type,
    sub_role=ProjectHost.sub_role,
    confidence=ProjectHost.confidence,
    has_open_port=lambda port: any_(ProjectHost.open_ports) == port,
)


def sort_key(fields: Fields, sort: str) -> Any:
    return {
        "device_type": fields.effective_type,
        "address": fields.address,
        "reachable": fields.reachable,
        "open_ports": fields.open_port_count,
        "closed": fields.closed_count,
        "filtered": fields.filtered_count,
        "country": fields.country,
        "asn": fields.asn,
        "organization": fields.organization,
    }[sort]


def row_columns(fields: Fields) -> tuple[Any, ...]:
    """The columns of a table row, named as the row's fields."""
    return (
        fields.address.label("address"),
        fields.scan_id.label("scan_id"),
        fields.reachable.label("reachable"),
        fields.open_port_count.label("open_port_count"),
        fields.closed_count.label("closed_count"),
        fields.filtered_count.label("filtered_count"),
        fields.ptr_names.label("ptr_names"),
        fields.asn.label("asn"),
        fields.as_holder.label("as_holder"),
        fields.organization.label("organization"),
        fields.country.label("country"),
        fields.city.label("city"),
        fields.location.label("location"),
        fields.accuracy_radius_km.label("accuracy_radius_km"),
        fields.device_type.label("device_type"),
        fields.sub_role.label("sub_role"),
        fields.confidence.label("confidence"),
        ProjectHost.type_override,
        fields.effective_type.label("effective_type"),
        ProjectHost.tags,
    )


def point_columns(fields: Fields) -> tuple[Any, ...]:
    return (
        fields.address.label("address"),
        fields.scan_id.label("scan_id"),
        fields.open_port_count.label("open_port_count"),
        fields.location.label("location"),
        fields.accuracy_radius_km.label("accuracy_radius_km"),
        fields.effective_type.label("effective_type"),
    )


def fields_of(source: Source) -> Fields:
    return SCAN_FIELDS if source.scan_id is not None else PROJECT_FIELDS


@dataclass(frozen=True)
class Source:
    """Where the hosts come from: one scan of a project, or, without a scan,
    the latest state of the project."""

    project_id: int
    scan_id: int | None = None


@dataclass(frozen=True)
class HostFilters:
    reachable: bool | None = None
    open_port: int | None = None
    asn: int | None = None
    organization: str | None = None
    """Part of the organization's name, in any case."""
    country: str | None = None
    """Country code of the location."""
    ptr: str | None = None
    """Part of a reverse-DNS name, in any case."""
    device_type: str | None = None
    """The effective device type: the override if set, otherwise the automatic one."""
    tag: str | None = None
    change: ChangeStatus | None = None
    """Change status relative to the previous scan (FR-MAP-6)."""
    addresses: frozenset[IPv4Address] | None = None
    """The addresses that have the asked-for change status, computed from the
    diff by the caller. Required when ``change`` is set."""


def _contains(text: str) -> str:
    """A LIKE pattern for "contains this text", with LIKE's own wildcards taken literally."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _conditions(fields: Fields, filters: HostFilters) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if filters.reachable is not None:
        conditions.append(fields.reachable == filters.reachable)
    if filters.open_port is not None:
        conditions.append(fields.has_open_port(filters.open_port))
    if filters.asn is not None:
        conditions.append(fields.asn == filters.asn)
    if filters.organization:
        conditions.append(fields.organization.ilike(_contains(filters.organization), escape="\\"))
    if filters.country:
        conditions.append(fields.country == filters.country.upper())
    if filters.ptr:
        # A name holds no line break, so a match cannot run from one name into the next.
        names = func.array_to_string(fields.ptr_names, "\n")
        conditions.append(names.ilike(_contains(filters.ptr), escape="\\"))
    if filters.device_type:
        conditions.append(fields.effective_type == filters.device_type)
    if filters.tag:
        conditions.append(ProjectHost.tags.contains([filters.tag]))
    if filters.change is not None:
        assert filters.addresses is not None, "the change status needs its addresses"
        conditions.append(fields.address.in_(sorted(filters.addresses)))
    return conditions


def select_hosts(*columns: Any, source: Source, filters: HostFilters) -> Select[Any]:
    """``SELECT columns`` over the hosts of the source that pass the filters.

    Every host query of the API is built by this one function. The columns are
    taken from ``fields_of(source)``.
    """
    fields = fields_of(source)
    if source.scan_id is not None:
        query = (
            select(*columns)
            .select_from(HostObservation)
            .outerjoin(
                EnrichmentSnapshot,
                and_(
                    EnrichmentSnapshot.scan_id == HostObservation.scan_id,
                    EnrichmentSnapshot.address == HostObservation.address,
                    EnrichmentSnapshot.latest,
                ),
            )
            .outerjoin(
                ClassificationResult,
                and_(
                    ClassificationResult.scan_id == HostObservation.scan_id,
                    ClassificationResult.address == HostObservation.address,
                    ClassificationResult.latest,
                ),
            )
            # The analyst's override, tags and notes belong to the address in the project.
            .outerjoin(
                ProjectHost,
                and_(
                    ProjectHost.project_id == source.project_id,
                    ProjectHost.address == HostObservation.address,
                ),
            )
            .where(HostObservation.scan_id == source.scan_id)
        )
    else:
        # The project remembers the latest scan that targeted each address,
        # and a copy of what that scan observed there.
        query = (
            select(*columns)
            .select_from(ProjectHost)
            .join(Scan, Scan.id == ProjectHost.latest_scan_id)
            .where(
                ProjectHost.project_id == source.project_id,
                ProjectHost.reachable.is_not(None),
                Scan.deleted_at.is_(None),
            )
        )
    return query.where(*_conditions(fields, filters))


async def page_of_hosts(
    db: AsyncSession,
    source: Source,
    filters: HostFilters,
    *,
    sort: str,
    descending: bool,
    limit: int,
    offset: int,
) -> tuple[list[Any], int]:
    """One page of table rows, and the number of hosts that pass the filters."""
    total = await db.scalar(select_hosts(func.count(), source=source, filters=filters))
    fields = fields_of(source)
    key = sort_key(fields, sort)
    # Hosts without a value come last, whichever way the column is sorted. The
    # address is unique within a source; as the last key it makes the order
    # total, so no row is repeated or missed from one page to the next.
    ordering = [
        (key.desc() if descending else key.asc()).nulls_last(),
        fields.address.asc(),
    ]
    rows = await db.execute(
        select_hosts(*row_columns(fields), source=source, filters=filters)
        .order_by(*ordering)
        .limit(limit)
        .offset(offset)
    )
    return list(rows), total or 0


async def all_points(db: AsyncSession, source: Source, filters: HostFilters) -> list[Any]:
    """Every host that passes the filters, slim, for the map. Not paged: the map
    needs all of them to cluster."""
    fields = fields_of(source)
    rows = await db.execute(
        select_hosts(*point_columns(fields), source=source, filters=filters).order_by(
            fields.address
        )
    )
    return list(rows)
