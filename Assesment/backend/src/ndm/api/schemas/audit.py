"""Models of the audit list endpoint."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditEventOut(BaseModel):
    id: int
    occurred_at: datetime
    actor: str
    action: str
    object_type: str
    object_id: str | None
    details: dict[str, Any]
