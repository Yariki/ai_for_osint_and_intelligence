"""Traceroute dispatch (check 3.11): runs above the ceiling wait, and a
running scan does not delay a traceroute. The scope travels with the job and
is checked again before it is sent (FR-TRC-1)."""

from datetime import timedelta
from ipaddress import IPv4Address
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.messages import TracerouteMethod
from ndm.db.models import AuditEvent, Scan, ScanState, TracerouteRun, TracerouteState
from ndm.orchestrator.tick import Orchestrator, tick
from tests.integration.orchestrator.conftest import Outbox, ScanFactory, scanner_reports

PARAMETERS = {"port": None, "max_hops": 30, "probes_per_hop": 3, "timeout_ms": 1000}


async def runs(db: AsyncSession, scan: Scan, count: int, **values: Any) -> list[TracerouteRun]:
    """Waiting traceroutes in the project of ``scan``, to addresses of its scope."""
    created = [
        TracerouteRun(
            project_id=scan.project_id,
            attestation_id=scan.attestation_id,
            target=IPv4Address(f"8.8.8.{10 + index}"),
            method=values.get("method", "icmp"),
            parameters=values.get("parameters", PARAMETERS),
        )
        for index in range(count)
    ]
    db.add_all(created)
    await db.commit()
    return created


async def states(db: AsyncSession) -> list[TracerouteState]:
    await db.commit()
    return list(
        await db.scalars(
            select(TracerouteRun.state)
            .order_by(TracerouteRun.id)
            .execution_options(populate_existing=True)
        )
    )


@pytest.fixture
def ceiling_of_two(monkeypatch: pytest.MonkeyPatch, orchestrator: Orchestrator) -> Orchestrator:
    orchestrator.settings = orchestrator.settings.model_copy(
        update={"max_concurrent_traceroutes": 2}
    )
    return orchestrator


async def test_runs_above_the_ceiling_wait(
    db_session: AsyncSession, make_scan: ScanFactory, ceiling_of_two: Orchestrator, outbox: Outbox
) -> None:
    scan = await make_scan(state=ScanState.COMPLETED, phase=None)
    created = await runs(db_session, scan, 4)
    await scanner_reports(ceiling_of_two)

    await tick(ceiling_of_two)

    assert [job.run_id for job in outbox.traceroutes] == [created[0].id, created[1].id]
    assert await states(db_session) == [TracerouteState.RUNNING] * 2 + [TracerouteState.QUEUED] * 2

    # Nothing more while both are in flight.
    await tick(ceiling_of_two)
    assert len(outbox.traceroutes) == 2

    # One ends: the next one goes.
    first = await db_session.get_one(TracerouteRun, created[0].id)
    first.state = TracerouteState.COMPLETED
    await db_session.commit()
    await tick(ceiling_of_two)
    assert [job.run_id for job in outbox.traceroutes][-1] == created[2].id
    assert (await states(db_session)).count(TracerouteState.RUNNING) == 2


async def test_a_running_scan_does_not_delay_a_traceroute(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    # Every scan slot is taken, and more scans wait in the queue.
    ceiling = orchestrator.settings.max_concurrent_scans
    running = [await make_scan() for _ in range(ceiling)]
    await make_scan(state=ScanState.QUEUED)
    [created] = await runs(db_session, running[0], 1)
    await scanner_reports(orchestrator)

    await tick(orchestrator)

    assert [job.run_id for job in outbox.traceroutes] == [created.id]
    assert await states(db_session) == [TracerouteState.RUNNING]


async def test_the_job_carries_the_current_scope_and_the_parameters(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan = await make_scan(state=ScanState.COMPLETED, phase=None)
    tcp = {"port": 443, "max_hops": 20, "probes_per_hop": 2, "timeout_ms": 800}
    [created] = await runs(db_session, scan, 1, method="tcp", parameters=tcp)
    await scanner_reports(orchestrator)

    await tick(orchestrator)

    [job] = outbox.traceroutes
    assert (job.run_id, job.target, job.method) == (
        created.id,
        IPv4Address("8.8.8.10"),
        TracerouteMethod.TCP,
    )
    assert (job.port, job.max_hops, job.probes_per_hop, job.timeout_ms) == (443, 20, 2, 800)
    assert job.scope == [(IPv4Address("8.8.8.0"), IPv4Address("8.8.8.255"))]
    assert job.attestation_id == scan.attestation_id
    run = await db_session.get_one(TracerouteRun, created.id, populate_existing=True)
    assert (run.attempts, run.vantage_point_id is not None, run.dispatched_at is not None) == (
        1,
        True,
        True,
    )


async def test_nothing_is_dispatched_without_a_live_scanner(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan = await make_scan(state=ScanState.COMPLETED, phase=None)
    await runs(db_session, scan, 1)

    await tick(orchestrator)

    assert outbox.traceroutes == []
    assert await states(db_session) == [TracerouteState.QUEUED]


async def test_a_target_that_left_the_scope_is_not_sent(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan = await make_scan(state=ScanState.COMPLETED, phase=None)
    run = TracerouteRun(
        project_id=scan.project_id,
        attestation_id=scan.attestation_id,
        target=IPv4Address("9.9.9.9"),  # outside 8.8.8.0/24
        method="icmp",
        parameters=PARAMETERS,
    )
    db_session.add(run)
    await db_session.commit()
    await scanner_reports(orchestrator)

    await tick(orchestrator)

    assert outbox.traceroutes == []
    run = await db_session.get_one(TracerouteRun, run.id, populate_existing=True)
    assert (run.state, run.failure_reason) == (TracerouteState.FAILED, "scope_changed")
    entry = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "traceroute.rejected")
    )
    assert entry is not None and entry.details["point"] == "orchestrator"


async def test_a_lost_job_is_sent_again_and_given_up_after_three_attempts(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan = await make_scan(state=ScanState.COMPLETED, phase=None)
    [created] = await runs(db_session, scan, 1)
    await scanner_reports(orchestrator)

    async def an_hour_passes() -> None:
        """30 hops of 1 second and a margin: the lease is about 2.5 minutes."""
        run = await db_session.get_one(TracerouteRun, created.id, populate_existing=True)
        assert run.dispatched_at is not None
        run.dispatched_at -= timedelta(hours=1)
        await db_session.commit()
        await tick(orchestrator)

    await tick(orchestrator)
    await an_hour_passes()
    await an_hour_passes()
    assert len(outbox.traceroutes) == 3
    await an_hour_passes()

    run = await db_session.get_one(TracerouteRun, created.id, populate_existing=True)
    assert (run.state, run.failure_reason, run.attempts) == (
        TracerouteState.FAILED,
        "traceroute_lost",
        3,
    )
    assert len(outbox.traceroutes) == 3


async def test_a_run_within_its_lease_is_not_sent_again(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan = await make_scan(state=ScanState.COMPLETED, phase=None)
    await runs(db_session, scan, 1)
    await scanner_reports(orchestrator)

    for _ in range(3):
        await tick(orchestrator)

    assert len(outbox.traceroutes) == 1
