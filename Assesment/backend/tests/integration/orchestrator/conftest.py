"""Helpers for orchestrator tests: scans in a given state, and snapshots of what is stored."""

import os
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from ipaddress import IPv4Address
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Range, insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ndm.core.messages import Cancel, Chunk, Heartbeat, Hello, TracerouteJob
from ndm.core.settings import Settings
from ndm.db.models import (
    Account,
    Attestation,
    ChunkState,
    Host,
    HostObservation,
    PortObservation,
    Project,
    ProjectHost,
    Scan,
    ScanChunk,
    ScanPhase,
    ScanState,
    Schedule,
    ScopeEntry,
    ScopeEntryKind,
    VantagePoint,
)
from ndm.orchestrator import vantage
from ndm.orchestrator.events import Event
from ndm.orchestrator.recording import record_result
from ndm.orchestrator.tick import Orchestrator, tick
from tests.tools.stub_scanner import World, respond

TARGETS = [IPv4Address(f"8.8.8.{last}") for last in (1, 2, 3, 4)]

ScanFactory = Callable[..., Awaitable[Scan]]


async def _one[T](db: AsyncSession, model: type[T], **values: Any) -> T:
    """The single row of a model, created on first use."""
    row = await db.scalar(select(model).limit(1))
    if row is None:
        row = model(**values)
        db.add(row)
        await db.flush()
    return row


@pytest.fixture
def make_scan(db_session: AsyncSession) -> ScanFactory:
    """Create a scan as the orchestrator leaves it after promotion: one
    observation row per target, all unreachable, and its chunk rows dispatched.

    Each call makes a project of its own, with the scope 8.8.8.0/24.
    """

    async def create(
        *,
        state: ScanState = ScanState.RUNNING,
        phase: ScanPhase | None = ScanPhase.PORT_SCAN,
        targets: list[IPv4Address] = TARGETS,
        chunks: int = 3,
        scope: tuple[str, str] = ("8.8.8.0", "8.8.8.255"),
        **scan_values: Any,
    ) -> Scan:
        db = db_session
        account = await _one(db, Account, username="analyst", password_hash="hash")
        vantage = await _one(
            db, VantagePoint, name="test", capabilities={"syn": True, "icmp": True}
        )
        project = Project(name="Edge routers")
        db.add(project)
        await db.flush()
        attestation = Attestation(project_id=project.id, account_id=account.id)
        db.add(attestation)
        await db.flush()
        db.add(
            ScopeEntry(
                attestation_id=attestation.id,
                kind=ScopeEntryKind.RANGE,
                original_text=f"{scope[0]}-{scope[1]}",
                start_address=IPv4Address(scope[0]),
                end_address=IPv4Address(scope[1]),
            )
        )
        project.current_attestation_id = attestation.id
        values: dict[str, Any] = {
            "project_id": project.id,
            "attestation_id": attestation.id,
            "vantage_point_id": None if state is ScanState.QUEUED else vantage.id,
            "state": state,
            "phase": phase,
            "targets": targets,
            "probed_ports": [Range(22, 23), Range(80, 81), Range(443, 444), Range(8080, 8081)],
            "parameters": {
                "discovery": {"icmp_echo": True, "tcp_ports": [80, 443]},
                "scan_all_targets": False,
                "pps": 1000,
                "timeout_ms": 1500,
                "retries": 1,
                "hosts_in_parallel": 64,
            },
            "technique_requested": "automatic",
            "technique_used": None if state is ScanState.QUEUED else "syn",
            "seed": 91823,
        }
        scan = Scan(**{**values, **scan_values})
        db.add(scan)
        await db.flush()
        if state is not ScanState.QUEUED:
            await db.execute(
                insert(HostObservation),
                [{"scan_id": scan.id, "address": address} for address in targets],
            )
            for seq in range(chunks):
                db.add(
                    ScanChunk(
                        scan_id=scan.id,
                        chunk_seq=seq,
                        phase=ScanPhase.DISCOVERY if seq == 0 else ScanPhase.PORT_SCAN,
                        spec={},
                        state=ChunkState.DISPATCHED,
                    )
                )
        await db.commit()
        return scan

    return create


async def schedule_for(db: AsyncSession, scan: Scan, name: str = "Nightly edge sweep") -> int:
    """Make a scan a run of a new daily schedule of its project."""
    schedule = Schedule(
        project_id=scan.project_id,
        name=name,
        targets=["8.8.8.1-8.8.8.4"],
        parameters={},
        interval_seconds=86400,
        next_run_at=datetime.now(UTC),
    )
    db.add(schedule)
    await db.flush()
    scan.schedule_id = schedule.id
    await db.merge(scan)
    await db.commit()
    return schedule.id


async def snapshot(db: AsyncSession, scan: Scan) -> dict[str, Any]:
    """Everything stored for a scan, without identifiers, so two scans can be compared."""
    await db.commit()
    # Read what the database holds now, not what the session remembers.
    fresh = {"populate_existing": True}
    stored = await db.get_one(Scan, scan.id, populate_existing=True)
    observations = await db.scalars(
        select(HostObservation)
        .where(HostObservation.scan_id == scan.id)
        .order_by(HostObservation.address)
        .execution_options(**fresh)
    )
    ports = await db.execute(
        select(PortObservation.address, PortObservation.port)
        .where(PortObservation.scan_id == scan.id)
        .order_by(PortObservation.address, PortObservation.port)
    )
    chunks = await db.scalars(
        select(ScanChunk)
        .where(ScanChunk.scan_id == scan.id)
        .order_by(ScanChunk.chunk_seq)
        .execution_options(**fresh)
    )
    hosts = await db.scalars(select(Host).order_by(Host.address).execution_options(**fresh))
    project_hosts = await db.scalars(
        select(ProjectHost)
        .where(ProjectHost.project_id == scan.project_id)
        .order_by(ProjectHost.address)
        .execution_options(**fresh)
    )
    return {
        "scan": (
            stored.state,
            stored.phase,
            stored.partial,
            stored.failure_reason,
            stored.counters,
        ),
        "observations": {
            str(o.address): (o.reachable, o.evidence, o.closed_count, o.filtered_count)
            for o in observations
        },
        "ports": [(str(address), port) for address, port in ports],
        "chunks": {c.chunk_seq: (c.state, c.result) for c in chunks},
        "hosts": {str(h.address): (h.first_seen_at, h.last_seen_at, h.is_public) for h in hosts},
        "project_hosts": {
            str(p.address): (p.first_seen_at, p.last_seen_at, p.latest_scan_id == scan.id)
            for p in project_hosts
        },
    }


# --- The orchestrator with its ways out replaced by lists ---------------------------


@dataclass
class Outbox:
    """Stands in for the broker: keeps what the orchestrator would have published."""

    chunks: list[Chunk] = field(default_factory=list)
    traceroutes: list[TracerouteJob] = field(default_factory=list)
    cancels: list[Cancel] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    crash_after_publish: bool = False
    """Raise right after the next chunk is published, as if the process died
    between the publish and the commit."""

    async def publish_chunk(self, chunk: Chunk) -> None:
        self.chunks.append(chunk)
        if self.crash_after_publish:
            self.crash_after_publish = False
            raise RuntimeError("process died between publish and commit")

    async def publish_cancel(self, cancel: Cancel) -> None:
        self.cancels.append(cancel)

    async def publish_traceroute(self, job: TracerouteJob) -> None:
        self.traceroutes.append(job)

    async def publish_events(self, events: Sequence[Event]) -> None:
        self.events += events


@pytest.fixture
def outbox() -> Outbox:
    return Outbox()


@pytest.fixture
def orchestrator(
    monkeypatch: pytest.MonkeyPatch, app_engine: AsyncEngine, outbox: Outbox
) -> Orchestrator:
    for name in list(os.environ):
        if name.startswith("NDM_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("NDM_VANTAGE_POINT", "test")
    return Orchestrator(
        sessions=async_sessionmaker(app_engine, expire_on_commit=False),
        settings=Settings(),
        never_scan=[],
        publish_chunk=outbox.publish_chunk,
        publish_cancel=outbox.publish_cancel,
        publish_events=outbox.publish_events,
        publish_traceroute=outbox.publish_traceroute,
    )


async def scanner_reports(
    orchestrator: Orchestrator,
    *,
    hello: bool = True,
    syn: bool = True,
    icmp: bool = True,
    allow_non_public: bool = False,
) -> None:
    """A hello (or heartbeat) of the scanner arrives: it is alive from now on."""
    kind = Hello if hello else Heartbeat
    status = kind(
        vantage_point="test",
        egress_address=IPv4Address("192.0.2.1"),
        syn_capable=syn,
        icmp_capable=icmp,
        allow_non_public_targets=allow_non_public,
    )
    async with orchestrator.sessions() as db:
        await vantage.record_status(db, status, orchestrator.settings)
        await db.commit()


async def answer(orchestrator: Orchestrator, chunk: Chunk, world: World) -> None:
    """The scanner's results for a chunk arrive, as the stub scanner would send them."""
    async with orchestrator.sessions() as db:
        for result in respond(chunk, world):
            await record_result(db, result, orchestrator.enricher)
            await db.commit()


async def run_to_end(
    orchestrator: Orchestrator, outbox: Outbox, world: World, *, ticks: int = 50
) -> None:
    """Tick, and answer every chunk as it is published, until nothing more comes."""
    answered = 0
    for _ in range(ticks):
        await tick(orchestrator)
        if len(outbox.chunks) == answered:
            return
        for chunk in outbox.chunks[answered:]:
            await answer(orchestrator, chunk, world)
        answered = len(outbox.chunks)
    raise AssertionError("the scan did not come to an end")


# --- Enrichment -----------------------------------------------------------------------


@pytest.fixture
def enrichment_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Settings:
    """Settings of an installation with every reference dataset configured, and
    reverse DNS off, so that no test sends a DNS query by accident."""
    from tests.tools import reference_data

    directory = tmp_path_factory.mktemp("reference")
    (directory / "registry-files").mkdir()
    for name in list(os.environ):
        if name.startswith("NDM_"):
            monkeypatch.delenv(name)
    for name, value in reference_data.ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("NDM_VANTAGE_POINT", "test")
    monkeypatch.setenv("NDM_REFERENCE_DATA_PATH", str(directory / "data"))
    monkeypatch.setenv("NDM_RIR_FILES_PATH", str(directory / "registry-files"))
    monkeypatch.setenv("NDM_PTR_ENABLED", "false")
    return Settings()


@pytest.fixture
def enriching_orchestrator(
    enrichment_settings: Settings,
    app_engine: AsyncEngine,
    outbox: Outbox,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Orchestrator]:
    """An orchestrator as ``main`` builds it: with an enricher, and with
    enrichment registered as the phase after probing."""
    from ndm.orchestrator import phases
    from ndm.orchestrator.datasets.geolite import GeoLite
    from ndm.orchestrator.enrichment import Enricher

    readers = GeoLite(enrichment_settings.reference_data_path)
    readers.reload()
    enricher = Enricher(enrichment_settings, readers)
    monkeypatch.setattr(phases, "PHASES", {ScanPhase.ENRICHMENT: enricher.run_phase})
    yield Orchestrator(
        sessions=async_sessionmaker(app_engine, expire_on_commit=False),
        settings=enrichment_settings,
        never_scan=[],
        publish_chunk=outbox.publish_chunk,
        publish_cancel=outbox.publish_cancel,
        publish_events=outbox.publish_events,
        enricher=enricher,
        publish_traceroute=outbox.publish_traceroute,
    )
    readers.close()


@pytest.fixture
def classifying_orchestrator(
    enriching_orchestrator: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> Orchestrator:
    """The orchestrator with both phases after probing, as ``main`` builds it:
    enrichment, then classification with the shipped ruleset."""
    from ndm.orchestrator import phases
    from ndm.orchestrator.classification import load_ruleset
    from ndm.orchestrator.classification_phase import Classifier

    classifier = Classifier(
        load_ruleset(), enriching_orchestrator.settings.classification_threshold
    )
    monkeypatch.setitem(phases.PHASES, ScanPhase.CLASSIFICATION, classifier.run_phase)
    return enriching_orchestrator
