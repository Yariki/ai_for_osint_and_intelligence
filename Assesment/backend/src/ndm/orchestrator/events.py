"""UI events: what the orchestrator tells the browser while a scan runs.

Events are built from the database after a change and published after the
commit. They are transient: a lost event delays an update on screen and loses
no data, and the browser reloads through REST after a reconnect.
"""

from collections.abc import Sequence
from ipaddress import IPv4Address

import structlog
from aio_pika.abc import AbstractChannel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core import broker
from ndm.core.messages import (
    AlertCreated,
    HostRow,
    HostsChanged,
    ScanProgress,
    TracerouteHopEvent,
)
from ndm.db.models import (
    ChunkState,
    EnrichmentSnapshot,
    HostObservation,
    PortObservation,
    Scan,
    ScanChunk,
)

log = structlog.get_logger(__name__)

Event = ScanProgress | HostsChanged | TracerouteHopEvent | AlertCreated


async def scan_progress(db: AsyncSession, scan: Scan) -> ScanProgress:
    """State, phase and progress of a scan. Totals survive the deletion of the chunk rows."""
    total = await db.scalar(
        select(func.count()).select_from(ScanChunk).where(ScanChunk.scan_id == scan.id)
    )
    done = await db.scalar(
        select(func.count())
        .select_from(ScanChunk)
        .where(ScanChunk.scan_id == scan.id, ScanChunk.state == ChunkState.DONE)
    )
    counters = scan.counters or {}
    return ScanProgress(
        scan_id=scan.id,
        project_id=scan.project_id,
        state=scan.state.value,
        phase=scan.phase.value if scan.phase else None,
        chunks_done=counters.get("chunks_done", done or 0),
        chunks_total=counters.get("chunks_total", total or 0),
        probes_sent=counters.get("probes_sent", 0),
    )


async def hosts_changed(
    db: AsyncSession, scan: Scan, addresses: Sequence[IPv4Address]
) -> HostsChanged:
    """Slim rows of the given hosts of a scan, as they are stored now."""
    open_ports = (
        select(func.count())
        .select_from(PortObservation)
        .where(
            PortObservation.scan_id == HostObservation.scan_id,
            PortObservation.address == HostObservation.address,
        )
        .scalar_subquery()
    )
    # The host's place on the map, from its current snapshot if it has one.
    location = (
        select(EnrichmentSnapshot.location)
        .where(
            EnrichmentSnapshot.scan_id == HostObservation.scan_id,
            EnrichmentSnapshot.address == HostObservation.address,
            EnrichmentSnapshot.latest,
        )
        .scalar_subquery()
    )
    rows = await db.execute(
        select(HostObservation.address, HostObservation.reachable, open_ports, location)
        .where(HostObservation.scan_id == scan.id, HostObservation.address.in_(addresses))
        .order_by(HostObservation.address)
    )
    return HostsChanged(
        scan_id=scan.id,
        project_id=scan.project_id,
        hosts=[
            HostRow(
                address=address,
                reachable=reachable,
                open_port_count=count,
                latitude=point.latitude if point else None,
                longitude=point.longitude if point else None,
            )
            for address, reachable, count, point in rows
        ],
    )


def routing_key(event: Event) -> str:
    if isinstance(event, TracerouteHopEvent):
        return f"project.{event.project_id}.traceroute.{event.run_id}"
    if isinstance(event, AlertCreated):
        return f"project.{event.project_id}.alert.{event.alert_id}"
    return f"project.{event.project_id}.scan.{event.scan_id}"


async def publish_events(channel: AbstractChannel, events: Sequence[Event]) -> None:
    """Publish events after the commit. A failure is logged and never raised:
    the data is safe in the database, and the event was only a hint."""
    for event in events:
        try:
            await broker.publish(
                channel, broker.EXCHANGE_EVENTS, routing_key(event), event, persistent=False
            )
        except Exception:
            log.warning("event_not_published", event_type=event.type, key=routing_key(event))
