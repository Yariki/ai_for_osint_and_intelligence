"""The messages that travel through the broker (architecture section 9).

Every message has a schema version and a message identifier; every message
about a scan has the scan identifier, which the receiver binds into its log
lines. A body that does not validate, or that has a schema version this code
does not know, raises ``pydantic.ValidationError``: the consumer then rejects
the message without requeue and the broker moves it to the dead-letter queue.

Messages are grouped by the queue they travel on. Each group has a
``TypeAdapter`` that picks the right model by the ``type`` field.
"""

import enum
from datetime import datetime
from ipaddress import IPv4Address
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

Port = Annotated[int, Field(ge=1, le=65535)]


class ChunkPhase(enum.StrEnum):
    DISCOVERY = "discovery"
    PORT_SCAN = "port_scan"


class Technique(enum.StrEnum):
    SYN = "syn"
    CONNECT = "connect"


class TracerouteMethod(enum.StrEnum):
    ICMP = "icmp"
    TCP = "tcp"
    UDP = "udp"


class Message(BaseModel):
    # An unknown field is an error: it means the sender speaks another schema.
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    message_id: UUID = Field(default_factory=uuid4)


class ScanMessage(Message):
    scan_id: int


# --- Work: orchestrator -> scanner --------------------------------------------


class Discovery(BaseModel):
    """The discovery methods of a scan (FR-CFG-2)."""

    model_config = ConfigDict(extra="forbid")

    icmp_echo: bool
    tcp_ports: list[Port]


class Chunk(ScanMessage):
    """One unit of work, of checkpointing and of recovery (architecture section 9.3).

    It carries everything the scanner needs, the scope included: the scanner
    has no database (AD3) and checks the scope itself before the first probe.
    """

    type: Literal["chunk"] = "chunk"
    chunk_seq: int
    phase: ChunkPhase
    technique: Technique
    targets: list[IPv4Address]
    ports: list[Port]
    """Empty for a discovery chunk."""
    discovery: Discovery | None = None
    """Set for a discovery chunk only."""
    pps: int = Field(gt=0)
    timeout_ms: int = Field(gt=0)
    retries: int = Field(ge=0)
    seed: int
    attestation_id: int
    scope: list[tuple[IPv4Address, IPv4Address]]
    """The project scope as start and end addresses, both included."""


class Cancel(ScanMessage):
    """Broadcast to every scanner: stop sending for this scan."""

    type: Literal["cancel"] = "cancel"


# --- Traceroute: orchestrator -> scanner, on a queue of its own (architecture 7) ---


class TracerouteJob(Message):
    """One traceroute. Like a chunk, it carries the scope the scanner checks
    before the first probe. Only the target is ever a destination (I2)."""

    type: Literal["traceroute_job"] = "traceroute_job"
    run_id: int
    target: IPv4Address
    method: TracerouteMethod
    port: Port | None
    """TCP: the destination port. UDP: the first destination port. ICMP: none."""
    max_hops: int = Field(ge=1, le=64)
    probes_per_hop: int = Field(ge=1, le=10)
    timeout_ms: int = Field(gt=0)
    pps: int = Field(gt=0)
    attestation_id: int
    scope: list[tuple[IPv4Address, IPv4Address]]


# --- Results: scanner -> orchestrator -----------------------------------------


class ScannerStatus(Message):
    """What the scanner can do and how it is doing. It has no listening port, so
    this is how its capabilities and counters reach the orchestrator."""

    vantage_point: str
    egress_address: IPv4Address | None
    syn_capable: bool
    icmp_capable: bool
    allow_non_public_targets: bool
    """The scanner's own value of the test switch (NFR-SEC-6)."""
    probes_sent: int = 0
    replies_received: int = 0
    receive_drops: int = 0
    current_rate_pps: float = 0.0


class Hello(ScannerStatus):
    """Sent once, when the scanner starts."""

    type: Literal["hello"] = "hello"


class Heartbeat(ScannerStatus):
    """Sent every 10 seconds."""

    type: Literal["heartbeat"] = "heartbeat"


class Evidence(BaseModel):
    """One reply that shows a host is reachable (FR-SCN-1)."""

    model_config = ConfigDict(extra="forbid")

    at: datetime
    """When the scanner received the reply."""
    rtt_ms: float = Field(ge=0)


class HostFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: IPv4Address
    evidence: dict[str, Evidence] = {}
    """Keyed by probe: ``icmp`` or ``tcp:<port>``."""
    open_ports: list[Port] = []


class Findings(ScanMessage):
    """What the scanner found since its last flush. Sent every 500 ms while a chunk runs."""

    type: Literal["findings"] = "findings"
    chunk_seq: int
    hosts: list[HostFinding]


class HostCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: IPv4Address
    closed: int = Field(ge=0)
    filtered: int = Field(ge=0)


class ChunkDone(ScanMessage):
    """The final result of a chunk. The scanner acknowledges the chunk only after
    it has published this."""

    type: Literal["chunk_done"] = "chunk_done"
    chunk_seq: int
    probes_sent: int = Field(ge=0)
    receive_drops: int = Field(ge=0)
    counts: list[HostCounts]
    """Closed and filtered ports per host. Empty for a discovery chunk."""


class ChunkRejected(ScanMessage):
    """The scanner's own scope check refused the chunk. Nothing was sent."""

    type: Literal["chunk_rejected"] = "chunk_rejected"
    chunk_seq: int
    reason: str


class HopReply(BaseModel):
    """One probe of one TTL: who answered, how fast, and with what. All three
    are None for a probe that was not answered (FR-TRC-3)."""

    model_config = ConfigDict(extra="forbid")

    probe: int = Field(ge=0)
    address: IPv4Address | None = None
    rtt_ms: float | None = Field(default=None, ge=0)
    kind: str | None = None
    """``time_exceeded``, ``unreachable``, ``echo_reply``, ``tcp_reset`` or ``tcp_syn_ack``."""


class TracerouteHop(Message):
    """Every probe of one TTL, published as soon as each is answered or timed out."""

    type: Literal["traceroute_hop"] = "traceroute_hop"
    run_id: int
    ttl: int = Field(ge=1, le=64)
    replies: list[HopReply]


class TracerouteDone(Message):
    """The final message of a traceroute. The job is acknowledged after it."""

    type: Literal["traceroute_done"] = "traceroute_done"
    run_id: int
    reached: bool
    """Whether the target itself answered."""
    probes_sent: int = Field(ge=0)


class TracerouteRejected(Message):
    """The scanner's own scope check refused the job. Nothing was sent."""

    type: Literal["traceroute_rejected"] = "traceroute_rejected"
    run_id: int
    reason: str


# --- Commands: api -> orchestrator --------------------------------------------
# Identifiers only. The API commits first and publishes afterwards; the
# orchestrator reads the state from the database (AD1).


class ScanCreated(ScanMessage):
    type: Literal["scan_created"] = "scan_created"


class CancelRequested(ScanMessage):
    type: Literal["cancel_requested"] = "cancel_requested"


class PhaseRetryRequested(ScanMessage):
    """Run the failed post-probe phase of a failed scan again."""

    type: Literal["phase_retry_requested"] = "phase_retry_requested"


class EnrichmentRerunRequested(ScanMessage):
    """Enrich a finished scan again; the result is an additional snapshot (FR-ENR-7)."""

    type: Literal["enrichment_rerun_requested"] = "enrichment_rerun_requested"


class DatasetUpdateRequested(Message):
    """Update one reference dataset now, or every dataset when none is named."""

    type: Literal["dataset_update_requested"] = "dataset_update_requested"
    dataset: str | None = None


class TracerouteRequested(Message):
    """A traceroute run was created and waits to be dispatched."""

    type: Literal["traceroute_requested"] = "traceroute_requested"
    run_id: int


class ClassificationRerunRequested(ScanMessage):
    """Classify a finished scan again; the result is stored beside the original (FR-CLS-7)."""

    type: Literal["classification_rerun_requested"] = "classification_rerun_requested"


# --- UI events: orchestrator -> api -> browser ---------------------------------
# Transient. Losing one delays an update; it does not lose data.


class ScanProgress(ScanMessage):
    type: Literal["scan_progress"] = "scan_progress"
    project_id: int
    state: str
    phase: str | None
    chunks_done: int
    chunks_total: int
    probes_sent: int


class HostRow(BaseModel):
    """A slim host row for the live table."""

    model_config = ConfigDict(extra="forbid")

    address: IPv4Address
    reachable: bool
    open_port_count: int
    # Where the map draws the host. None while it has no known location.
    latitude: float | None = None
    longitude: float | None = None


class HostsChanged(ScanMessage):
    type: Literal["hosts_changed"] = "hosts_changed"
    project_id: int
    hosts: list[HostRow]


class HopRow(BaseModel):
    """A responder of a hop, as the live path needs it."""

    model_config = ConfigDict(extra="forbid")

    address: IPv4Address
    rtt_ms: list[float | None]
    """One entry per probe of the TTL; None where this address did not answer it."""
    unlocatable: bool
    """Private or CGNAT space: stored, not enriched, never on the map (FR-TRC-4)."""
    latitude: float | None = None
    longitude: float | None = None


class TracerouteHopEvent(Message):
    """A hop of a running traceroute, or the end of the run (FR-TRC-6)."""

    type: Literal["traceroute_hop"] = "traceroute_hop"
    project_id: int
    run_id: int
    target: IPv4Address
    state: str
    ttl: int | None
    """None when the event only says that the run has ended."""
    responders: list[HopRow]
    lost: int
    """Probes of this TTL that nobody answered."""


class AlertCreated(Message):
    """A new alert (FR-HIS-7). The unread indicator counts it without a reload."""

    type: Literal["alert_created"] = "alert_created"
    # None for an alert about the installation, such as a dead-lettered message.
    project_id: int | None
    alert_id: int
    alert_type: str
    summary: str


# --- One adapter per queue -----------------------------------------------------

WorkMessage = Annotated[Chunk, Field(discriminator="type")]
ControlMessage = Annotated[Cancel, Field(discriminator="type")]
Result = (
    Hello
    | Heartbeat
    | Findings
    | ChunkDone
    | ChunkRejected
    | TracerouteHop
    | TracerouteDone
    | TracerouteRejected
)
ResultMessage = Annotated[Result, Field(discriminator="type")]
Command = (
    ScanCreated
    | CancelRequested
    | PhaseRetryRequested
    | EnrichmentRerunRequested
    | DatasetUpdateRequested
    | TracerouteRequested
    | ClassificationRerunRequested
)
CommandMessage = Annotated[Command, Field(discriminator="type")]
Event = ScanProgress | HostsChanged | TracerouteHopEvent | AlertCreated
EventMessage = Annotated[Event, Field(discriminator="type")]

WORK: TypeAdapter[Chunk] = TypeAdapter(Chunk)
TRACEROUTE_WORK: TypeAdapter[TracerouteJob] = TypeAdapter(TracerouteJob)
CONTROL: TypeAdapter[Cancel] = TypeAdapter(Cancel)
RESULTS: TypeAdapter[Result] = TypeAdapter(ResultMessage)
COMMANDS: TypeAdapter[Command] = TypeAdapter(CommandMessage)
EVENTS: TypeAdapter[Event] = TypeAdapter(EventMessage)
