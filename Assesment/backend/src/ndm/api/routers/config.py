"""Configuration the interface needs: parameter ceilings, scanner capabilities and
the map's tile source."""

from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import select

from ndm.api.deps import DbDep, SettingsDep
from ndm.api.schemas import scans as limits
from ndm.api.schemas.config import (
    ConfigOut,
    Limit,
    Limits,
    PortProfileOut,
    ScannerOut,
    TilesOut,
)
from ndm.core.ports import PortProfile, profile_ports
from ndm.db.models import VantagePoint
from ndm.orchestrator.vantage import is_alive

router = APIRouter(prefix="/config", tags=["config"])


@router.get("")
async def get_config(db: DbDep, settings: SettingsDep) -> ConfigOut:
    point = await db.scalar(select(VantagePoint).where(VantagePoint.name == settings.vantage_point))
    scanner = None
    if point is not None:
        scanner = ScannerOut(
            vantage_point=point.name,
            alive=is_alive(point, datetime.now(UTC)),
            syn_capable=point.capabilities.get("syn") is True,
            icmp_capable=point.capabilities.get("icmp") is True,
        )
    tiles = None
    if settings.tile_url_template is not None and settings.tile_attribution is not None:
        tiles = TilesOut(
            url_template=settings.tile_url_template, attribution=settings.tile_attribution
        )
    ceiling = settings.global_rate_pps
    return ConfigOut(
        limits=Limits(
            max_targets=limits.MAX_TARGETS,
            max_concurrent_scans=settings.max_concurrent_scans,
            pps=Limit(min=1, max=ceiling, default=min(limits.DEFAULT_PPS, ceiling)),
            timeout_ms=Limit(
                min=limits.TIMEOUT_MS[0], max=limits.TIMEOUT_MS[1], default=limits.TIMEOUT_MS[2]
            ),
            retries=Limit(min=limits.RETRIES[0], max=limits.RETRIES[1], default=limits.RETRIES[2]),
            hosts_in_parallel=Limit(
                min=limits.HOSTS_IN_PARALLEL[0],
                max=limits.HOSTS_IN_PARALLEL[1],
                default=limits.HOSTS_IN_PARALLEL[2],
            ),
        ),
        port_profiles=[
            PortProfileOut(name=profile.value, ports=len(profile_ports(profile)))
            for profile in PortProfile
        ],
        scanner=scanner,
        tiles=tiles,
    )
