from pydantic import Field, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    database_url: str = "sqlite:///./data/rss.db"
    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")
    worker_concurrency: int = Field(default=1, ge=1, le=16)
    default_period_minutes: int = Field(default=60, ge=1, le=525600)
    scheduler_interval_seconds: int = Field(default=30, ge=5)
    request_delay_seconds: float = Field(default=3, ge=0.1)
    request_jitter_seconds: float = Field(default=1, ge=0)
    request_timeout_seconds: float = Field(default=20, gt=0, le=60)
    max_response_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_base_seconds: int = Field(default=30, ge=1)
    robots_cache_seconds: int = Field(default=86400, ge=60, le=86400)
    job_lease_seconds: int = Field(default=300, ge=120)
    dispatch_lease_seconds: int = Field(default=120, ge=30)
    user_agent: str = Field(default="RespectfulRSSReader/1.0", min_length=3, max_length=200)
    proxy_url: str | None = None

    @field_validator("proxy_url", mode="before")
    @classmethod
    def empty_proxy(cls, value: object) -> object:
        return value or None

    @field_validator("database_url")
    @classmethod
    def sqlite_only(cls, value: str) -> str:
        if not value.startswith("sqlite:///") or value.endswith(":memory:"):
            raise ValueError("Use a file-backed sqlite:/// database URL")
        return value

    @field_validator("user_agent")
    @classmethod
    def valid_agent(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("User agent must not contain control characters")
        return value
