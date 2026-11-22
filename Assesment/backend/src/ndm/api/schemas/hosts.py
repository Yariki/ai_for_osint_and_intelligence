"""Models of the host endpoints."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HostRowOut(BaseModel):
    """One row of the host table: a target with what was found and what is known about it."""

    address: str
    scan_id: int
    """The scan the row comes from. In the project-wide view it differs from row to row."""
    reachable: bool
    open_port_count: int
    closed_count: int
    filtered_count: int
    ptr_names: list[str]
    asn: int | None
    as_holder: str | None
    organization: str | None
    country: str | None
    city: str | None
    # The location is approximate: it is the centre of an area with this radius (FR-ENR-8).
    latitude: float | None
    longitude: float | None
    accuracy_radius_km: int | None
    # Classification (FR-CLS-3, FR-CLS-5): the automatic result, the analyst's
    # override, and the type in effect, which is the override if there is one.
    device_type: str | None
    sub_role: str | None
    confidence: float | None
    type_override: str | None
    effective_type: str | None
    tags: list[str]


class MapPointOut(BaseModel):
    """A host with a known location, as the map needs it."""

    address: str
    scan_id: int
    open_port_count: int
    latitude: float
    longitude: float
    accuracy_radius_km: int | None
    effective_type: str | None = None


class UnlocatedHostOut(BaseModel):
    """A host that has no known location. It is listed beside the map (FR-MAP-3)."""

    address: str
    scan_id: int
    open_port_count: int
    effective_type: str | None = None


class MapPointsOut(BaseModel):
    """The hosts of the table, for the map. Together the two lists hold exactly
    the hosts the table shows for the same filters."""

    located: list[MapPointOut]
    unlocated: list[UnlocatedHostOut]


class EvidenceOut(BaseModel):
    """A reply that showed the host to be reachable (FR-SCN-1)."""

    probe: str
    """``icmp`` or ``tcp:<port>``."""
    at: datetime
    rtt_ms: float


class OpenPortOut(BaseModel):
    port: int
    nominal_service: str | None
    """The IANA-registered name of the port number. It is the nominal
    assignment, not a detected service: nothing was read from the port (FR-SCN-3)."""


class EnrichmentOut(BaseModel):
    """What the reference datasets said about the host when the snapshot was made."""

    run: int
    """1 for the snapshot made with the scan; higher for a re-run."""
    created_at: datetime
    asn: int | None
    as_holder: str | None
    network_name: str | None
    organization: str | None
    organization_source: str | None
    """``registry`` or ``as_holder``: where the organization was taken from (FR-ENR-3)."""
    rir: str | None
    registration_country: str | None
    country: str | None
    city: str | None
    latitude: float | None
    longitude: float | None
    accuracy_radius_km: int | None
    unavailable: list[str]
    """Fields that could not be filled because their dataset was missing or
    stale. Such a field is unknown, which is not the same as empty (FR-ENR-7)."""
    dataset_versions: dict[str, str]


class ReverseDnsOut(BaseModel):
    names: list[str]
    status: str | None
    """``ok``, ``no_name``, ``failed``, ``disabled``, or null while not looked up."""


class TimelineEntryOut(BaseModel):
    """One scan of the project that targeted the address."""

    scan_id: int
    state: str
    started_at: datetime | None
    finished_at: datetime | None
    reachable: bool
    open_ports: list[int]
    # Against the scan before in which the host answered, for ports probed in both.
    opened: list[int]
    closed: list[int]


class ClassificationOut(BaseModel):
    """The automatic classification of the host in this scan (FR-CLS-3)."""

    run: int
    created_at: datetime
    device_type: str
    """``unknown`` below the confidence threshold."""
    sub_role: str | None
    confidence: float
    rules: list[dict[str, Any]]
    """Every rule that fired, with the evidence it matched."""
    ruleset_version: str


class OverrideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_type: str = Field(pattern="^[a-z_]{1,32}$")


class AnnotationsIn(BaseModel):
    """Tags and notes of a host in a project. They never change scan data (FR-CLS-6)."""

    model_config = ConfigDict(extra="forbid")

    tags: list[str] = Field(default_factory=list, max_length=20)
    notes: str | None = Field(default=None, max_length=10_000)


class AnnotationsOut(BaseModel):
    address: str
    type_override: str | None
    override_at: datetime | None
    tags: list[str]
    notes: str | None


class HostDetailOut(BaseModel):
    scan_id: int
    address: str
    reachable: bool
    evidence: list[EvidenceOut]
    open_ports: list[OpenPortOut]
    closed_count: int
    filtered_count: int
    enrichment: EnrichmentOut | None
    """Null while the host has no snapshot: it did not answer, or the scan is still running."""
    reverse_dns: ReverseDnsOut
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    timeline: list[TimelineEntryOut]
    classification: ClassificationOut | None
    """Null while the host has not been classified."""
    type_override: str | None
    """The analyst's own device type for the address, in every scan of the project."""
    effective_type: str | None
    tags: list[str]
    notes: str | None
