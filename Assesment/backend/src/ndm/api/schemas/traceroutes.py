"""Models of the traceroute endpoints (FR-TRC-1 to FR-TRC-7)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

UDP_FIRST_PORT = 33434


class TracerouteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str
    """One address of the project scope."""
    method: Literal["icmp", "tcp", "udp"] = "icmp"
    port: int | None = Field(default=None, ge=1, le=65535)
    """TCP: the destination port; by default an open port of the host. UDP:
    the first destination port; by default 33434. ICMP: not used."""
    max_hops: int = Field(default=30, ge=1, le=64)
    probes_per_hop: int = Field(default=3, ge=1, le=10)
    timeout_ms: int = Field(default=1000, ge=100, le=10000)


class TracerouteParameters(BaseModel):
    port: int | None
    max_hops: int
    probes_per_hop: int
    timeout_ms: int


class TracerouteOut(BaseModel):
    """A traceroute with how it was made (FR-TRC-7)."""

    id: int
    project_id: int
    target: str
    method: str
    parameters: TracerouteParameters
    state: str
    """``queued``, ``running``, ``completed`` or ``failed``."""
    reached: bool | None
    failure_reason: str | None
    vantage_point: str | None
    created_at: datetime
    dispatched_at: datetime | None
    finished_at: datetime | None


class ProbeOut(BaseModel):
    probe: int
    address: str | None
    """None: nobody answered this probe."""
    rtt_ms: float | None
    kind: str | None


class HopResponderOut(BaseModel):
    """An address that answered a hop, with what is known about it (FR-TRC-4)."""

    address: str
    rtt_ms: list[float | None]
    """One entry per probe of the hop; None where this address did not answer it."""
    unlocatable: bool
    """Private or CGNAT space: not enriched and not on the map."""
    ptr_names: list[str]
    ptr_status: str | None
    asn: int | None = None
    as_holder: str | None = None
    organization: str | None = None
    country: str | None = None
    city: str | None = None
    # Approximate, the centre of an area with this radius (FR-ENR-8).
    latitude: float | None = None
    longitude: float | None = None
    accuracy_radius_km: int | None = None


class HopOut(BaseModel):
    ttl: int
    responders: list[HopResponderOut]
    """Several on a load-balanced path (FR-TRC-3)."""
    probes: list[ProbeOut]
    lost: int
    """Probes of the hop that nobody answered."""
    silent: bool
    """No probe of the hop was answered."""


class TracerouteDetailOut(TracerouteOut):
    hops: list[HopOut]
