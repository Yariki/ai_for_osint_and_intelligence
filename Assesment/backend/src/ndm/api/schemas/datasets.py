"""Models of the dataset endpoints (FR-ENR-6)."""

from datetime import datetime

from pydantic import BaseModel


class DatasetOut(BaseModel):
    """One reference dataset: its version, its age, and how its last update went."""

    name: str
    version: str | None
    status: str
    """``missing``: no data. ``ok``: data that is in use. ``stale``: data that is
    older than its licence allows, and is not used."""
    detail: str | None
    """Why the last update failed, or why the dataset is not updated here."""
    loaded_at: datetime | None
    age_seconds: int | None
    """Seconds since the data was loaded. Null while there is none."""
    checked_at: datetime | None
    """When an update was last tried."""
    update_requested: bool
    """An update was asked for and has not been carried out yet."""
