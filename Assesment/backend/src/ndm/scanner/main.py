"""The scanner worker: ``python -m ndm.scanner.main``.

One asyncio process. It takes chunks from its queue, probes, and publishes
what it finds. It has no listening port and no database: everything it needs
arrives in the chunk, and everything it knows leaves through the broker (C-3).

- Before the first probe of a chunk it runs its own scope check.
- Findings are published every 500 ms while a chunk runs (AD4).
- A chunk is acknowledged only after its final result has been published, so
  a chunk that was in flight when the scanner died is delivered again.
- It says hello at start-up and sends a heartbeat every 10 seconds, with its
  capabilities and counters.
- Traceroutes arrive on a queue of their own, consumed on a channel of their
  own whose prefetch is the traceroute ceiling (architecture section 7).
"""

import asyncio
import contextlib
import os
import secrets
import socket
import time
from collections.abc import Awaitable, Callable
from ipaddress import IPv4Address
from pathlib import Path

import structlog
from aio_pika.abc import AbstractIncomingMessage

from ndm.core import broker, messages
from ndm.core.logging import configure_logging
from ndm.core.messages import (
    Chunk,
    ChunkDone,
    ChunkPhase,
    ChunkRejected,
    Findings,
    Heartbeat,
    Hello,
    HopReply,
    Message,
    Technique,
    TracerouteDone,
    TracerouteHop,
    TracerouteJob,
    TracerouteMethod,
    TracerouteRejected,
)
from ndm.core.scope import parse_never_scan
from ndm.core.settings import Settings, get_settings
from ndm.scanner import hostprep
from ndm.scanner.connect import ConnectProbes
from ndm.scanner.discovery import discover
from ndm.scanner.icmp import EchoProbes
from ndm.scanner.packet import parse_tcp_reply
from ndm.scanner.ratelimit import RateLimiter
from ndm.scanner.results import ChunkResults
from ndm.scanner.scopecheck import check_chunk
from ndm.scanner.sockets import RawSockets, egress_address, open_icmp_socket
from ndm.scanner.syn import SynProbes
from ndm.scanner.traceroute import Traceroutes, check_job

log = structlog.get_logger(__name__)

FLUSH_SECONDS = 0.5
HEARTBEAT_SECONDS = 10
# Touched with every heartbeat; the container health check looks at its age.
HEARTBEAT_FILE = Path("/tmp/ndm-scanner.heartbeat")
# Chunks taken from the queue at a time. The orchestrator keeps one chunk per
# scan in flight, so this is the number of scans that can be served at once.
PREFETCH = 8
# The TTL of every probe that is not a traceroute probe.
DEFAULT_TTL = 64
# How long a cancelled scan is remembered, for chunks of it that are still queued.
CANCEL_MEMORY_SECONDS = 3600

Publish = Callable[[Message], Awaitable[None]]


class Worker:
    def __init__(self, settings: Settings, publish: Publish) -> None:
        self.settings = settings
        self._publish = publish
        # The scanner's own copies, from its own environment.
        self.never_scan = parse_never_scan(settings.never_scan)
        self.limiter = RateLimiter(settings.global_rate_pps)
        self._key = secrets.token_bytes(16)

        self.preparation = hostprep.check_host(settings.source_ports)
        self.raw: RawSockets | None = None
        self.echo: EchoProbes | None = None
        self._icmp: socket.socket | None = None

        low, high = settings.source_ports
        self._free_ports = list(range(low, high + 1))
        self._by_port: dict[int, SynProbes] = {}
        # Source ports of TCP traceroutes in flight; their answers arrive on the capture socket.
        self._trace_ports: set[int] = set()
        # Sends an empty UDP datagram with a TTL; replaceable in tests.
        self.send_udp = _send_udp
        self.traceroutes = Traceroutes(
            os.getpid() ^ 0x5A5A, self._send_icmp_ttl, self._send_tcp_ttl, self._send_udp_probe
        )
        self._in_flight: dict[tuple[int, int], SynProbes | ConnectProbes] = {}
        self._cancelled: dict[int, float] = {}

        self.probes_sent = 0
        self.replies_received = 0
        self._rate_mark = (time.monotonic(), 0)

    # --- Sockets and capabilities ---

    def open_sockets(self) -> None:
        """Try to open the raw sockets. Without NET_RAW the scanner works in connect mode."""
        loop = asyncio.get_running_loop()
        for warning in self.preparation.warnings:
            log.warning("host_preparation", problem=warning)
        if self.preparation.syn_allowed:
            try:
                self.raw = RawSockets.open(self.settings.source_ports)
                loop.add_reader(self.raw.capture, self._on_capture)
            except OSError as error:
                log.warning("no_syn_capability", reason=str(error))
        try:
            self._icmp = open_icmp_socket()
            self.echo = EchoProbes(os.getpid(), self._send_icmp)
            loop.add_reader(self._icmp, self._on_icmp)
        except OSError as error:
            log.warning("no_icmp_capability", reason=str(error))

    def close(self) -> None:
        loop = asyncio.get_running_loop()
        if self.raw is not None:
            loop.remove_reader(self.raw.capture)
            self.raw.close()
            self.raw = None
        if self._icmp is not None:
            loop.remove_reader(self._icmp)
            self._icmp.close()
            self._icmp = None

    @property
    def syn_capable(self) -> bool:
        return self.raw is not None

    @property
    def icmp_capable(self) -> bool:
        return self.echo is not None

    def _send_tcp(self, segment: bytes, target: IPv4Address) -> None:
        assert self.raw is not None
        with contextlib.suppress(BlockingIOError):  # a full send buffer: the probe is retried
            self.raw.send.sendto(segment, (str(target), 0))

    def _send_icmp(self, message: bytes, target: IPv4Address) -> None:
        assert self._icmp is not None
        with contextlib.suppress(BlockingIOError):
            self._icmp.sendto(message, (str(target), 0))

    # Traceroute probes use the same sockets with a TTL of their own. The TTL is
    # set and reset around one send; nothing else runs in between in this loop.

    def _send_icmp_ttl(self, message: bytes, target: IPv4Address, ttl: int) -> None:
        assert self._icmp is not None
        _with_ttl(self._icmp, ttl, lambda: self._send_icmp(message, target))

    def _send_tcp_ttl(self, segment: bytes, target: IPv4Address, ttl: int) -> None:
        assert self.raw is not None
        _with_ttl(self.raw.send, ttl, lambda: self._send_tcp(segment, target))

    def _send_udp_probe(self, source_port: int, target: IPv4Address, port: int, ttl: int) -> None:
        self.send_udp(source_port, target, port, ttl)

    def _on_capture(self) -> None:
        """One receive loop for all chunks in flight: drain the capture socket."""
        assert self.raw is not None
        while True:
            try:
                packet = self.raw.capture.recv(65535)
            except BlockingIOError:
                return
            reply = parse_tcp_reply(packet)
            if reply is None:
                continue
            # The destination port of a reply selects the chunk, or the traceroute.
            probes = self._by_port.get(reply.destination_port)
            if probes is not None:
                self.replies_received += 1
                probes.on_reply(reply)
            elif reply.destination_port in self._trace_ports:
                self.traceroutes.on_tcp(reply)

    def _on_icmp(self) -> None:
        assert self._icmp is not None and self.echo is not None
        while True:
            try:
                packet = self._icmp.recv(65535)
            except BlockingIOError:
                return
            self.echo.on_packet(packet)
            self.traceroutes.on_icmp(packet)

    # --- Status ---

    def status(self, kind: type[Hello] | type[Heartbeat]) -> Hello | Heartbeat:
        now = time.monotonic()
        marked_at, marked_count = self._rate_mark
        elapsed = now - marked_at
        rate = (self.probes_sent - marked_count) / elapsed if elapsed > 0 else 0.0
        self._rate_mark = (now, self.probes_sent)
        return kind(
            vantage_point=self.settings.vantage_point,
            egress_address=self.settings.egress_address,
            syn_capable=self.syn_capable,
            icmp_capable=self.icmp_capable,
            allow_non_public_targets=self.settings.allow_non_public_targets,
            probes_sent=self.probes_sent,
            replies_received=self.replies_received,
            receive_drops=self.raw.drops() if self.raw is not None else 0,
            current_rate_pps=round(rate, 1),
        )

    # --- Cancel ---

    def cancel(self, scan_id: int) -> None:
        """Stop sending for a scan. Chunks in flight return what they have."""
        now = time.monotonic()
        self._cancelled = {
            scan: at for scan, at in self._cancelled.items() if now - at < CANCEL_MEMORY_SECONDS
        }
        self._cancelled[scan_id] = now
        for (scan, _seq), probes in self._in_flight.items():
            if scan == scan_id:
                probes.cancelled = True
        log.info("scan_cancelled", scan_id=scan_id)

    # --- Chunks ---

    async def handle_chunk(self, chunk: Chunk) -> None:
        """Process one chunk and publish its results. The caller acknowledges
        the chunk after this returns, that is, after the final result is out."""
        if chunk.scan_id in self._cancelled:
            return

        # The scanner's own scope check, before anything is sent.
        reason = check_chunk(
            chunk,
            never_scan=self.never_scan,
            allow_non_public=self.settings.allow_non_public_targets,
        )
        if reason is None and chunk.technique is Technique.SYN and not self.syn_capable:
            reason = "syn_unavailable"
        if reason is not None:
            log.warning(
                "chunk_rejected", scan_id=chunk.scan_id, chunk_seq=chunk.chunk_seq, reason=reason
            )
            await self._publish(
                ChunkRejected(scan_id=chunk.scan_id, chunk_seq=chunk.chunk_seq, reason=reason)
            )
            return

        results = ChunkResults()
        # The rate in the chunk is clamped to this scanner's own ceiling.
        self.limiter.set_scan_rate(chunk.scan_id, chunk.pps)
        discovery = chunk.phase is ChunkPhase.DISCOVERY
        drops_before = self.raw.drops() if self.raw is not None else 0

        source_port: int | None = None
        probes: SynProbes | ConnectProbes
        if chunk.technique is Technique.SYN:
            source_port = self._free_ports.pop()
            probes = SynProbes(
                scan_id=chunk.scan_id,
                source=self.settings.egress_address or egress_address(chunk.targets[0]),
                source_port=source_port,
                key=self._key,
                send=self._send_tcp,
                limiter=self.limiter,
                results=results,
                timeout_ms=chunk.timeout_ms,
                retries=chunk.retries,
                seed=chunk.seed + chunk.chunk_seq,
                discovery=discovery,
            )
            self._by_port[source_port] = probes
        else:
            probes = ConnectProbes(
                scan_id=chunk.scan_id,
                limiter=self.limiter,
                results=results,
                timeout_ms=chunk.timeout_ms,
                retries=chunk.retries,
                seed=chunk.seed + chunk.chunk_seq,
                discovery=discovery,
            )
        key = (chunk.scan_id, chunk.chunk_seq)
        self._in_flight[key] = probes

        async def flush() -> None:
            findings = results.drain()
            if findings:
                await self._publish(
                    Findings(scan_id=chunk.scan_id, chunk_seq=chunk.chunk_seq, hosts=findings)
                )

        async def flush_every_half_second() -> None:
            while True:
                await asyncio.sleep(FLUSH_SECONDS)
                await flush()

        flusher = asyncio.create_task(flush_every_half_second())
        try:
            if discovery and chunk.discovery is not None:
                await discover(
                    chunk.targets,
                    chunk.discovery,
                    scan_id=chunk.scan_id,
                    limiter=self.limiter,
                    results=results,
                    timeout_ms=chunk.timeout_ms,
                    retries=chunk.retries,
                    tcp=probes,
                    echo=self.echo,
                )
            else:
                await probes.run(chunk.targets, chunk.ports)
        finally:
            flusher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await flusher
            del self._in_flight[key]
            if source_port is not None:
                del self._by_port[source_port]
                self._free_ports.append(source_port)
            if not any(scan == chunk.scan_id for scan, _ in self._in_flight):
                self.limiter.drop_scan(chunk.scan_id)
            self.probes_sent += results.probes_sent

        # The findings first, then the completion: the final result.
        await flush()
        drops = (self.raw.drops() if self.raw is not None else 0) - drops_before
        await self._publish(
            ChunkDone(
                scan_id=chunk.scan_id,
                chunk_seq=chunk.chunk_seq,
                probes_sent=results.probes_sent,
                receive_drops=drops,
                counts=[] if discovery else results.counts(chunk.targets),
            )
        )

    # --- Traceroutes ---

    async def handle_traceroute(self, job: TracerouteJob) -> None:
        """Trace one route and publish it TTL by TTL. The caller acknowledges
        the job after this returns, that is, after the final message is out."""
        reason = check_job(
            job, never_scan=self.never_scan, allow_non_public=self.settings.allow_non_public_targets
        )
        # The answers of routers arrive on the raw ICMP socket, whatever the method.
        if reason is None and not self.icmp_capable:
            reason = "no_raw_sockets"
        if reason is None and job.method is TracerouteMethod.TCP and not self.syn_capable:
            reason = "no_raw_sockets"
        if reason is not None:
            log.warning("traceroute_rejected", run_id=job.run_id, reason=reason)
            await self._publish(TracerouteRejected(run_id=job.run_id, reason=reason))
            return

        source_port: int | None = None
        if job.method is not TracerouteMethod.ICMP:
            source_port = self._free_ports.pop()
            if job.method is TracerouteMethod.TCP:
                self._trace_ports.add(source_port)

        async def publish_hop(ttl: int, replies: list[HopReply]) -> None:
            await self._publish(TracerouteHop(run_id=job.run_id, ttl=ttl, replies=replies))

        try:
            reached, sent = await self.traceroutes.run(
                job,
                limiter=self.limiter,
                publish_hop=publish_hop,
                source=self.settings.egress_address or egress_address(job.target),
                source_port=source_port,
            )
        finally:
            if source_port is not None:
                self._trace_ports.discard(source_port)
                self._free_ports.append(source_port)
        self.probes_sent += sent
        await self._publish(TracerouteDone(run_id=job.run_id, reached=reached, probes_sent=sent))


def _with_ttl(sock: socket.socket, ttl: int, send: Callable[[], None]) -> None:
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
    try:
        send()
    finally:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, DEFAULT_TTL)


def _send_udp(source_port: int, target: IPv4Address, port: int, ttl: int) -> None:
    """Send one empty UDP datagram (zero-length payload) with the given TTL."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", source_port))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
        with contextlib.suppress(BlockingIOError, ConnectionRefusedError):
            sock.sendto(b"", (str(target), port))


async def run(settings: Settings) -> None:
    connection = await broker.connect(settings.broker_url)
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=PREFETCH)
    await broker.declare_topology(channel, settings.vantage_point)

    async def publish(message: Message) -> None:
        await broker.publish(channel, broker.EXCHANGE_RESULTS, broker.QUEUE_RESULTS, message)

    worker = Worker(settings, publish)
    worker.open_sockets()
    if settings.allow_non_public_targets:
        log.warning("test_switch_enabled", setting="NDM_ALLOW_NON_PUBLIC_TARGETS")

    # Failed attempts at each chunk and traceroute job; the last one dead-letters it,
    # and the orchestrator then fails its scan or traceroute.
    failures = broker.FailedDeliveries()

    async def on_chunk(delivery: AbstractIncomingMessage) -> None:
        chunk = await broker.decode(delivery, messages.WORK)
        if chunk is None:
            return
        structlog.contextvars.bind_contextvars(scan_id=chunk.scan_id)
        try:
            await worker.handle_chunk(chunk)
            await delivery.ack()
            failures.succeeded(delivery)
            log.info("chunk_done", chunk_seq=chunk.chunk_seq)
        except Exception:
            log.exception("chunk_failed", chunk_seq=chunk.chunk_seq)
            await failures.failed(delivery)
        finally:
            structlog.contextvars.unbind_contextvars("scan_id")

    async def on_control(delivery: AbstractIncomingMessage) -> None:
        cancel = await broker.decode(delivery, messages.CONTROL)
        if cancel is not None:
            worker.cancel(cancel.scan_id)
            await delivery.ack()

    async def on_traceroute(delivery: AbstractIncomingMessage) -> None:
        job = await broker.decode(delivery, messages.TRACEROUTE_WORK)
        if job is None:
            return
        structlog.contextvars.bind_contextvars(run_id=job.run_id)
        try:
            await worker.handle_traceroute(job)
            await delivery.ack()
            failures.succeeded(delivery)
            log.info("traceroute_done")
        except Exception:
            log.exception("traceroute_failed")
            await failures.failed(delivery)
        finally:
            structlog.contextvars.unbind_contextvars("run_id")

    await publish(worker.status(Hello))
    log.info("scanner_started", syn=worker.syn_capable, icmp=worker.icmp_capable)
    chunks = await channel.get_queue(broker.chunk_queue(settings.vantage_point))
    control = await broker.declare_private_queue(channel, broker.EXCHANGE_CONTROL)
    await chunks.consume(on_chunk)
    await control.consume(on_control)
    # A channel of its own: its prefetch is the number of traceroutes at once.
    tracing = await connection.channel()
    await tracing.set_qos(prefetch_count=settings.max_concurrent_traceroutes)
    traceroutes = await tracing.get_queue(broker.traceroute_queue(settings.vantage_point))
    await traceroutes.consume(on_traceroute)

    try:
        while True:
            HEARTBEAT_FILE.touch()
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await publish(worker.status(Heartbeat))
    finally:
        worker.close()
        await connection.close()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.secret_values(), settings.log_level)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
