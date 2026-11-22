"""Models of the alert endpoints (FR-HIS-7, FR-HIS-8)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AlertOut(BaseModel):
    id: int
    project_id: int | None
    """Null for an alert about the installation, such as a dead-lettered message."""
    project_name: str | None
    type: str
    summary: str
    link: dict[str, Any]
    """What the alert points at: a stored diff, a scan or a schedule."""
    created_at: datetime
    read_at: datetime | None


class UnreadOut(BaseModel):
    unread: int


class MarkedOut(BaseModel):
    marked: int
