"""Metrics and the orchestrator's health check (NFR-OPS-3, without dataset age)."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.messages import Heartbeat
from ndm.db.models import ReferenceDataset, ScanState
from ndm.orchestrator import metrics as metrics_server
from ndm.orchestrator import vantage
from ndm.orchestrator.metrics import Metrics
from ndm.orchestrator.recording import finish_scan
from ndm.orchestrator.tick import Orchestrator
from tests.integration.orchestrator.conftest import ScanFactory


def samples(metrics: Metrics) -> dict[str, float]:
    """Every sample as 'name{labels}' -> value."""
    found = {}
    for line in metrics.render().decode().splitlines():
        if line and not line.startswith("#"):
            name, value = line.rsplit(" ", 1)
            found[name] = float(value)
    return found


async def test_probe_rate_and_scanner_counters_come_from_the_heartbeat(
    db_session: AsyncSession, orchestrator: Orchestrator
) -> None:
    heartbeat = Heartbeat(
        vantage_point="test",
        egress_address=None,
        syn_capable=True,
        icmp_capable=True,
        allow_non_public_targets=False,
        probes_sent=120_000,
        replies_received=830,
        receive_drops=2,
        current_rate_pps=4987.5,
    )
    await vantage.record_status(db_session, heartbeat, orchestrator.settings)
    await db_session.commit()
    metrics = Metrics()

    await metrics.refresh(db_session)

    found = samples(metrics)
    assert found['ndm_scanner_probe_rate_pps{vantage_point="test"}'] == 4987.5
    assert found['ndm_scanner_probes_sent{vantage_point="test"}'] == 120_000
    assert found['ndm_scanner_replies_received{vantage_point="test"}'] == 830
    assert found['ndm_scanner_receive_drops{vantage_point="test"}'] == 2
    assert 0 <= found['ndm_scanner_heartbeat_age_seconds{vantage_point="test"}'] < 30


async def test_age_of_each_loaded_dataset_is_reported(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    db_session.add_all(
        [
            ReferenceDataset(
                name="geolite2-city",
                version="2026-11-13",
                status="ok",
                loaded_at=now - timedelta(hours=30),
            ),
            # Never loaded: it has no age, and no sample.
            ReferenceDataset(name="rir-objects-arin"),
        ]
    )
    await db_session.commit()
    metrics = Metrics()

    await metrics.refresh(db_session)

    found = samples(metrics)
    age = found['ndm_dataset_age_seconds{dataset="geolite2-city"}']
    assert (
        timedelta(hours=30).total_seconds() <= age < timedelta(hours=30, minutes=1).total_seconds()
    )
    assert not [name for name in found if "rir-objects-arin" in name]


async def test_queue_depth_is_reported_per_queue(db_session: AsyncSession) -> None:
    async def depths() -> dict[str, int]:
        return {"scanner.test.chunks": 3, "orchestrator.results": 0, "ndm.dead": 1}

    metrics = Metrics()

    await metrics.refresh(db_session, depths)

    found = samples(metrics)
    assert found['ndm_queue_depth{queue="scanner.test.chunks"}'] == 3
    assert found['ndm_queue_depth{queue="orchestrator.results"}'] == 0
    assert found['ndm_queue_depth{queue="ndm.dead"}'] == 1


async def test_scan_outcomes_and_durations_are_counted_once(
    db_session: AsyncSession, make_scan: ScanFactory
) -> None:
    metrics = Metrics()
    started = datetime.now(UTC) - timedelta(seconds=90)
    completed = await make_scan(started_at=started)
    failed = await make_scan(started_at=started)
    await make_scan()  # still running
    await make_scan(state=ScanState.QUEUED)
    await finish_scan(db_session, completed, ScanState.COMPLETED)
    await finish_scan(db_session, failed, ScanState.FAILED, reason="scope_changed")
    await db_session.commit()

    await metrics.refresh(db_session)
    await metrics.refresh(db_session)  # a second refresh must not count them again

    found = samples(metrics)
    assert found['ndm_scan_outcomes_total{outcome="completed"}'] == 1
    assert found['ndm_scan_outcomes_total{outcome="failed"}'] == 1
    assert found['ndm_scan_duration_seconds_count{outcome="completed"}'] == 1
    assert 89 <= found['ndm_scan_duration_seconds_sum{outcome="completed"}'] <= 100
    # 90 seconds falls into the bucket "up to 120" and not into "up to 60".
    assert found['ndm_scan_duration_seconds_bucket{le="60.0",outcome="completed"}'] == 0
    assert found['ndm_scan_duration_seconds_bucket{le="120.0",outcome="completed"}'] == 1
    assert found['ndm_scans{state="running"}'] == 1
    assert found['ndm_scans{state="queued"}'] == 1
    assert found['ndm_scans{state="interrupted"}'] == 0


async def test_scans_that_ended_before_the_start_are_not_counted(
    db_session: AsyncSession, make_scan: ScanFactory
) -> None:
    earlier = await make_scan()
    await finish_scan(db_session, earlier, ScanState.COMPLETED)
    await db_session.commit()
    await asyncio.sleep(0.05)
    metrics = Metrics()  # the orchestrator restarts here

    await metrics.refresh(db_session)

    assert 'ndm_scan_outcomes_total{outcome="completed"}' not in samples(metrics)


async def test_metrics_and_health_are_served_over_http(db_session: AsyncSession) -> None:
    metrics = Metrics()
    await metrics.refresh(db_session)
    server = await metrics_server.serve(metrics, "127.0.0.1", 0, max_tick_age=30)
    port = server.sockets[0].getsockname()[1]
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            exposition = await client.get("/metrics")
            health = await client.get("/healthz")
            missing = await client.get("/anything-else")
            wrong_method = await client.post("/metrics")

            # No tick for longer than allowed: the health check fails.
            metrics.last_tick -= 60
            stalled = await client.get("/healthz")
    finally:
        server.close()
        await server.wait_closed()

    assert exposition.status_code == 200
    assert exposition.headers["content-type"].startswith("text/plain")
    for name in (
        "ndm_scanner_probe_rate_pps",
        "ndm_queue_depth",
        "ndm_scan_duration_seconds",
        "ndm_scan_outcomes",
    ):
        assert f"# TYPE {name}" in exposition.text
    assert (health.status_code, health.text) == (200, "ok\n")
    assert (missing.status_code, wrong_method.status_code) == (404, 405)
    assert (stalled.status_code, stalled.text) == (503, "stalled\n")
