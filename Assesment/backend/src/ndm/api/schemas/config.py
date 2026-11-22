"""Models of the configuration endpoint: what the launch dialog and the map need to know."""

from pydantic import BaseModel


class Limit(BaseModel):
    min: int
    max: int
    default: int


class Limits(BaseModel):
    """Ceilings of the scan parameters. The operator's are set in the
    environment and cannot be changed here (FR-CFG-7)."""

    max_targets: int
    max_concurrent_scans: int
    pps: Limit
    timeout_ms: Limit
    retries: Limit
    hosts_in_parallel: Limit


class PortProfileOut(BaseModel):
    name: str
    ports: int


class ScannerOut(BaseModel):
    """What the scanner last reported about itself."""

    vantage_point: str
    alive: bool
    syn_capable: bool
    icmp_capable: bool


class TilesOut(BaseModel):
    """The map's tile source, as the operator configured it (FR-MAP-11)."""

    url_template: str
    """Raster tile URL with the placeholders ``{z}``, ``{x}`` and ``{y}``."""
    attribution: str
    """The attribution the provider requires. It is shown on the map (NFR-LIC-3)."""


class ConfigOut(BaseModel):
    limits: Limits
    port_profiles: list[PortProfileOut]
    scanner: ScannerOut | None
    """Null until a scanner has reported for the first time."""
    tiles: TilesOut | None
    """Null if no tile source is configured. The map then shows a notice and
    contacts no tile server: there is no default source (R6)."""
