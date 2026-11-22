"""Models of the scan endpoints, and the limits of the tunable parameters (FR-CFG-6)."""

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy.dialects.postgresql import Range

from ndm.core.ports import (
    FIRST_PORT,
    LAST_PORT,
    PortProfile,
    parse_port_list,
    profile_ports,
)

# One scan probes at most this many addresses (FR-CFG-1).
MAX_TARGETS = 256

# Limits of the parameters the analyst may tune: lowest, highest, default.
TIMEOUT_MS = (100, 10_000, 1_500)
RETRIES = (0, 3, 1)
HOSTS_IN_PARALLEL = (1, 256, 64)
# Default rate of one scan. The highest rate is the operator's global ceiling.
DEFAULT_PPS = 1_000
DEFAULT_DISCOVERY_PORTS = [80, 443]

Port = Annotated[int, Field(ge=FIRST_PORT, le=LAST_PORT)]
ProfileName = Literal["top-100", "top-1000", "full", "custom"]
TechniqueName = Literal["automatic", "syn", "connect"]


def to_ranges(ports: Sequence[int]) -> list[Range[int]]:
    """Ports as ranges of consecutive numbers, the form the scan row stores."""
    ranges: list[Range[int]] = []
    for port in sorted(set(ports)):
        if ranges and ranges[-1].upper == port:
            ranges[-1] = Range(ranges[-1].lower, port + 1)
        else:
            ranges.append(Range(port, port + 1))
    return ranges


class DiscoveryIn(BaseModel):
    icmp_echo: bool = True
    tcp_ports: list[Port] = Field(default=DEFAULT_DISCOVERY_PORTS, max_length=16)

    @model_validator(mode="after")
    def _at_least_one_method(self) -> DiscoveryIn:
        if not self.icmp_echo and not self.tcp_ports:
            raise ValueError("enable ICMP echo or give at least one TCP port, or skip discovery")
        return self


class PortSelection(BaseModel):
    """What to probe: a built-in profile or a custom list (FR-CFG-4)."""

    port_profile: ProfileName = "top-1000"
    custom_ports: Annotated[str, StringConstraints(max_length=2000)] | None = None

    @model_validator(mode="after")
    def _custom_list_goes_with_the_custom_profile(self) -> PortSelection:
        if (self.port_profile == "custom") != (self.custom_ports is not None):
            raise ValueError("custom_ports is required for the custom profile and only for it")
        if self.custom_ports is not None:
            parse_port_list(self.custom_ports)
        return self

    def ports(self) -> list[int]:
        if self.custom_ports is not None:
            return parse_port_list(self.custom_ports)
        return list(profile_ports(PortProfile(self.port_profile)))


class ScanSettings(PortSelection):
    """The parameters of a scan without its targets: what a scan profile and a
    schedule store (FR-CFG-8)."""

    discovery: DiscoveryIn | None = DiscoveryIn()
    """Null skips discovery: every target is port-scanned."""
    scan_all_targets: bool = False
    """Port-scan every target, also those that did not answer discovery (FR-CFG-3)."""
    technique: TechniqueName = "automatic"
    # The upper limit of pps is the operator ceiling; the endpoint checks it.
    pps: int = Field(default=DEFAULT_PPS, ge=1)
    timeout_ms: int = Field(default=TIMEOUT_MS[2], ge=TIMEOUT_MS[0], le=TIMEOUT_MS[1])
    retries: int = Field(default=RETRIES[2], ge=RETRIES[0], le=RETRIES[1])
    hosts_in_parallel: int = Field(
        default=HOSTS_IN_PARALLEL[2], ge=HOSTS_IN_PARALLEL[0], le=HOSTS_IN_PARALLEL[1]
    )


Targets = Annotated[
    list[Annotated[str, StringConstraints(max_length=64)]],
    Field(min_length=1, max_length=MAX_TARGETS),
]
"""Addresses, CIDR blocks or start-end ranges; at most 256 addresses after expansion."""


class ScanCreate(ScanSettings):
    targets: Targets


class EstimateRequest(PortSelection):
    targets: list[Annotated[str, StringConstraints(max_length=64)]] = Field(
        min_length=1, max_length=MAX_TARGETS
    )
    pps: int = Field(default=DEFAULT_PPS, ge=1)
    retries: int = Field(default=RETRIES[2], ge=RETRIES[0], le=RETRIES[1])


class EstimateOut(BaseModel):
    """Probes and duration of the port scan (FR-CFG-9). The ``max_`` values hold
    if nothing answers and every probe is retried."""

    hosts: int
    ports: int
    probes: int
    max_probes: int
    seconds: float
    max_seconds: float


class ScanOut(BaseModel):
    id: int
    project_id: int
    state: str
    phase: str | None
    partial: bool
    failure_reason: str | None
    failed_phase: str | None
    """The phase after probing that failed. Such a scan can be retried from there."""
    technique_requested: str
    technique_used: str | None
    """The technique actually used; null until the scan starts (FR-CFG-5)."""
    icmp_discovery_unavailable: bool
    """ICMP discovery was asked for, but the scanner could not send echo
    requests: hosts that answer only ping were not found."""
    targets: list[str]
    port_profile: str
    port_count: int
    parameters: dict[str, Any]
    attestation_id: int
    vantage_point: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested: bool
    retry_requested: bool
    enrichment_requested: bool
    classification_requested: bool
    dataset_versions: dict[str, str]
    """The versions of the reference datasets when the scan started (FR-SCN-8)."""
    # Progress.
    chunks_done: int
    chunks_total: int
    probes_sent: int
    receive_drops: int
    # Summary.
    hosts_reachable: int
    open_ports: int
