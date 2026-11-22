"""A stub scanner for tests: it plays the scanner on the broker and sends no packet.

It answers chunks from a scripted "world", a description of which addresses
have a host and which ports are open there, so its findings are right for any
chunk the orchestrator plans. The orchestrator can therefore be built and
tested before the real scanner exists.

Two parts:

- ``respond`` is a pure function: a chunk and a world in, the result messages
  a scanner would publish for it out.
- ``StubScanner`` connects that function to the broker and can misbehave on
  request: duplicate, reorder and delay its messages, reject chunks, or stop
  without acknowledging a chunk, as a crashed scanner would.
"""

import asyncio
import json
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractIncomingMessage, AbstractRobustConnection

from ndm.core import broker, messages
from ndm.core.messages import (
    Chunk,
    ChunkDone,
    ChunkPhase,
    ChunkRejected,
    Evidence,
    Findings,
    Heartbeat,
    Hello,
    HostCounts,
    HostFinding,
)

RECORDED_SCAN = Path(__file__).resolve().parents[1] / "fixtures" / "recorded_scan.json"

# address -> {"ping": bool, "open_ports": [int], "closed": "reset" | "drop"}
World = dict[str, dict[str, Any]]
Result = Findings | ChunkDone | ChunkRejected

# How many hosts one findings message holds. Small, so that a chunk gives
# several findings messages, as the real scanner's 500 ms flushes do.
FINDINGS_BATCH = 2


def respond(
    chunk: Chunk, world: World, now: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> list[Result]:
    """The result messages of one chunk: findings first, then the chunk's completion."""
    findings: list[HostFinding] = []
    counts: list[HostCounts] = []

    for target in chunk.targets:
        host = world.get(str(target))
        open_ports = set(host["open_ports"]) if host is not None else set()
        resets = host is not None and host["closed"] == "reset"

        if chunk.phase is ChunkPhase.DISCOVERY:
            assert chunk.discovery is not None
            evidence = {}
            if host is not None and chunk.discovery.icmp_echo and host["ping"]:
                evidence["icmp"] = Evidence(at=now(), rtt_ms=1.5)
            for port in chunk.discovery.tcp_ports:
                # A SYN/ACK and a reset both show that the host is there (FR-SCN-1).
                if port in open_ports or resets:
                    evidence[f"tcp:{port}"] = Evidence(at=now(), rtt_ms=2.5)
            if evidence:
                findings.append(HostFinding(address=target, evidence=evidence))
        else:
            found = sorted(open_ports & set(chunk.ports))
            if found:
                evidence = {f"tcp:{port}": Evidence(at=now(), rtt_ms=2.5) for port in found}
                findings.append(HostFinding(address=target, evidence=evidence, open_ports=found))
            not_open = len(chunk.ports) - len(found)
            counts.append(
                HostCounts(
                    address=target,
                    closed=not_open if resets else 0,
                    filtered=0 if resets else not_open,
                )
            )

    results: list[Result] = [
        Findings(
            scan_id=chunk.scan_id,
            chunk_seq=chunk.chunk_seq,
            hosts=findings[start : start + FINDINGS_BATCH],
        )
        for start in range(0, len(findings), FINDINGS_BATCH)
    ]
    probes = len(chunk.targets) * (len(chunk.ports) or 1)
    results.append(
        ChunkDone(
            scan_id=chunk.scan_id,
            chunk_seq=chunk.chunk_seq,
            probes_sent=probes,
            receive_drops=0,
            counts=counts,
        )
    )
    return results


@dataclass
class RecordedScan:
    """The complete message sequence of one small scan (tests/fixtures/recorded_scan.json)."""

    world: World
    scan: dict[str, Any]
    chunks: list[Chunk]
    results: list[Result]

    def for_scan(self, scan_id: int) -> list[Result]:
        """The recorded results, addressed to another scan."""
        return [result.model_copy(update={"scan_id": scan_id}) for result in self.results]


def stamped(result: Result, at: datetime) -> Result:
    """The result with every reply in it timed at ``at``.

    The real scanner sends a reply within one 500 ms flush of receiving it.
    The stub, which can pause between messages, times each message's replies
    as it sends them; otherwise a host in the last message of a chunk would
    look late by every pause before it.
    """
    if not isinstance(result, Findings):
        return result
    hosts = [
        host.model_copy(
            update={
                "evidence": {
                    probe: item.model_copy(update={"at": at})
                    for probe, item in host.evidence.items()
                }
            }
        )
        for host in result.hosts
    ]
    return result.model_copy(update={"hosts": hosts})


def load_recorded_scan(path: Path = RECORDED_SCAN) -> RecordedScan:
    data = json.loads(path.read_text())
    results: list[Result] = []
    for item in data["results"]:
        result = messages.RESULTS.validate_python(item)
        assert isinstance(result, Findings | ChunkDone | ChunkRejected)
        results.append(result)
    return RecordedScan(
        world=data["world"],
        scan=data["scan"],
        chunks=[Chunk.model_validate(item) for item in data["chunks"]],
        results=results,
    )


@dataclass
class StubScanner:
    """The scanner's side of the broker protocol, driven by a world."""

    url: str
    world: World
    vantage_point: str = "test"
    # Capabilities reported in hello and heartbeat.
    syn_capable: bool = True
    icmp_capable: bool = True
    allow_non_public_targets: bool = False
    # Fault modes.
    duplicate: bool = False
    """Publish every result twice."""
    reorder: bool = False
    """Publish the results of a chunk in a shuffled order."""
    delay: float = 0.0
    """Seconds to wait before each result."""
    reject_chunks: set[int] = field(default_factory=set)
    """Chunk numbers to answer with a rejection, as after a failed scope check."""
    crash_on_chunk: int | None = None
    """Stop without acknowledging this chunk, the first time it arrives."""

    chunks_seen: list[Chunk] = field(default_factory=list)
    crashed: bool = False
    _connection: AbstractRobustConnection | None = None
    _channel: AbstractChannel | None = None

    async def start(self) -> None:
        self._connection = await aio_pika.connect_robust(self.url)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=1)
        await broker.declare_topology(self._channel, self.vantage_point)
        await self._publish(Hello(**self._status()))
        queue = await self._channel.get_queue(broker.chunk_queue(self.vantage_point))
        await queue.consume(self._on_chunk)

    async def heartbeat(self) -> None:
        await self._publish(Heartbeat(**self._status()))

    async def stop(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    def _status(self) -> dict[str, Any]:
        return {
            "vantage_point": self.vantage_point,
            "egress_address": IPv4Address("192.0.2.1"),
            "syn_capable": self.syn_capable,
            "icmp_capable": self.icmp_capable,
            "allow_non_public_targets": self.allow_non_public_targets,
        }

    async def _publish(self, message: messages.Message) -> None:
        assert self._channel is not None
        await broker.publish(self._channel, broker.EXCHANGE_RESULTS, broker.QUEUE_RESULTS, message)

    async def _on_chunk(self, delivery: AbstractIncomingMessage) -> None:
        chunk = await broker.decode(delivery, messages.WORK)
        if chunk is None:
            return
        self.chunks_seen.append(chunk)

        if chunk.chunk_seq == self.crash_on_chunk and not self.crashed:
            # Close the connection with the chunk unacknowledged: the broker
            # delivers it again to the next scanner that connects.
            self.crashed = True
            await self.stop()
            return

        results: list[Result]
        if chunk.chunk_seq in self.reject_chunks:
            results = [
                ChunkRejected(
                    scan_id=chunk.scan_id, chunk_seq=chunk.chunk_seq, reason="out_of_scope"
                )
            ]
        else:
            results = respond(chunk, self.world)
        if self.reorder:
            random.shuffle(results)
        if self.duplicate:
            results = [copy for result in results for copy in (result, result)]

        for result in results:
            if self.delay:
                await asyncio.sleep(self.delay)
                result = stamped(result, datetime.now(UTC))
            await self._publish(result)
        # As the real scanner does: acknowledge only after the final result is published.
        await delivery.ack()
