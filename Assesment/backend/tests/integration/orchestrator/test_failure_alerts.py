"""A failed scheduled run and a dead-lettered message each raise one alert
(FR-HIS-7, NFR-REL-4)."""

import asyncio
from datetime import UTC, datetime
from ipaddress import IPv4Address
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.messages import AlertCreated
from ndm.db.models import (
    Alert,
    Scan,
    ScanPhase,
    ScanState,
    TracerouteRun,
    TracerouteState,
)
from ndm.orchestrator import phases
from ndm.orchestrator.main import handle_dead
from ndm.orchestrator.tick import Orchestrator, tick
from tests.integration.orchestrator.conftest import (
    Outbox,
    ScanFactory,
    run_to_end,
    scanner_reports,
    schedule_for,
)

WORLD = {"8.8.8.1": {"ping": True, "open_ports": [443], "closed": "reset"}}


async def alerts_of(db: AsyncSession) -> list[tuple[str, int | None, dict[str, Any], str]]:
    rows = await db.execute(
        select(Alert.type, Alert.project_id, Alert.link, Alert.summary).order_by(Alert.id)
    )
    return [tuple(row) for row in rows]


def created(outbox: Outbox) -> list[AlertCreated]:
    return [event for event in outbox.events if isinstance(event, AlertCreated)]


# --- Failed scheduled runs ---


@pytest.fixture(autouse=True)
def no_phases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(phases, "PHASES", {})


async def test_a_scheduled_run_that_fails_in_a_phase_raises_one_alert(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    async def broken(sessions: Any, scan_id: int) -> None:
        raise RuntimeError("the phase broke")

    phases.register(ScanPhase.ENRICHMENT, broken)
    scan = await make_scan(state=ScanState.QUEUED)
    schedule_id = await schedule_for(db_session, scan)
    await scanner_reports(orchestrator)
    await run_to_end(orchestrator, outbox, WORLD)
    await phases.run_pending(orchestrator.sessions, outbox.publish_events)
    assert (
        await db_session.get_one(Scan, scan.id, populate_existing=True)
    ).state is ScanState.FAILED

    await tick(orchestrator)
    await tick(orchestrator)  # a later tick raises no second alert

    assert await alerts_of(db_session) == [
        (
            "scheduled_run_failed",
            scan.project_id,
            {"scan_id": scan.id, "schedule_id": schedule_id},
            f"Nightly edge sweep: scheduled scan {scan.id} failed (enrichment_failed).",
        )
    ]
    [event] = created(outbox)
    assert (event.alert_type, event.project_id) == ("scheduled_run_failed", scan.project_id)


async def test_a_retried_scan_that_fails_again_is_not_alerted_twice(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    scan = await make_scan(state=ScanState.FAILED, phase=None, partial=True, chunks=0)
    await schedule_for(db_session, scan)
    await tick(orchestrator)
    scan.state, scan.failure_reason = ScanState.RUNNING, None
    await db_session.merge(scan)
    await db_session.commit()
    scan.state, scan.failure_reason = ScanState.FAILED, "classification_failed"
    await db_session.merge(scan)
    await db_session.commit()

    await tick(orchestrator)

    assert len(await alerts_of(db_session)) == 1


@pytest.mark.parametrize(
    ("state", "scheduled"),
    [
        (ScanState.FAILED, False),  # a manual scan
        (ScanState.CANCELLED, True),  # cancelled by the analyst is not a failure
        (ScanState.COMPLETED, True),
    ],
    ids=["manual-failed", "scheduled-cancelled", "scheduled-completed"],
)
async def test_other_ends_raise_no_failure_alert(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    orchestrator: Orchestrator,
    state: ScanState,
    scheduled: bool,
) -> None:
    scan = await make_scan(state=state, phase=None, partial=state is not ScanState.COMPLETED)
    if scheduled:
        await schedule_for(db_session, scan)

    await tick(orchestrator)

    assert await alerts_of(db_session) == []


async def test_a_deleted_scheduled_scan_raises_no_alert(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    scan = await make_scan(
        state=ScanState.FAILED, phase=None, partial=True, deleted_at=datetime.now(UTC)
    )
    await schedule_for(db_session, scan)

    await tick(orchestrator)

    assert await alerts_of(db_session) == []


# --- Dead-lettered messages ---


class Delivery:
    """A message taken from the dead-letter queue, without a broker."""

    def __init__(self, body: bytes, queue: str = "orchestrator.results") -> None:
        self.body = body
        self.headers = {"x-death": [{"queue": queue, "reason": "rejected", "count": 1}]}
        self.acked = False

    async def ack(self) -> None:
        self.acked = True

    async def nack(self, requeue: bool = True) -> None:
        raise AssertionError("the message was not processed")


async def take(orchestrator: Orchestrator, delivery: Delivery) -> None:
    await handle_dead(cast(Any, delivery), orchestrator, asyncio.Event())
    assert delivery.acked


async def test_a_dead_lettered_message_raises_one_alert_for_its_project(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan = await make_scan()

    await take(orchestrator, Delivery(b'{"type": "findings", "scan_id": %d}' % scan.id))

    assert await alerts_of(db_session) == [
        (
            "message_dead_lettered",
            scan.project_id,
            {"scan_id": scan.id},
            f"A message for scan {scan.id} could not be processed "
            "(rejected in orchestrator.results).",
        )
    ]
    [event] = created(outbox)
    assert event.alert_type == "message_dead_lettered"


async def test_a_dead_letter_that_fails_a_scheduled_run_raises_both_alerts(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    scan = await make_scan()
    await schedule_for(db_session, scan)

    await take(orchestrator, Delivery(b'{"type": "findings", "scan_id": %d}' % scan.id))
    await tick(orchestrator)

    assert [kind for kind, *_ in await alerts_of(db_session)] == [
        "message_dead_lettered",
        "scheduled_run_failed",
    ]


async def test_every_dead_lettered_message_raises_an_alert(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator
) -> None:
    """Also a message for a scan that has ended, and one that names nothing."""
    scan = await make_scan(state=ScanState.COMPLETED, phase=None)
    run = TracerouteRun(
        project_id=scan.project_id,
        attestation_id=scan.attestation_id,
        target=IPv4Address("8.8.8.1"),
        method="icmp",
        parameters={"port": None, "max_hops": 30, "probes_per_hop": 2, "timeout_ms": 1000},
        state=TracerouteState.RUNNING,
    )
    db_session.add(run)
    await db_session.commit()

    await take(orchestrator, Delivery(b'{"scan_id": %d}' % scan.id))
    await take(orchestrator, Delivery(b'{"run_id": %d}' % run.id, "scanner.test.traceroutes"))
    await take(orchestrator, Delivery(b"not json at all"))

    alerts = await alerts_of(db_session)
    assert [(kind, project, link) for kind, project, link, _ in alerts] == [
        ("message_dead_lettered", scan.project_id, {"scan_id": scan.id}),
        ("message_dead_lettered", scan.project_id, {"traceroute_run_id": run.id}),
        ("message_dead_lettered", None, {}),
    ]
    assert alerts[2][3] == (
        "A message for no known scan or traceroute could not be processed "
        "(rejected in orchestrator.results)."
    )
