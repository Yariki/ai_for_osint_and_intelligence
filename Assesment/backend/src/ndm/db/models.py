"""ORM models (architecture section 10.1).

Keys are ``bigint`` identities (AD7) and every timestamp is ``timestamptz``.
The models carry no relationships: queries name what they load.
"""

import enum
from collections.abc import Callable
from datetime import date, datetime
from ipaddress import IPv4Address
from typing import Any, NamedTuple

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ColumnElement,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, INT4MULTIRANGE, INT8RANGE, JSONB, Range
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import UserDefinedType


class Inet(INET):
    """The ``inet`` type, with its parameters always cast to ``inet``.

    Without the cast, a statement that writes several rows at once sends the
    addresses as untyped parameters, and the driver then refuses the address
    objects. Every address column uses this type.
    """

    render_bind_cast = True
    cache_ok = True


class GeoPoint(NamedTuple):
    latitude: float
    longitude: float


class Geography(UserDefinedType[GeoPoint]):
    """A PostGIS ``geography(Point, 4326)`` column, read and written as a ``GeoPoint``.

    PostGIS is used for this one type (architecture 10.2). The value travels
    as text: ``SRID=4326;POINT(longitude latitude)`` in, ``POINT(...)`` out.
    """

    cache_ok = True

    def get_col_spec(self, **_: Any) -> str:
        return "geography(Point,4326)"

    def bind_expression(self, bindvalue: Any) -> ColumnElement[Any]:
        return func.ST_GeogFromText(bindvalue, type_=self)

    def column_expression(self, column: Any) -> ColumnElement[Any]:
        return func.ST_AsText(column, type_=self)

    def bind_processor(self, dialect: Dialect) -> Callable[[GeoPoint | None], str | None]:
        def to_text(value: GeoPoint | None) -> str | None:
            if value is None:
                return None
            latitude, longitude = value
            return f"SRID=4326;POINT({float(longitude)!r} {float(latitude)!r})"

        return to_text

    def result_processor(
        self, dialect: Dialect, coltype: object
    ) -> Callable[[str | None], GeoPoint | None]:
        def to_point(value: str | None) -> GeoPoint | None:
            if value is None:
                return None
            longitude, latitude = value.removeprefix("POINT(").removesuffix(")").split()
            return GeoPoint(latitude=float(latitude), longitude=float(longitude))

        return to_point


class Base(DeclarativeBase):
    # Fixed constraint names, so a later migration can refer to them.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def _identity() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(always=True), primary_key=True)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _enum(python_enum: type[enum.Enum], name: str) -> Enum:
    """A PostgreSQL enum that stores the members' values."""
    return Enum(python_enum, name=name, values_callable=lambda members: [m.value for m in members])


class ProjectState(enum.StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ScopeEntryKind(enum.StrEnum):
    ADDRESS = "address"
    CIDR = "cidr"
    RANGE = "range"


class Account(Base):
    """The single analyst (D1)."""

    __tablename__ = "account"

    id: Mapped[int] = _identity()
    username: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class UserSession(Base):
    """A login session, keyed by the hash of its token. The token is not stored."""

    __tablename__ = "session"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = _created_at()
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Project(Base):
    __tablename__ = "project"

    id: Mapped[int] = _identity()
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default="")
    state: Mapped[ProjectState] = mapped_column(
        _enum(ProjectState, "project_state"), server_default=ProjectState.ACTIVE.value
    )
    # The attestation that holds the current scope; null until a scope is saved.
    current_attestation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("attestation.id", ondelete="SET NULL", use_alter=True)
    )
    created_at: Mapped[datetime] = _created_at()
    # Set by deletion: the project is hidden at once and purged later (I3).
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Attestation(Base):
    """The analyst's confirmation for one version of a scope. Never updated."""

    __tablename__ = "attestation"

    id: Mapped[int] = _identity()
    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("account.id"))
    created_at: Mapped[datetime] = _created_at()


class ScopeEntry(Base):
    """One entry of an attested scope, with the text exactly as the analyst wrote it."""

    __tablename__ = "scope_entry"

    id: Mapped[int] = _identity()
    attestation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("attestation.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[ScopeEntryKind] = mapped_column(_enum(ScopeEntryKind, "scope_entry_kind"))
    original_text: Mapped[str] = mapped_column(Text)
    start_address: Mapped[IPv4Address] = mapped_column(Inet)
    end_address: Mapped[IPv4Address] = mapped_column(Inet)


class AuditEvent(Base):
    """Append-only audit log. No foreign keys, so entries survive deletions."""

    __tablename__ = "audit_event"
    __table_args__ = (Index("ix_audit_event_occurred_at", "occurred_at"),)

    id: Mapped[int] = _identity()
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    actor: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    object_type: Mapped[str] = mapped_column(Text)
    object_id: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


# --- Scan pipeline (Phase 1B) ---------------------------------------------------


class ScanState(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


# States a scan never leaves. Its observations are immutable from then on (FR-SCN-9).
FINAL_SCAN_STATES = frozenset({ScanState.COMPLETED, ScanState.CANCELLED, ScanState.FAILED})


class ScanPhase(enum.StrEnum):
    DISCOVERY = "discovery"
    PORT_SCAN = "port_scan"
    ENRICHMENT = "enrichment"
    CLASSIFICATION = "classification"
    DIFF = "diff"


class ChunkState(enum.StrEnum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    DONE = "done"


def _jsonb() -> Mapped[dict[str, Any]]:
    return mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class VantagePoint(Base):
    """Where scans are made from. Version 1 has exactly one (C-4)."""

    __tablename__ = "vantage_point"

    id: Mapped[int] = _identity()
    name: Mapped[str] = mapped_column(Text, unique=True)
    egress_address: Mapped[IPv4Address | None] = mapped_column(Inet)
    # What the scanner reported in its last hello or heartbeat.
    capabilities: Mapped[dict[str, Any]] = _jsonb()
    counters: Mapped[dict[str, Any]] = _jsonb()
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Scan(Base):
    __tablename__ = "scan"
    __table_args__ = (Index("ix_scan_state", "state"),)

    id: Mapped[int] = _identity()
    project_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("project.id"), index=True)
    # The schedule that started the scan; None for a scan the analyst launched.
    schedule_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("schedule.id", ondelete="SET NULL"), index=True
    )
    # The scope the scan was validated against (FR-SCN-8).
    attestation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("attestation.id"))
    vantage_point_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("vantage_point.id"))
    state: Mapped[ScanState] = mapped_column(
        _enum(ScanState, "scan_state"), server_default=ScanState.QUEUED.value
    )
    phase: Mapped[ScanPhase | None] = mapped_column(_enum(ScanPhase, "scan_phase"))
    targets: Mapped[list[IPv4Address]] = mapped_column(ARRAY(Inet))
    probed_ports: Mapped[list[Range[int]]] = mapped_column(INT4MULTIRANGE)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    technique_requested: Mapped[str] = mapped_column(Text)
    technique_used: Mapped[str | None] = mapped_column(Text)
    # ICMP discovery was asked for but the scanner could not send echo requests
    # (no NET_RAW): discovery rested on the TCP probes alone, and a host that
    # answers only ping was not found. Set when the scan starts.
    icmp_discovery_unavailable: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    seed: Mapped[int] = mapped_column(BigInteger)
    # The versions of the reference datasets when the scan started (FR-SCN-8).
    dataset_versions: Mapped[dict[str, Any]] = _jsonb()
    counters: Mapped[dict[str, Any]] = _jsonb()
    partial: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    # The phase after probing that failed, while it can be retried (NFR-REL-3).
    failed_phase: Mapped[ScanPhase | None] = mapped_column(_enum(ScanPhase, "scan_phase"))
    created_at: Mapped[datetime] = _created_at()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Requests of the analyst that the orchestrator carries out and then clears.
    # They are state, not messages: a lost command does not lose the request (AD1).
    retry_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enrichment_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    classification_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScanChunk(Base):
    """A unit of work of a running scan. Deleted when the scan ends."""

    __tablename__ = "scan_chunk"

    scan_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("scan.id", ondelete="CASCADE"), primary_key=True
    )
    chunk_seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    phase: Mapped[ScanPhase] = mapped_column(_enum(ScanPhase, "scan_phase"))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state: Mapped[ChunkState] = mapped_column(
        _enum(ChunkState, "chunk_state"), server_default=ChunkState.PENDING.value
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Host(Base):
    """Global registry of addresses (I1): one row per address, across all projects."""

    __tablename__ = "host"

    address: Mapped[IPv4Address] = mapped_column(Inet, primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_public: Mapped[bool] = mapped_column(Boolean)


class ProjectHost(Base):
    """An address within a project: where its per-project data hangs."""

    __tablename__ = "project_host"

    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("project.id", ondelete="CASCADE"), primary_key=True
    )
    address: Mapped[IPv4Address] = mapped_column(Inet, primary_key=True)
    # Null while the address has been targeted but has never answered.
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The latest scan that targeted the address; the project-wide view starts here.
    latest_scan_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("scan.id", ondelete="SET NULL")
    )
    # The analyst's own data about the address in this project (FR-CLS-5, FR-CLS-6).
    # It never changes what a scan observed.
    type_override: Mapped[str | None] = mapped_column(Text)
    override_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    notes: Mapped[str | None] = mapped_column(Text)
    # A copy of what the project-wide view shows of the observation in the
    # latest scan, with its open ports, latest enrichment snapshot and latest
    # classification (FR-MAP-5). Written only by ndm.db.project_view.refresh;
    # null when the address has no observation in a scan that still exists.
    reachable: Mapped[bool | None] = mapped_column(Boolean)
    open_ports: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    closed_count: Mapped[int | None] = mapped_column(Integer)
    filtered_count: Mapped[int | None] = mapped_column(Integer)
    ptr_names: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    asn: Mapped[int | None] = mapped_column(BigInteger)
    as_holder: Mapped[str | None] = mapped_column(Text)
    network_name: Mapped[str | None] = mapped_column(Text)
    organization: Mapped[str | None] = mapped_column(Text)
    rir: Mapped[str | None] = mapped_column(Text)
    registration_country: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    accuracy_radius_km: Mapped[int | None] = mapped_column(Integer)
    device_type: Mapped[str | None] = mapped_column(Text)
    sub_role: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    location: Mapped[GeoPoint | None] = mapped_column(Geography)


class HostObservation(Base):
    """One target of one scan, reachable or not. Hash-partitioned by scan (AD8)."""

    __tablename__ = "host_observation"
    __table_args__ = (
        # Serves the host timeline and the global host view.
        Index("ix_host_observation_address_scan_id", "address", "scan_id"),
        {"postgresql_partition_by": "HASH (scan_id)"},
    )

    scan_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("scan.id"), primary_key=True)
    address: Mapped[IPv4Address] = mapped_column(Inet, primary_key=True)
    vantage_point_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("vantage_point.id"))
    reachable: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # Keyed by probe ("icmp", "tcp:443"); each value has the time and the round-trip time.
    evidence: Mapped[dict[str, Any]] = _jsonb()
    closed_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    filtered_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    # Reverse DNS, stored with the scan (FR-ENR-4). The status is null until
    # the lookup has been made or skipped.
    ptr_names: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    ptr_status: Mapped[str | None] = mapped_column(Text)


class PortObservation(Base):
    """An open port. One row per port and no other column; only the primary key is indexed."""

    __tablename__ = "port_observation"
    __table_args__ = (
        CheckConstraint("port BETWEEN 1 AND 65535", name="port_range"),
        {"postgresql_partition_by": "HASH (scan_id)"},
    )

    scan_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("scan.id"), primary_key=True)
    address: Mapped[IPv4Address] = mapped_column(Inet, primary_key=True)
    port: Mapped[int] = mapped_column(Integer, primary_key=True)


# --- Enrichment and reference datasets (Phase 2) ---------------------------------


class ReferenceDataset(Base):
    """One reference dataset: its version, its age and whether its last update worked."""

    __tablename__ = "reference_dataset"

    id: Mapped[int] = _identity()
    name: Mapped[str] = mapped_column(Text, unique=True)
    version: Mapped[str | None] = mapped_column(Text)
    # "missing", "ok" or "stale": whether there is data, and whether it may be used.
    status: Mapped[str] = mapped_column(Text, server_default="missing")
    # Why the last update failed or was not tried. The version above is then the one before it.
    detail: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    update_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RirInetnum(Base):
    """A registration record of a registry: who a range is registered to.

    The range holds the addresses as integers, ``[first, last + 1)``. IPv4 only (C-5).
    """

    __tablename__ = "rir_inetnum"
    __table_args__ = (Index("ix_rir_inetnum_ip_range", "ip_range", postgresql_using="gist"),)

    id: Mapped[int] = _identity()
    rir: Mapped[str] = mapped_column(Text)
    ip_range: Mapped[Range[int]] = mapped_column(INT8RANGE)
    network_name: Mapped[str | None] = mapped_column(Text)
    organization: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)


class RirDelegation(Base):
    """A line of a registry's delegation statistics. All five registries."""

    __tablename__ = "rir_delegation"
    __table_args__ = (Index("ix_rir_delegation_ip_range", "ip_range", postgresql_using="gist"),)

    id: Mapped[int] = _identity()
    rir: Mapped[str] = mapped_column(Text)
    ip_range: Mapped[Range[int]] = mapped_column(INT8RANGE)
    country: Mapped[str | None] = mapped_column(Text)
    delegated_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text)


class EnrichmentSnapshot(Base):
    """What the reference datasets said about one host of one scan (FR-ENR-5).

    Never updated, except for the ``latest`` flag: a re-run adds a row with the
    next run number and moves the flag (FR-ENR-7).
    """

    __tablename__ = "enrichment_snapshot"
    __table_args__ = (
        # A snapshot belongs to a scan or to a traceroute run, never to both.
        CheckConstraint("(scan_id IS NULL) <> (traceroute_run_id IS NULL)", name="one_owner"),
        # Redelivered findings and a repeated phase cannot add a second row for a run.
        UniqueConstraint("scan_id", "address", "run", name="uq_enrichment_snapshot_run"),
        # A responding hop is enriched once per traceroute run.
        UniqueConstraint(
            "traceroute_run_id", "address", name="uq_enrichment_snapshot_traceroute_run_id"
        ),
        # A direct join from an observation to its current snapshot.
        Index(
            "uq_enrichment_snapshot_latest",
            "scan_id",
            "address",
            unique=True,
            postgresql_where=text("latest"),
        ),
    )

    id: Mapped[int] = _identity()
    scan_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("scan.id"))
    traceroute_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("traceroute_run.id")
    )
    address: Mapped[IPv4Address] = mapped_column(Inet)
    run: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    latest: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    asn: Mapped[int | None] = mapped_column(BigInteger)
    as_holder: Mapped[str | None] = mapped_column(Text)
    network_name: Mapped[str | None] = mapped_column(Text)
    organization: Mapped[str | None] = mapped_column(Text)
    # "registry" or "as_holder": where the organization was taken from (FR-ENR-3).
    organization_source: Mapped[str | None] = mapped_column(Text)
    rir: Mapped[str | None] = mapped_column(Text)
    registration_country: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    # No spatial index: the map loads points by scan or project, not by area.
    location: Mapped[GeoPoint | None] = mapped_column(Geography)
    accuracy_radius_km: Mapped[int | None] = mapped_column(Integer)
    # Fields whose dataset was missing or failed. They are unknown, not empty.
    unavailable: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    dataset_versions: Mapped[dict[str, Any]] = _jsonb()
    created_at: Mapped[datetime] = _created_at()


# --- Traceroute and classification (Phase 3) ---------------------------------------


class TracerouteState(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


FINAL_TRACEROUTE_STATES = frozenset({TracerouteState.COMPLETED, TracerouteState.FAILED})


class TracerouteRun(Base):
    """One traceroute to one address of a project, with how it was made (FR-TRC-7)."""

    __tablename__ = "traceroute_run"
    __table_args__ = (
        Index("ix_traceroute_run_project_id_target", "project_id", "target"),
        Index("ix_traceroute_run_state", "state"),
    )

    id: Mapped[int] = _identity()
    project_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("project.id"))
    # The scope the target was checked against.
    attestation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("attestation.id"))
    vantage_point_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("vantage_point.id"))
    target: Mapped[IPv4Address] = mapped_column(Inet)
    method: Mapped[str] = mapped_column(Text)
    # port, max_hops, probes_per_hop, timeout_ms
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state: Mapped[TracerouteState] = mapped_column(
        _enum(TracerouteState, "traceroute_state"), server_default=TracerouteState.QUEUED.value
    )
    reached: Mapped[bool | None] = mapped_column(Boolean)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = _created_at()
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TracerouteProbe(Base):
    """One probe of one TTL of a run: who answered, or that nobody did (FR-TRC-3).
    Loss and the responders of a hop are derived when the run is read."""

    __tablename__ = "traceroute_probe"
    __table_args__ = (
        # "Was this address seen as an intermediate hop?" (FR-CLS-2)
        Index("ix_traceroute_probe_responder", "responder"),
    )

    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("traceroute_run.id"), primary_key=True
    )
    ttl: Mapped[int] = mapped_column(Integer, primary_key=True)
    probe: Mapped[int] = mapped_column(Integer, primary_key=True)
    responder: Mapped[IPv4Address | None] = mapped_column(Inet)
    rtt_ms: Mapped[float | None] = mapped_column(Float)
    reply_kind: Mapped[str | None] = mapped_column(Text)


class TracerouteResponder(Base):
    """An address that answered a run, with what is known about it as a hop.

    Its enrichment is a snapshot owned by the run. Private and CGNAT addresses
    are stored here and marked unlocatable; they are not enriched (FR-TRC-4).
    """

    __tablename__ = "traceroute_responder"

    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("traceroute_run.id"), primary_key=True
    )
    address: Mapped[IPv4Address] = mapped_column(Inet, primary_key=True)
    unlocatable: Mapped[bool] = mapped_column(Boolean)
    ptr_names: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    ptr_status: Mapped[str | None] = mapped_column(Text)


class ClassificationResult(Base):
    """The automatic device type of one host of one scan (FR-CLS-3).

    Never updated, except for the ``latest`` flag: a re-run adds a row with
    the next run number and moves the flag (FR-CLS-7). A newer ruleset changes
    no stored result (FR-CLS-4).
    """

    __tablename__ = "classification_result"
    __table_args__ = (
        UniqueConstraint("scan_id", "address", "run", name="uq_classification_result_run"),
        Index(
            "uq_classification_result_latest",
            "scan_id",
            "address",
            unique=True,
            postgresql_where=text("latest"),
        ),
    )

    id: Mapped[int] = _identity()
    scan_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("scan.id"))
    address: Mapped[IPv4Address] = mapped_column(Inet)
    run: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    latest: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    device_type: Mapped[str] = mapped_column(Text)
    sub_role: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    # The rules that fired, each with the evidence it matched.
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    ruleset_version: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


# --- Schedules, comparison and alerts (Phase 4) -------------------------------------


class ScanProfile(Base):
    """A named set of scan parameters, for manual scans and schedules (FR-CFG-8)."""

    __tablename__ = "scan_profile"

    id: Mapped[int] = _identity()
    name: Mapped[str] = mapped_column(Text, unique=True)
    # The parameters of a scan without its targets, as the scan API takes them.
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()


class Schedule(Base):
    """A recurring scan of a project (FR-SCH-1): targets, parameters, and an
    interval or a cron expression with a time zone."""

    __tablename__ = "schedule"
    __table_args__ = (
        CheckConstraint("(interval_seconds IS NULL) <> (cron IS NULL)", name="interval_or_cron"),
        Index("ix_schedule_next_run_at", "next_run_at", postgresql_where=text("enabled")),
    )

    id: Mapped[int] = _identity()
    project_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("project.id"), index=True)
    name: Mapped[str] = mapped_column(Text)
    # The target entries as the analyst wrote them, checked like a scan's.
    targets: Mapped[list[str]] = mapped_column(ARRAY(Text))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    interval_seconds: Mapped[int | None] = mapped_column(Integer)
    cron: Mapped[str | None] = mapped_column(Text)
    time_zone: Mapped[str] = mapped_column(Text, server_default="UTC")
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    # Why the scheduler stopped trusting the schedule, until the analyst saves it again.
    flag_reason: Mapped[str | None] = mapped_column(Text)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # "started", "skipped" or "rejected", and later how the started scan ended.
    last_outcome: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class ScheduleRun(Base):
    """One planned run of a schedule and what became of it (FR-SCH-3, FR-SCH-5)."""

    __tablename__ = "schedule_run"

    id: Mapped[int] = _identity()
    schedule_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("schedule.id", ondelete="CASCADE"), index=True
    )
    planned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # "started", "skipped" or "rejected".
    outcome: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    scan_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("scan.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()


class ScanDiff(Base):
    """A stored diff of two scans. Only automatic diffs are stored: an alert
    must keep pointing at what was detected at the time (architecture 11.3)."""

    __tablename__ = "scan_diff"

    id: Mapped[int] = _identity()
    base_scan_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("scan.id"), index=True)
    target_scan_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("scan.id"), index=True)
    # The ports that were probed in both scans; changes are reported for these only.
    compared_ports: Mapped[list[Range[int]]] = mapped_column(INT4MULTIRANGE)
    not_comparable: Mapped[list[IPv4Address]] = mapped_column(ARRAY(Inet))
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()


class ChangeEvent(Base):
    """One change between the two scans of a stored diff."""

    __tablename__ = "change_event"

    id: Mapped[int] = _identity()
    diff_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("scan_diff.id", ondelete="CASCADE"), index=True
    )
    address: Mapped[IPv4Address] = mapped_column(Inet)
    # host_appeared, host_disappeared, port_opened, port_closed, ptr_changed,
    # classification_changed, enrichment_changed
    kind: Mapped[str] = mapped_column(Text)
    port: Mapped[int | None] = mapped_column(Integer)
    field: Mapped[str | None] = mapped_column(Text)
    before: Mapped[Any] = mapped_column(JSONB, nullable=True)
    after: Mapped[Any] = mapped_column(JSONB, nullable=True)
    # The dataset behind the changed field has another version (FR-HIS-5).
    dataset_change: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class Alert(Base):
    """An in-app alert (FR-HIS-7, FR-HIS-8)."""

    __tablename__ = "alert"
    __table_args__ = (
        Index("ix_alert_unread", "created_at", postgresql_where=text("read_at IS NULL")),
    )

    id: Mapped[int] = _identity()
    # None for an alert about the installation, such as a dead-lettered message.
    project_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("project.id"), index=True)
    # changes_detected, scheduled_run_failed, scheduled_run_skipped,
    # scheduled_run_rejected, message_dead_lettered
    type: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    # What the alert points at, for example {"diff_id": 4} or {"scan_id": 9}.
    link: Mapped[dict[str, Any]] = _jsonb()
    created_at: Mapped[datetime] = _created_at()
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
