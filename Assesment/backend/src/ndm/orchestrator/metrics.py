"""Metrics in Prometheus format, and the orchestrator's HTTP health check (NFR-OPS-3).

Served on the internal network only: ``GET /metrics`` and ``GET /healthz``.

The scanner has no listening port. Its counters arrive in its heartbeat, are
stored on the vantage point, and are exported from here.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import structlog
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import FINAL_SCAN_STATES, ReferenceDataset, Scan, VantagePoint

log = structlog.get_logger(__name__)

# From a few seconds (top-100 at 5,000 packets per second) to several hours (full range).
DURATION_BUCKETS = (5, 15, 30, 60, 120, 300, 600, 1800, 3600, 7200, 14400, 28800)

QueueDepths = Callable[[], Awaitable[dict[str, int]]]


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.probe_rate = Gauge(
            "ndm_scanner_probe_rate_pps",
            "Current probe rate of the scanner, packets per second.",
            ["vantage_point"],
            registry=self.registry,
        )
        self.probes_sent = Gauge(
            "ndm_scanner_probes_sent",
            "Probes sent by the scanner since it started.",
            ["vantage_point"],
            registry=self.registry,
        )
        self.replies_received = Gauge(
            "ndm_scanner_replies_received",
            "Replies matched by the scanner since it started.",
            ["vantage_point"],
            registry=self.registry,
        )
        self.receive_drops = Gauge(
            "ndm_scanner_receive_drops",
            "Packets the kernel dropped at the scanner's receive socket since it started.",
            ["vantage_point"],
            registry=self.registry,
        )
        self.heartbeat_age = Gauge(
            "ndm_scanner_heartbeat_age_seconds",
            "Seconds since the scanner's last heartbeat.",
            ["vantage_point"],
            registry=self.registry,
        )
        self.queue_depth = Gauge(
            "ndm_queue_depth",
            "Messages waiting in a broker queue.",
            ["queue"],
            registry=self.registry,
        )
        self.scans = Gauge(
            "ndm_scans", "Scans that have not ended, by state.", ["state"], registry=self.registry
        )
        self.scan_outcomes = Counter(
            "ndm_scan_outcomes",
            "Scans that ended, by outcome.",
            ["outcome"],
            registry=self.registry,
        )
        self.scan_duration = Histogram(
            "ndm_scan_duration_seconds",
            "Time from the start of a scan to its end.",
            ["outcome"],
            buckets=DURATION_BUCKETS,
            registry=self.registry,
        )
        self.dataset_age = Gauge(
            "ndm_dataset_age_seconds",
            "Seconds since a reference dataset was last loaded.",
            ["dataset"],
            registry=self.registry,
        )
        # Scans that ended before this moment are not counted again.
        self._counted_until = datetime.now(UTC)
        self.last_tick = time.monotonic()

    async def refresh(self, db: AsyncSession, queue_depths: QueueDepths | None = None) -> None:
        """Bring the metrics up to date from the database and the broker."""
        now = datetime.now(UTC)
        for point in await db.scalars(select(VantagePoint)):
            counters = point.counters or {}
            self.probe_rate.labels(point.name).set(counters.get("current_rate_pps", 0))
            self.probes_sent.labels(point.name).set(counters.get("probes_sent", 0))
            self.replies_received.labels(point.name).set(counters.get("replies_received", 0))
            self.receive_drops.labels(point.name).set(counters.get("receive_drops", 0))
            if point.last_heartbeat_at is not None:
                self.heartbeat_age.labels(point.name).set(
                    (now - point.last_heartbeat_at).total_seconds()
                )

        loaded = await db.execute(
            select(ReferenceDataset.name, ReferenceDataset.loaded_at).where(
                ReferenceDataset.loaded_at.is_not(None)
            )
        )
        for name, loaded_at in loaded:
            if loaded_at is not None:
                self.dataset_age.labels(name).set((now - loaded_at).total_seconds())

        open_states = {"queued": 0, "running": 0, "interrupted": 0}
        for state in await db.scalars(
            select(Scan.state).where(
                Scan.state.not_in(FINAL_SCAN_STATES), Scan.deleted_at.is_(None)
            )
        ):
            open_states[state.value] += 1
        for name, count in open_states.items():
            self.scans.labels(name).set(count)

        # Each scan that ended since the last refresh is counted once.
        ended = await db.execute(
            select(Scan.state, Scan.started_at, Scan.finished_at)
            .where(Scan.finished_at > self._counted_until, Scan.finished_at <= now)
            .order_by(Scan.finished_at)
        )
        for state, started_at, finished_at in ended:
            self.scan_outcomes.labels(state.value).inc()
            if started_at is not None and finished_at is not None:
                self.scan_duration.labels(state.value).observe(
                    (finished_at - started_at).total_seconds()
                )
        self._counted_until = now

        if queue_depths is not None:
            for queue, depth in (await queue_depths()).items():
                self.queue_depth.labels(queue).set(depth)

    def ticked(self) -> None:
        self.last_tick = time.monotonic()

    def healthy(self, max_tick_age: float) -> bool:
        """True while the main loop is alive: a tick completed not long ago."""
        return time.monotonic() - self.last_tick <= max_tick_age

    def render(self) -> bytes:
        return generate_latest(self.registry)


async def serve(metrics: Metrics, host: str, port: int, max_tick_age: float) -> asyncio.Server:
    """Serve ``/metrics`` and ``/healthz`` over plain HTTP. Nothing else is answered."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(reader.readline(), timeout=5)
            method, path, *_ = request.decode("latin-1").split() + ["", ""]
            if method != "GET":
                status, body, kind = "405 Method Not Allowed", b"", "text/plain"
            elif path == "/metrics":
                status, body = "200 OK", metrics.render()
                kind = "text/plain; version=0.0.4; charset=utf-8"
            elif path == "/healthz":
                healthy = metrics.healthy(max_tick_age)
                status = "200 OK" if healthy else "503 Service Unavailable"
                body, kind = (b"ok\n" if healthy else b"stalled\n"), "text/plain"
            else:
                status, body, kind = "404 Not Found", b"", "text/plain"
            head = (
                f"HTTP/1.1 {status}\r\nContent-Type: {kind}\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            )
            writer.write(head.encode("latin-1") + body)
            await writer.drain()
        except TimeoutError, ConnectionError, UnicodeDecodeError:
            pass
        finally:
            writer.close()

    return await asyncio.start_server(handle, host, port)
