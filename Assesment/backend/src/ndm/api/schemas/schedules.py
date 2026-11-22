"""Models of the schedule endpoints (FR-SCH-1, FR-SCH-2)."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, model_validator

from ndm.api.schemas.scans import ScanSettings, Targets

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ScheduleIn(BaseModel):
    """A recurring scan: targets, parameters, and an interval or a cron
    expression read in a time zone."""

    name: Name
    targets: Targets
    parameters: ScanSettings
    interval_seconds: int | None = Field(default=None, ge=1)
    cron: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    time_zone: Annotated[str, StringConstraints(max_length=64)] = "UTC"
    enabled: bool = True

    @model_validator(mode="after")
    def _interval_or_cron(self) -> ScheduleIn:
        if (self.interval_seconds is None) == (self.cron is None):
            raise ValueError("give either interval_seconds or cron")
        return self


class ScheduleRunOut(BaseModel):
    planned_at: datetime
    outcome: str
    """started, skipped or rejected."""
    reason: str | None
    scan_id: int | None
    scan_state: str | None
    """How the started scan ended, or where it is now; null if it was deleted."""
    created_at: datetime


class ScheduleOut(BaseModel):
    id: int
    project_id: int
    name: str
    targets: list[str]
    parameters: ScanSettings
    interval_seconds: int | None
    cron: str | None
    time_zone: str
    enabled: bool
    flag_reason: str | None
    """Why the scheduler stopped running the schedule; cleared when it is saved again."""
    next_run_at: datetime
    last_run: ScheduleRunOut | None
    created_at: datetime
