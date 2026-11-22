"""The vantage point: what the scanner reports about itself.

The scanner has no listening port. Its capabilities and counters arrive in its
hello and heartbeat messages and are stored here. A scanner whose heartbeat has
been missing for 30 seconds is taken to be gone.
"""

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.messages import Heartbeat, Hello
from ndm.core.settings import Settings
from ndm.db.audit import SYSTEM_ACTOR, record_audit
from ndm.db.models import VantagePoint

log = structlog.get_logger(__name__)

HEARTBEAT_TIMEOUT = timedelta(seconds=30)


async def record_status(
    db: AsyncSession, message: Hello | Heartbeat, settings: Settings
) -> VantagePoint:
    """Store the capabilities and counters of a hello or heartbeat. Does not commit."""
    vantage = await db.scalar(
        select(VantagePoint).where(VantagePoint.name == message.vantage_point).with_for_update()
    )
    if vantage is None:
        vantage = VantagePoint(name=message.vantage_point)
        db.add(vantage)
    vantage.egress_address = message.egress_address
    vantage.capabilities = {
        "syn": message.syn_capable,
        "icmp": message.icmp_capable,
        "allow_non_public_targets": message.allow_non_public_targets,
    }
    vantage.counters = {
        "probes_sent": message.probes_sent,
        "replies_received": message.replies_received,
        "receive_drops": message.receive_drops,
        "current_rate_pps": message.current_rate_pps,
    }
    vantage.last_heartbeat_at = datetime.now(UTC)
    await db.flush()

    # NFR-SEC-6: the use of the test switch goes to the audit log, with the
    # value each process has. Written when a scanner starts.
    if isinstance(message, Hello) and (
        settings.allow_non_public_targets or message.allow_non_public_targets
    ):
        effective = settings.allow_non_public_targets and message.allow_non_public_targets
        log.warning(
            "test_switch_enabled", setting="NDM_ALLOW_NON_PUBLIC_TARGETS", effective=effective
        )
        await record_audit(
            db,
            actor=SYSTEM_ACTOR,
            action="test_switch.enabled",
            object_type="system",
            details={
                "process": "orchestrator",
                "orchestrator": settings.allow_non_public_targets,
                "scanner": message.allow_non_public_targets,
                "vantage_point": message.vantage_point,
                "effective": effective,
            },
        )
    return vantage


async def current(db: AsyncSession, settings: Settings) -> VantagePoint | None:
    return await db.scalar(select(VantagePoint).where(VantagePoint.name == settings.vantage_point))


def is_alive(vantage: VantagePoint | None, now: datetime) -> bool:
    return (
        vantage is not None
        and vantage.last_heartbeat_at is not None
        and now - vantage.last_heartbeat_at <= HEARTBEAT_TIMEOUT
    )


def non_public_allowed(vantage: VantagePoint | None, settings: Settings) -> bool:
    """The test switch takes effect only when every process has it set. The API
    checks its own value when a scan is created; here the orchestrator's own
    value and the one the scanner reported are both required."""
    return bool(
        settings.allow_non_public_targets
        and vantage is not None
        and vantage.capabilities.get("allow_non_public_targets") is True
    )
