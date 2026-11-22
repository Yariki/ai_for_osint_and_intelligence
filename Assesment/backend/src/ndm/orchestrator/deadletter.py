"""The dead-letter consumer (NFR-REL-4).

A message that fails validation, or that was delivered five times without an
acknowledgement, ends in the dead-letter queue. It is taken from there once:
the scan it belongs to is failed with the reason, and the message is gone.
Nothing loops. Every message taken from there raises one alert.
"""

import json
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.messages import AlertCreated
from ndm.db.models import FINAL_SCAN_STATES, Scan, ScanState, TracerouteRun
from ndm.orchestrator import alerts, traceroute
from ndm.orchestrator.recording import finish_scan

log = structlog.get_logger(__name__)


def _identifier(body: bytes, name: str) -> int | None:
    """The scan or run a message is about, read without trusting anything else in it."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    value = data.get(name) if isinstance(data, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _origin(headers: dict[str, Any]) -> tuple[str, str]:
    """The queue a message died in and why, from the broker's x-death header."""
    deaths = headers.get("x-death")
    if isinstance(deaths, list) and deaths and isinstance(deaths[0], dict):
        return str(deaths[0].get("queue", "unknown")), str(deaths[0].get("reason", "unknown"))
    return "unknown", "unknown"


async def handle_dead_letter(
    db: AsyncSession, body: bytes, headers: dict[str, Any]
) -> Scan | TracerouteRun | None:
    """Fail the scan or the traceroute run a dead-lettered message belongs to.
    Does not commit.

    Returns what was failed. A message with no usable identifier, or for a
    scan or run that has already ended, is only logged.
    """
    queue, cause = _origin(headers)
    scan_id = _identifier(body, "scan_id")
    run_id = _identifier(body, "run_id")
    if scan_id is None and run_id is not None:
        run = await traceroute.fail_lost(db, run_id, f"dead_letter: {cause} in {queue}")
        if run is None:
            log.warning("dead_letter_ignored", run_id=run_id, queue=queue, cause=cause)
        else:
            log.error("traceroute_failed", run_id=run_id, reason="dead_letter")
        return run
    if scan_id is None:
        log.error("dead_letter_without_scan", queue=queue, cause=cause, size=len(body))
        return None

    scan = await db.scalar(select(Scan).where(Scan.id == scan_id).with_for_update())
    if scan is None or scan.state in FINAL_SCAN_STATES:
        log.warning("dead_letter_ignored", scan_id=scan_id, queue=queue, cause=cause)
        return None

    reason = f"dead_letter: {cause} in {queue}"
    await finish_scan(db, scan, ScanState.FAILED, reason=reason)
    log.error("scan_failed", scan_id=scan_id, reason=reason)
    return scan


async def alert_dead_letter(db: AsyncSession, body: bytes, headers: dict[str, Any]) -> AlertCreated:
    """One alert for a message taken from the dead-letter queue (NFR-REL-4).
    Does not commit.

    The alert belongs to the project of the scan or run the message names; a
    message that names neither is an alert about the installation.
    """
    queue, cause = _origin(headers)
    scan_id = _identifier(body, "scan_id")
    run_id = _identifier(body, "run_id")
    project_id = None
    if scan_id is not None:
        project_id = await db.scalar(select(Scan.project_id).where(Scan.id == scan_id))
        about, link = f"scan {scan_id}", {"scan_id": scan_id}
    elif run_id is not None:
        project_id = await db.scalar(
            select(TracerouteRun.project_id).where(TracerouteRun.id == run_id)
        )
        about, link = f"traceroute {run_id}", {"traceroute_run_id": run_id}
    else:
        about, link = "no known scan or traceroute", {}
    return await alerts.raise_alert(
        db,
        alerts.MESSAGE_DEAD_LETTERED,
        f"A message for {about} could not be processed ({cause} in {queue}).",
        project_id=project_id,
        link=link,
    )
