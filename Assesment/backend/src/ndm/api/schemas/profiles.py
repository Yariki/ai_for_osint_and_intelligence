"""Models of the scan profile endpoints (FR-CFG-8)."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

from ndm.api.schemas.scans import ScanSettings

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ProfileIn(BaseModel):
    name: Name
    parameters: ScanSettings


class ProfileOut(BaseModel):
    id: int
    name: str
    parameters: ScanSettings
    port_count: int
    created_at: datetime
    updated_at: datetime
