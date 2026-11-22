"""The tick: one pass over the state in the database (AD1).

Everything the orchestrator does to a scan happens here, from what the
database says. Messages from the API or the scanner only make the next tick
come sooner; a lost or repeated message can neither stall nor corrupt a scan.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ndm.core.messages import Cancel
from ndm.core.scope import ScopeEntry
from ndm.core.settings import Settings
from ndm.db.models import Scan
from ndm.orchestrator import lifecycle, traceroute, vantage
from ndm.orchestrator.dispatch import PublishChunk, dispatch
from ndm.orchestrator.enrichment import Enricher
from ndm.orchestrator.events import Event, scan_progress

log = structlog.get_logger(__name__)

TICK_SECONDS = 5


@dataclass
class Orchestrator:
    """What a tick needs: the database, the configuration, and the three ways out."""

    sessions: async_sessionmaker[AsyncSession]
    settings: Settings
    never_scan: Sequence[ScopeEntry]
    publish_chunk: PublishChunk
    publish_cancel: Callable[[Cancel], Awaitable[None]]
    publish_events: Callable[[Sequence[Event]], Awaitable[None]]
    enricher: Enricher | None = None
    """Enriches hosts as they are found. Without it, findings are recorded as they are."""
    publish_traceroute: traceroute.PublishJob | None = None
    """Sends a traceroute job to the scanner. Without it, no traceroute is dispatched."""


async def tick(orchestrator: Orchestrator, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    events: list[Event] = []

    async with orchestrator.sessions() as db:
        point = await vantage.current(db, orchestrator.settings)
        alive = vantage.is_alive(point, now)

        # 1. Cancellations and deletions: stop, keep the results.
        cancelled = await lifecycle.cancel_requested(db)
        events += [await scan_progress(db, scan) for scan in cancelled]
        await db.commit()
        for scan in cancelled:
            await orchestrator.publish_cancel(Cancel(scan_id=scan.id))

        # Scheduled scans that failed since the last tick: one alert each.
        events += await lifecycle.alert_failed_scheduled_runs(db)
        await db.commit()

        # 2. A scanner that stopped sending heartbeats: its scans are interrupted.
        if not alive:
            interrupted = await lifecycle.mark_interrupted(db)
            events += [await scan_progress(db, scan) for scan in interrupted]
            await db.commit()

        # 3. Queue. Without a live scanner nothing is started: its capabilities
        #    decide the technique.
        if alive and point is not None:
            started = await lifecycle.promote_queued(
                db, point, orchestrator.settings.max_concurrent_scans
            )
            events += [await scan_progress(db, scan) for scan in started]
            await db.commit()

        # 4. Traceroutes: their own ceiling and their own queue, so a running
        #    scan never delays one (check 3.11).
        if alive and point is not None and orchestrator.publish_traceroute is not None:
            runs = await traceroute.dispatch(
                db,
                orchestrator.publish_traceroute,
                vantage=point,
                ceiling=orchestrator.settings.max_concurrent_traceroutes,
                never_scan=orchestrator.never_scan,
                allow_non_public=vantage.non_public_allowed(point, orchestrator.settings),
                now=now,
            )
            events += [await traceroute.hop_event(db, run, None) for run in runs]

        active_ids = list(
            await db.scalars(
                select(Scan.id)
                .where(Scan.state.in_(lifecycle.ACTIVE_STATES), Scan.deleted_at.is_(None))
                .order_by(Scan.id)
            )
        )
        allow_non_public = vantage.non_public_allowed(point, orchestrator.settings)

    # 5. Each running scan in a transaction of its own, so that a failure in
    #    one scan does not hold up the others.
    for scan_id in active_ids:
        structlog.contextvars.bind_contextvars(scan_id=scan_id)
        try:
            async with orchestrator.sessions() as db:
                active = await db.scalar(select(Scan).where(Scan.id == scan_id).with_for_update())
                if active is None or active.state not in lifecycle.ACTIVE_STATES:
                    continue
                changed = await lifecycle.advance(db, active)
                await db.commit()
                if alive and active.state in lifecycle.ACTIVE_STATES:
                    changed |= await dispatch(
                        db,
                        active,
                        orchestrator.publish_chunk,
                        never_scan=orchestrator.never_scan,
                        allow_non_public=allow_non_public,
                        now=now,
                    )
                if changed:
                    events.append(await scan_progress(db, active))
        except Exception:
            # The transaction is rolled back; the next tick tries again.
            log.exception("tick_failed_for_scan")
        finally:
            structlog.contextvars.unbind_contextvars("scan_id")

    await orchestrator.publish_events(events)
