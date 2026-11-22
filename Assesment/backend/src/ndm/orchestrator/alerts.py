"""In-app alerts (FR-HIS-7, FR-HIS-8, NFR-REL-4).

An alert is a row in ``alert``, written in the same transaction as what it is
about. Its UI event is returned to the caller, which publishes it after the
commit: an event is never sent for an alert that was rolled back.
"""

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.messages import AlertCreated
from ndm.db.models import Alert

log = structlog.get_logger(__name__)

# The alert types. A text column, so that a new type needs no migration.
CHANGES_DETECTED = "changes_detected"
SCHEDULED_RUN_FAILED = "scheduled_run_failed"
SCHEDULED_RUN_SKIPPED = "scheduled_run_skipped"
SCHEDULED_RUN_REJECTED = "scheduled_run_rejected"
MESSAGE_DEAD_LETTERED = "message_dead_lettered"


async def raise_alert(
    db: AsyncSession,
    alert_type: str,
    summary: str,
    *,
    project_id: int | None,
    link: dict[str, Any] | None = None,
) -> AlertCreated:
    """Store an alert. Does not commit; publish the returned event after the commit."""
    alert = Alert(project_id=project_id, type=alert_type, summary=summary, link=link or {})
    db.add(alert)
    await db.flush()
    log.info("alert_raised", alert_id=alert.id, alert_type=alert_type, project_id=project_id)
    return AlertCreated(
        project_id=project_id, alert_id=alert.id, alert_type=alert_type, summary=summary
    )
