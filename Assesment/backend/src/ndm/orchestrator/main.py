"""Entry point of the orchestrator: ``python -m ndm.orchestrator.main``.

Exactly one orchestrator works at a time. Each instance takes a PostgreSQL
advisory lock at start-up and does nothing until it holds it; a second
instance started by mistake stays idle (architecture section 11.5).
"""

import asyncio
import contextlib

import httpx
import structlog
from aio_pika.abc import AbstractChannel, AbstractIncomingMessage
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ndm.core import broker, messages
from ndm.core.logging import configure_logging
from ndm.core.messages import Cancel, Chunk, Heartbeat, Hello, TracerouteJob
from ndm.core.scope import parse_never_scan
from ndm.core.settings import Settings, get_settings
from ndm.db.models import ScanPhase, TracerouteRun
from ndm.db.session import create_engine, create_session_factory
from ndm.orchestrator import datasets, diff_phase, lifecycle, phases, scheduler, traceroute, vantage
from ndm.orchestrator import metrics as metrics_server
from ndm.orchestrator.classification import load_ruleset
from ndm.orchestrator.classification_phase import Classifier
from ndm.orchestrator.datasets.geolite import GeoLite
from ndm.orchestrator.datasets.manager import DatasetManager
from ndm.orchestrator.deadletter import alert_dead_letter, handle_dead_letter
from ndm.orchestrator.enrichment import Enricher
from ndm.orchestrator.events import Event, publish_events, scan_progress
from ndm.orchestrator.metrics import Metrics
from ndm.orchestrator.purge import purge
from ndm.orchestrator.recording import record_result
from ndm.orchestrator.tick import TICK_SECONDS, Orchestrator, tick

log = structlog.get_logger(__name__)

# Key of the advisory lock that makes the orchestrator a single instance.
LOCK_KEY = 7_201_002
LOCK_RETRY_SECONDS = 5
# Pause before a message that could not be processed is offered again.
RETRY_SECONDS = 2
# Failed attempts at each result message; the last one dead-letters it.
RESULT_FAILURES = broker.FailedDeliveries()
# How often the rows of deleted scans and projects are removed.
PURGE_SECONDS = 60
# How often the phase runner looks for a scan that has finished probing.
PHASE_SECONDS = 2
# How often the dataset manager looks for a dataset that is due or was asked for.
DATASET_SECONDS = 15
# What the registries and MaxMind see as the client.
USER_AGENT = "network-discovery-map"
# Metrics and health check, on the internal network only.
METRICS_PORT = 9108
# The health check fails when no tick has completed for this long.
MAX_TICK_AGE_SECONDS = 6 * TICK_SECONDS


class SingleInstanceLock:
    """A session-level advisory lock, held on a connection of its own.

    The lock belongs to the connection: if this process dies, PostgreSQL
    releases it and a waiting instance takes over.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._connection: AsyncConnection | None = None

    async def try_acquire(self) -> bool:
        if self._connection is None:
            self._connection = await self._engine.connect()
        held = await self._connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}
        )
        await self._connection.commit()
        return bool(held)

    async def release(self) -> None:
        if self._connection is None:
            return
        # Closing would only hand the connection back to the pool, with the
        # lock still held by its database session. Unlock, then discard it.
        with contextlib.suppress(Exception):
            await self._connection.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY}
            )
            await self._connection.commit()
        await self._connection.invalidate()
        await self._connection.close()
        self._connection = None


async def handle_result(
    delivery: AbstractIncomingMessage,
    orchestrator: Orchestrator,
    wake: asyncio.Event,
) -> None:
    """Process one message of the results queue, then acknowledge it.

    The queue is consumed one message at a time, in order, so findings are
    recorded before the completion of their chunk.
    """
    message = await broker.decode(delivery, messages.RESULTS)
    if message is None:
        return  # rejected: it is in the dead-letter queue now
    scan_id = getattr(message, "scan_id", None)
    structlog.contextvars.bind_contextvars(scan_id=scan_id)
    try:
        async with orchestrator.sessions() as db:
            events: list[Event]
            if isinstance(message, Hello | Heartbeat):
                await vantage.record_status(db, message, orchestrator.settings)
                await db.commit()
                events = []
            elif isinstance(
                message,
                messages.TracerouteHop | messages.TracerouteDone | messages.TracerouteRejected,
            ):
                events = list(
                    await traceroute.record_traceroute(db, message, orchestrator.enricher)
                )
                await db.commit()
            else:
                events = await record_result(db, message, orchestrator.enricher)
                await db.commit()
        await orchestrator.publish_events(events)
        await delivery.ack()
        RESULT_FAILURES.succeeded(delivery)
        # A finished or rejected chunk, or a scanner that came back: act on it now.
        if not isinstance(message, messages.Findings | Heartbeat | messages.TracerouteHop):
            wake.set()
    except Exception:
        # A failure that may pass, such as the database being away. The message
        # is offered again, up to the delivery limit; then it is dead-lettered.
        log.exception("result_not_processed", message_type=message.type)
        await asyncio.sleep(RETRY_SECONDS)
        await RESULT_FAILURES.failed(delivery)
    finally:
        structlog.contextvars.unbind_contextvars("scan_id")


async def handle_command(delivery: AbstractIncomingMessage, wake: asyncio.Event) -> None:
    """A command says only that something changed. The tick reads what."""
    if await broker.decode(delivery, messages.COMMANDS) is not None:
        await delivery.ack()
        wake.set()


async def handle_dead(
    delivery: AbstractIncomingMessage, orchestrator: Orchestrator, wake: asyncio.Event
) -> None:
    """Take one message from the dead-letter queue: fail its scan, and be done with it."""
    try:
        async with orchestrator.sessions() as db:
            headers = dict(delivery.headers or {})
            dead = await handle_dead_letter(db, delivery.body, headers)
            events: list[Event] = [await alert_dead_letter(db, delivery.body, headers)]
            if isinstance(dead, TracerouteRun):
                events.append(await traceroute.hop_event(db, dead, None))
            elif dead is not None:
                events.append(await scan_progress(db, dead))
            await db.commit()
        await orchestrator.publish_events(events)
        await delivery.ack()
        wake.set()
    except Exception:
        log.exception("dead_letter_not_processed")
        await asyncio.sleep(RETRY_SECONDS)
        await delivery.nack(requeue=True)


async def purge_forever(orchestrator: Orchestrator) -> None:
    """Remove the rows of deleted scans and projects, beside the tick and not in it:
    a large deletion must not delay the dispatch of chunks."""
    while True:
        try:
            await purge(orchestrator.sessions)
        except Exception:
            log.exception("purge_failed")
        await asyncio.sleep(PURGE_SECONDS)


async def phases_forever(orchestrator: Orchestrator, classifier: Classifier | None = None) -> None:
    """Run the phases after probing, beside the tick and not in it: a phase can
    take minutes, and the dispatch of chunks must not wait for it."""
    while True:
        try:
            busy = await phases.run_pending(orchestrator.sessions, orchestrator.publish_events)
            if orchestrator.enricher is not None:
                # Re-runs of enrichment the analyst asked for, one scan after another.
                await orchestrator.enricher.rerun_requested(orchestrator.sessions)
                # Reverse DNS of traceroute hops, apart from the recording of hops.
                busy |= await orchestrator.enricher.resolve_hop_names(orchestrator.sessions) > 0
            if classifier is not None:
                # Re-runs of classification the analyst asked for (FR-CLS-7).
                await classifier.rerun_requested(orchestrator.sessions)
        except Exception:
            log.exception("phase_runner_failed")
            busy = False
        if not busy:
            await asyncio.sleep(PHASE_SECONDS)


async def scheduler_forever(orchestrator: Orchestrator, wake: asyncio.Event) -> None:
    """Start the scheduled runs that are due, beside the tick. A scan it creates
    is queued; the tick is woken to start it at once."""
    while True:
        try:
            outcome = await scheduler.run_due(
                orchestrator.sessions, orchestrator.settings, orchestrator.never_scan
            )
            await orchestrator.publish_events(outcome.alerts)
            if outcome.started:
                wake.set()
        except Exception:
            log.exception("scheduler_failed")
        await asyncio.sleep(scheduler.SCHEDULER_SECONDS)


async def datasets_forever(manager: DatasetManager) -> None:
    """Keep the reference datasets up to date, beside the tick: a download
    takes its time, and a scan must never wait for one."""
    while True:
        try:
            await manager.run_due()
        except Exception:
            log.exception("dataset_manager_failed")
        await asyncio.sleep(DATASET_SECONDS)


async def queue_depths(channel: AbstractChannel, vantage_point: str) -> dict[str, int]:
    depths = {}
    for name in (
        broker.chunk_queue(vantage_point),
        broker.QUEUE_RESULTS,
        broker.QUEUE_COMMANDS,
        broker.QUEUE_DEAD,
    ):
        queue = await channel.declare_queue(name, passive=True)
        depths[name] = queue.declaration_result.message_count or 0
    return depths


def build(
    settings: Settings,
    engine: AsyncEngine,
    channel: AbstractChannel,
    enricher: Enricher | None = None,
) -> Orchestrator:
    async def publish_chunk(chunk: Chunk) -> None:
        await broker.publish(
            channel, broker.EXCHANGE_SCANNER, broker.chunk_queue(settings.vantage_point), chunk
        )

    async def publish_cancel(cancel: Cancel) -> None:
        await broker.publish(channel, broker.EXCHANGE_CONTROL, "", cancel, persistent=False)

    async def publish_traceroute(job: TracerouteJob) -> None:
        await broker.publish(
            channel,
            broker.EXCHANGE_SCANNER,
            broker.traceroute_queue(settings.vantage_point),
            job,
        )

    return Orchestrator(
        sessions=create_session_factory(engine),
        settings=settings,
        never_scan=parse_never_scan(settings.never_scan),
        publish_chunk=publish_chunk,
        publish_cancel=publish_cancel,
        publish_events=lambda events: publish_events(channel, events),
        enricher=enricher,
        publish_traceroute=publish_traceroute,
    )


async def run(settings: Settings) -> None:
    engine = create_engine(settings.database_url)
    lock = SingleInstanceLock(engine)
    while not await lock.try_acquire():
        log.info("standing_by", reason="another orchestrator holds the lock")
        await asyncio.sleep(LOCK_RETRY_SECONDS)
    log.info("orchestrator_started")

    connection = await broker.connect(settings.broker_url)
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=1)
    await broker.declare_topology(channel, settings.vantage_point)
    # Enrichment: the GeoLite2 readers, the phase after probing, and the
    # manager that keeps every reference dataset up to date.
    geolite = GeoLite(settings.reference_data_path)
    geolite.reload()
    enricher = Enricher(settings, geolite)
    orchestrator = build(settings, engine, channel, enricher)
    phases.register(ScanPhase.ENRICHMENT, enricher.run_phase)
    # Classification follows enrichment: it reads the snapshots. The ruleset is
    # read once; a newer one takes effect at the next start (FR-CLS-4).
    ruleset = load_ruleset()
    classifier = Classifier(ruleset, settings.classification_threshold)
    phases.register(ScanPhase.CLASSIFICATION, classifier.run_phase)
    log.info("ruleset_loaded", version=ruleset.version, rules=len(ruleset.rules))
    # The diff of a scheduled scan against its baseline; manual scans skip it.
    phases.register(ScanPhase.DIFF, diff_phase.run_phase)
    downloads = httpx.AsyncClient(headers={"User-Agent": USER_AGENT})
    manager = DatasetManager(
        orchestrator.sessions,
        datasets.build(settings, downloads, geolite),
        settings.secret_values(),
    )

    # Whatever was running when the previous instance stopped is interrupted
    # until the scanner is heard from again.
    async with orchestrator.sessions() as db:
        await lifecycle.mark_interrupted(db)
        await db.commit()

    wake = asyncio.Event()
    results = await channel.get_queue(broker.QUEUE_RESULTS)
    commands = await channel.get_queue(broker.QUEUE_COMMANDS)
    dead = await channel.get_queue(broker.QUEUE_DEAD)
    await results.consume(lambda delivery: handle_result(delivery, orchestrator, wake))
    await commands.consume(lambda delivery: handle_command(delivery, wake))
    await dead.consume(lambda delivery: handle_dead(delivery, orchestrator, wake))

    metrics = Metrics()
    server = await metrics_server.serve(metrics, "0.0.0.0", METRICS_PORT, MAX_TICK_AGE_SECONDS)
    purging = asyncio.create_task(purge_forever(orchestrator))
    running_phases = asyncio.create_task(phases_forever(orchestrator, classifier))
    updating = asyncio.create_task(datasets_forever(manager))
    scheduling = asyncio.create_task(scheduler_forever(orchestrator, wake))

    try:
        while True:
            try:
                await tick(orchestrator)
                metrics.ticked()
                async with orchestrator.sessions() as db:
                    await metrics.refresh(db, lambda: queue_depths(channel, settings.vantage_point))
            except Exception:
                log.exception("tick_failed")
            wake.clear()
            # The next tick comes after 5 seconds, or at once when a message says so.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=TICK_SECONDS)
    finally:
        purging.cancel()
        running_phases.cancel()
        updating.cancel()
        scheduling.cancel()
        await downloads.aclose()
        geolite.close()
        server.close()
        await connection.close()
        await lock.release()
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.secret_values(), settings.log_level)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
