"""Helpers for the scheduler tests: a project with a scope, and a schedule of it."""

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import Scan, ScanState, Schedule
from tests.integration.orchestrator.conftest import ScanFactory

PARAMETERS: dict[str, Any] = {
    "port_profile": "top-100",
    "custom_ports": None,
    "discovery": {"icmp_echo": True, "tcp_ports": [80, 443]},
    "scan_all_targets": False,
    "technique": "automatic",
    "pps": 1000,
    "timeout_ms": 1500,
    "retries": 1,
    "hosts_in_parallel": 64,
}


async def project_scan(make_scan: ScanFactory) -> Scan:
    """A completed manual scan: it makes a project with the scope 8.8.8.0/24."""
    return await make_scan(state=ScanState.COMPLETED, phase=None, chunks=0)


async def make_schedule(
    db: AsyncSession, project: Scan, next_run_at: datetime, **values: Any
) -> Schedule:
    schedule = Schedule(
        **{
            "project_id": project.project_id,
            "name": "Nightly edge sweep",
            "targets": ["8.8.8.1-8.8.8.4"],
            "parameters": PARAMETERS,
            "interval_seconds": 6 * 3600,
            "next_run_at": next_run_at,
        }
        | values
    )
    db.add(schedule)
    await db.commit()
    return schedule
