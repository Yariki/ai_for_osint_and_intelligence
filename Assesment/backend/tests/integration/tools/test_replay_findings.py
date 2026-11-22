"""The findings-replay harness: a scan of public addresses that probes nothing."""

# The fixtures of the orchestrator tests are imported by name and then used as arguments.
# ruff: noqa: F811

import ast
import socket
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Any

import pytest
from aio_pika.abc import AbstractChannel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core import broker, messages, scope
from ndm.core.messages import Chunk, ChunkPhase, Discovery, Technique
from ndm.db.models import HostObservation, PortObservation, Scan, ScanState
from ndm.orchestrator.tick import Orchestrator
from tests.integration.orchestrator.conftest import (  # noqa: F401
    Outbox,
    ScanFactory,
    make_scan,
    orchestrator,
    outbox,
    run_to_end,
    scanner_reports,
)
from tests.tools import replay_findings, stub_scanner
from tests.tools.replay_findings import ScannerPresent, load

RECORDED = load()


# --- The recorded scan ---


def test_recorded_scan_is_about_public_addresses_only() -> None:
    assert len(RECORDED.targets) == 80
    for entry in RECORDED.scope:
        # The product's own rule: these ranges are accepted without the test switch.
        scope.check_entry(scope.parse_entry(entry), never_scan=[], allow_non_public=False)
    networks = [IPv4Network(entry) for entry in RECORDED.scope]
    for address in RECORDED.world:
        assert any(IPv4Address(address) in network for network in networks), address
    assert set(RECORDED.reference) == set(RECORDED.scope)


def test_recorded_scan_has_colocated_and_unlocated_hosts() -> None:
    located = [n for n, data in RECORDED.reference.items() if "latitude" in data]
    unlocated = [n for n, data in RECORDED.reference.items() if "latitude" not in data]

    assert located and unlocated
    # Several hosts in one network share its coordinates, as city-level data does (FR-MAP-2).
    assert sum(IPv4Address(a) in IPv4Network("8.8.8.0/28") for a in RECORDED.world) >= 4


# --- A replayed scan, without a broker ---


@pytest.fixture
def no_packet_to_a_target(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Fail the test if anything connects or sends to a replayed address."""
    targets = {str(address) for address in RECORDED.targets}
    attempts: list[Any] = []

    def guard(name: str) -> None:
        real = getattr(socket.socket, name)

        def guarded(self: socket.socket, *arguments: Any) -> Any:
            address = arguments[-1]
            if isinstance(address, tuple) and address and address[0] in targets:
                attempts.append((name, address))
                raise AssertionError(f"{name} to the target {address}")
            return real(self, *arguments)

        monkeypatch.setattr(socket.socket, name, guarded)

    for name in ("connect", "connect_ex", "sendto"):
        guard(name)
    return attempts


async def test_replayed_scan_reaches_completed_with_no_packet_sent_to_any_target(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    orchestrator: Orchestrator,
    outbox: Outbox,
    no_packet_to_a_target: list[Any],
) -> None:
    scan = await make_scan(
        state=ScanState.QUEUED, targets=RECORDED.targets, scope=("1.0.0.0", "200.3.13.255")
    )
    await scanner_reports(orchestrator)

    await run_to_end(orchestrator, outbox, RECORDED.world)

    stored = await db_session.get_one(Scan, scan.id, populate_existing=True)
    assert (stored.state, stored.partial) == (ScanState.COMPLETED, False)
    assert no_packet_to_a_target == []
    # One observation per target; the hosts the recording says answer are reachable.
    observations = (
        await db_session.execute(
            select(HostObservation.address, HostObservation.reachable).where(
                HostObservation.scan_id == scan.id
            )
        )
    ).all()
    assert len(observations) == 80
    reachable = {str(address) for address, is_reachable in observations if is_reachable}
    probed = {"icmp", "tcp:80", "tcp:443"}
    expected = {
        address
        for address, host in RECORDED.world.items()
        if host["ping"]
        or host["closed"] == "reset"
        or probed & {f"tcp:{port}" for port in host["open_ports"]}
    }
    assert reachable == expected
    assert len(reachable) >= 11
    ports = await db_session.execute(
        select(PortObservation.address, PortObservation.port).where(
            PortObservation.scan_id == scan.id
        )
    )
    scanned = {22, 80, 443, 8080}
    assert {(str(address), port) for address, port in ports} == {
        (address, port)
        for address in reachable
        for port in RECORDED.world[address]["open_ports"]
        if port in scanned
    }


def test_the_harness_has_no_code_that_could_probe() -> None:
    """Neither the harness nor the stub it builds on imports a socket or the scanner."""
    for module in (replay_findings, stub_scanner):
        tree = ast.parse(Path(module.__file__ or "").read_text())
        imported = {
            name
            for node in ast.walk(tree)
            for name in (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
        }
        assert not {n for n in imported if n == "socket" or n.startswith("ndm.scanner")}


# --- On the broker ---


def discovery_chunk(scan_id: int = 7) -> Chunk:
    return Chunk(
        scan_id=scan_id,
        chunk_seq=0,
        phase=ChunkPhase.DISCOVERY,
        technique=Technique.SYN,
        targets=[IPv4Address("8.8.8.1"), IPv4Address("8.8.8.4")],
        ports=[],
        discovery=Discovery(icmp_echo=True, tcp_ports=[80, 443]),
        pps=1000,
        timeout_ms=1500,
        retries=1,
        seed=1,
        attestation_id=1,
        scope=[(IPv4Address("8.8.8.0"), IPv4Address("8.8.8.15"))],
    )


async def test_harness_on_a_broker_answers_chunks_from_the_recording(
    broker_url: str, topology: AbstractChannel
) -> None:
    harness = await replay_findings.start(broker_url, "test", RECORDED.world)
    try:
        await broker.publish(
            topology, broker.EXCHANGE_SCANNER, broker.chunk_queue("test"), discovery_chunk()
        )
        results = await topology.get_queue(broker.QUEUE_RESULTS)
        received: list[Any] = []
        while len(received) < 3:
            delivery = await results.get(timeout=5)
            received.append(messages.RESULTS.validate_json(delivery.body))
            await delivery.ack()
    finally:
        await harness.stop()

    assert [message.type for message in received] == ["hello", "findings", "chunk_done"]
    findings = received[1]
    assert isinstance(findings, messages.Findings)
    assert [str(host.address) for host in findings.hosts] == ["8.8.8.1"]


async def test_harness_on_a_broker_refuses_to_start_beside_a_scanner(
    broker_url: str, topology: AbstractChannel
) -> None:
    scanner = stub_scanner.StubScanner(url=broker_url, world={}, vantage_point="test")
    await scanner.start()
    try:
        with pytest.raises(ScannerPresent, match="Stop it first"):
            await replay_findings.start(broker_url, "test", RECORDED.world)
    finally:
        await scanner.stop()
