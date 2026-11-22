"""Models of the comparison endpoints (FR-HIS-2 to FR-HIS-5)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ChangeOut(BaseModel):
    address: str
    kind: str
    """host_appeared, host_disappeared, port_opened, port_closed, ptr_changed,
    classification_changed or enrichment_changed."""
    port: int | None
    field: str | None
    """The enrichment field that changed."""
    before: Any
    after: Any
    dataset_change: bool
    """The dataset behind the field has another version in the later scan:
    the change may come from a database update, not from the network (FR-HIS-5)."""


class DiffOut(BaseModel):
    id: int | None
    """The stored diff; null for a comparison computed on request."""
    stored: bool
    base_scan_id: int
    target_scan_id: int
    compared_ports: list[tuple[int, int]]
    """The ports probed in both scans, as inclusive ranges. Port changes are
    reported for these only (FR-HIS-4)."""
    not_comparable: list[str]
    """Addresses targeted by one of the two scans only (FR-HIS-2)."""
    summary: dict[str, int]
    changes: list[ChangeOut]
    created_at: datetime | None


class BaselineOut(BaseModel):
    """The scan a scan is compared with by the change-status filter."""

    scan_id: int
    base_scan_id: int | None
    """Null when there is no earlier completed scan to compare with."""
