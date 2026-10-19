from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from rss_reader.network import validate_url

JobStatus = Literal["queued", "running", "retrying", "successful", "partial", "failed", "blocked"]


class SourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(max_length=2048)
    period_minutes: int = Field(default=60, ge=1, le=525600)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        return validate_url(value)


class SourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    period_minutes: int = Field(ge=1, le=525600)
    enabled: bool


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    url: str
    period_minutes: int
    enabled: bool
    next_run_at: datetime
    last_checked_at: datetime | None
    last_success_at: datetime | None
    last_entry_id: str | None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source_id: int
    status: JobStatus
    trigger: str
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    next_attempt_at: datetime
    http_status: int | None
    found_count: int
    new_count: int
    duplicate_count: int
    invalid_count: int
    errors: list[dict[str, str]]


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class ArticleOut(BaseModel):
    id: int
    title: str
    link: str
    published_at: datetime | None
    discovered_at: datetime
    description: str
    tags: list[TagOut]
    source_ids: list[int]


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int


class Stats(BaseModel):
    sources: int
    enabled_sources: int
    jobs: int
    active_jobs: int
    articles: int
    error_jobs: int
    invalid_entries: int


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
