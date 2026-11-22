"""Models of the project and scope endpoints."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from ndm.db.models import ProjectState, ScopeEntryKind

ProjectName = Annotated[str, StringConstraints(min_length=1, max_length=200)]
ProjectDescription = Annotated[str, StringConstraints(max_length=5000)]


class ProjectCreate(BaseModel):
    name: ProjectName
    description: ProjectDescription = ""


class ProjectUpdate(BaseModel):
    """Rename or describe. A field that is left out keeps its value."""

    name: ProjectName | None = None
    description: ProjectDescription | None = None


class ProjectOut(BaseModel):
    id: int
    name: str
    description: str
    state: ProjectState
    created_at: datetime


class ScopeEntryOut(BaseModel):
    kind: ScopeEntryKind
    text: str
    """The entry exactly as it was entered."""
    start: str
    end: str


class ScopeOut(BaseModel):
    """One attested version of a scope. All fields are null or empty before the first save."""

    attestation_id: int | None
    attested_at: datetime | None
    attested_by: str | None
    entries: list[ScopeEntryOut]


class ScopeReplace(BaseModel):
    # One entry per string: an address, a CIDR block or a start-end range.
    entries: list[Annotated[str, StringConstraints(max_length=64)]] = Field(max_length=10_000)
    authorized: bool = False
    """The analyst's confirmation that they are authorized to scan these ranges."""
