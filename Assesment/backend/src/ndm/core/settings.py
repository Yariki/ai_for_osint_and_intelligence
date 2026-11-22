"""Settings read from ``NDM_`` environment variables.

The environment is the only source: no ``.env`` file, no secrets directory and
no constructor arguments. This is what makes the test switch "settable through
the environment only" (NFR-SEC-6).

A variable that is set to the empty string counts as not set. Compose passes
every optional variable, and passes the ones the operator left out as empty.
"""

from functools import lru_cache
from ipaddress import IPv4Address
from pathlib import Path
from typing import Annotated, Self
from urllib.parse import unquote, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# Registries whose detailed objects can be downloaded without a login
# (docs/datasets/registry-sources.md).
DOWNLOADABLE_REGISTRIES = ("ripe", "apnic", "afrinic")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NDM_", extra="ignore", env_ignore_empty=True)

    # Database. Both URLs are optional because not every process has both:
    # only the migration job gets the owner's URL, and the scanner gets none.
    # A process that needs one and does not find it fails when it connects.
    database_url: SecretStr | None = None
    """URL of the application role. This role cannot alter the audit log."""
    migration_database_url: SecretStr | None = None
    """URL of the role that owns the schema and runs the migrations."""
    app_db_role: str = Field(default="ndm_app", pattern=r"^[a-z_][a-z0-9_]*$")
    """Name of the application role. Migrations grant and revoke privileges for it."""

    session_idle_timeout_seconds: int = Field(default=1800, gt=0)

    project_scope_ceiling: int = Field(default=65_536, gt=0)
    """Largest number of addresses one project scope may hold (A1)."""

    # Comma-separated in the environment. Kept as raw strings: parsing needs
    # ndm.core.scope and is done by each process at start-up.
    never_scan: Annotated[list[str], NoDecode] = []

    allow_non_public_targets: bool = False
    """Test switch (NFR-SEC-6). Off unless set in the environment."""

    # Broker. The scanner reaches it on the host's loopback address.
    broker_url: SecretStr | None = None

    # Operator ceilings (FR-CFG-7). Not editable in the application. The API,
    # the orchestrator and the scanner each read them from their own environment.
    global_rate_pps: int = Field(default=5000, gt=0)
    """Packets per second across all running scans."""
    max_concurrent_scans: int = Field(default=3, gt=0)
    max_concurrent_traceroutes: int = Field(default=5, gt=0, le=50)
    """Traceroutes that run at the same time. Further ones wait."""

    min_schedule_interval_seconds: int = Field(default=3600, ge=60)
    """Shortest time a schedule may leave between two runs (FR-SCH-6, FR-CFG-7)."""

    classification_threshold: float = Field(default=0.5, ge=0, le=1)
    """Below this confidence a host's device type is ``unknown`` (FR-CLS-3)."""

    # Scanner.
    vantage_point: str = Field(default="default", pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    """Name of the location scans are made from. Part of the scanner's queue names."""
    egress_address: IPv4Address | None = None
    """The public address probes are sent from, for the record on each scan."""
    source_port_range: str = Field(default="61000-61999", pattern=r"^[0-9]{4,5}-[0-9]{4,5}$")
    """Source ports of SYN probes, as low-high. Must lie outside the kernel's ephemeral range."""

    # Enrichment. Read by the orchestrator only; no other process gets these.
    reference_data_path: Path = Path("/var/lib/ndm/reference")
    """Where downloaded reference datasets are kept. Only the orchestrator mounts it."""
    maxmind_account_id: str | None = Field(default=None, pattern=r"^[0-9]+$")
    maxmind_license_key: SecretStr | None = None
    """The operator's own GeoLite2 licence key (NFR-LIC-1). Without it no GeoLite2 data loads."""
    geolite_update_interval_hours: int = Field(default=24, gt=0)
    rir_update_interval_hours: int = Field(default=168, gt=0)
    # Comma-separated in the environment. Empty by default: the operator names
    # the registries whose terms the installation meets.
    rir_object_registries: Annotated[list[str], NoDecode] = []
    """Registries whose detailed objects are downloaded: any of ripe, apnic, afrinic."""
    rir_files_path: Path | None = None
    """Directory with operator-supplied bulk files of ARIN and LACNIC."""
    ptr_enabled: bool = True
    """Reverse DNS sends target addresses to the resolver (FR-ENR-4). False sends no query."""
    ptr_resolver: str | None = Field(default=None, pattern=r"^[0-9.]+(:[0-9]{1,5})?$")
    """Resolver for reverse DNS, as address or address:port. Unset: the system's resolver."""

    # Map. There is no default tile source: the operator chooses a provider
    # whose usage policy permits the use (R6, NFR-LIC-3).
    tile_url_template: str | None = None
    """Raster tile URL with the placeholders {z}, {x} and {y}."""
    tile_attribution: str | None = None
    """The attribution the tile provider requires. Shown on the map."""

    log_level: str = "INFO"

    @field_validator("never_scan", "rir_object_registries", mode="before")
    @classmethod
    def _split_list(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("rir_object_registries")
    @classmethod
    def _check_registries(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(DOWNLOADABLE_REGISTRIES))
        if unknown:
            raise ValueError(
                f"not a registry with downloadable objects: {', '.join(unknown)}; "
                f"use any of {', '.join(DOWNLOADABLE_REGISTRIES)}"
            )
        return sorted(set(value))

    @field_validator("ptr_resolver")
    @classmethod
    def _check_resolver(cls, value: str | None) -> str | None:
        if value is not None:
            address, _, port = value.partition(":")
            IPv4Address(address)  # raises ValueError for anything else
            if port and not 1 <= int(port) <= 65535:
                raise ValueError("the resolver port must be within 1-65535")
        return value

    @field_validator("tile_url_template")
    @classmethod
    def _check_tile_url(cls, value: str | None) -> str | None:
        if value is not None:
            if not value.startswith(("https://", "http://")):
                raise ValueError("the tile URL must start with https:// or http://")
            if any(placeholder not in value for placeholder in ("{z}", "{x}", "{y}")):
                raise ValueError("the tile URL must contain {z}, {x} and {y}")
        return value

    @model_validator(mode="after")
    def _check_pairs(self) -> Self:
        if (self.maxmind_account_id is None) != (self.maxmind_license_key is None):
            raise ValueError("set the MaxMind account ID and the licence key together")
        if self.tile_url_template is not None and self.tile_attribution is None:
            raise ValueError("a tile source needs the attribution its provider requires")
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (env_settings,)

    @field_validator("source_port_range")
    @classmethod
    def _check_source_ports(cls, value: str) -> str:
        low, high = (int(part) for part in value.split("-"))
        if not 1024 <= low <= high <= 65535:
            raise ValueError("the source-port range must be low-high within 1024-65535")
        return value

    @property
    def source_ports(self) -> tuple[int, int]:
        low, high = (int(part) for part in self.source_port_range.split("-"))
        return low, high

    @property
    def ptr_resolver_address(self) -> tuple[str, int] | None:
        """The configured resolver as (address, port), or None for the system's resolver."""
        if self.ptr_resolver is None:
            return None
        address, _, port = self.ptr_resolver.partition(":")
        return address, int(port or 53)

    def secret_values(self) -> list[str]:
        """Every configured secret, for redaction at the logging boundary.

        A secret is any ``SecretStr`` field. For a URL the password inside it is
        a secret of its own, because drivers report it apart from the URL.
        """
        values: list[str] = []
        for name in type(self).model_fields:
            field_value = getattr(self, name)
            if not isinstance(field_value, SecretStr):
                continue
            secret = field_value.get_secret_value()
            values.append(secret)
            try:
                password = urlsplit(secret).password
            except ValueError:
                password = None
            if password:
                values.extend({password, unquote(password)})
        return [value for value in values if value]


@lru_cache
def get_settings() -> Settings:
    return Settings()
