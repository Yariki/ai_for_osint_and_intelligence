"""Check 4.9: the automatic diff of a scheduled scan.

The baseline is the previous completed, non-partial run of the same schedule;
a diff with changes is stored and raises one alert that links to it, and the
link still resolves after later runs; an empty diff raises none. A manual
scan runs no diff phase.
"""

from datetime import UTC, datetime
from ipaddress import IPv4Address
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.messages import AlertCreated, ScanProgress
from ndm.db.models import Alert, ChangeEvent, Scan, ScanDiff, ScanPhase, ScanState, Schedule
from ndm.orchestrator import diff_phase, phases
from ndm.orchestrator.tick import Orchestrator
from tests.integration.orchestrator.conftest import (
    TARGETS,
    Outbox,
    ScanFactory,
    run_to_end,
    scanner_reports,
)

BEFORE = {
    "8.8.8.1": {"ping": True, "open_ports": [22, 443], "closed": "reset"},
    "8.8.8.2": {"ping": True, "open_ports": [443], "closed": "reset"},
}
AFTER = {
    # 8.8.8.1: 22 closed, 80 opened. 8.8.8.3 appeared.
    "8.8.8.1": {"ping": True, "open_ports": [80, 443], "closed": "reset"},
    "8.8.8.2": {"ping": True, "open_ports": [443], "closed": "reset"},
    "8.8.8.3": {"ping": True, "open_ports": [], "closed": "reset"},
}


@pytest.fixture(autouse=True)
def diff_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the diff phase is registered: enrichment and classification have tests of their own."""
    monkeypatch.setattr(phases, "PHASES", {})
    phases.register(ScanPhase.DIFF, diff_phase.run_phase)


class Runs:
    """Scans of one project, scheduled by one schedule unless told otherwise."""

    def __init__(
        self, db: AsyncSession, orchestrator: Orchestrator, outbox: Outbox, make_scan: ScanFactory
    ) -> None:
        self.db, self.orchestrator, self.outbox, self.make_scan = (
            db,
            orchestrator,
            outbox,
            make_scan,
        )
        self.first: Scan | None = None
        self.schedule_id: int | None = None

    async def schedule(self, name: str = "Nightly edge sweep") -> int:
        assert self.first is not None
        schedule = Schedule(
            project_id=self.first.project_id,
            name=name,
            targets=["8.8.8.1-8.8.8.4"],
            parameters={},
            interval_seconds=86400,
            next_run_at=datetime.now(UTC),
        )
        self.db.add(schedule)
        await self.db.commit()
        return schedule.id

    async def create(self, *, manual: bool = False, **values: Any) -> Scan:
        if self.first is None:
            # The first scan makes the project; the schedule belongs to it.
            self.first = await self.make_scan(**values)
            self.schedule_id = await self.schedule()
            if not manual:
                self.first.schedule_id = self.schedule_id
                await self.db.merge(self.first)
                await self.db.commit()
            return self.first
        values = {"schedule_id": None if manual else self.schedule_id} | values
        return await self.make_scan(
            project_id=self.first.project_id, attestation_id=self.first.attestation_id, **values
        )

    async def run(self, world: dict[str, Any], *, manual: bool = False) -> Scan:
        """A scan that is queued, probed and taken through its phases."""
        self.outbox.chunks.clear()
        scan = await self.create(state=ScanState.QUEUED, manual=manual)
        await scanner_reports(self.orchestrator)
        await run_to_end(self.orchestrator, self.outbox, world)
        while await phases.run_pending(self.orchestrator.sessions, self.outbox.publish_events):
            pass
        done = await self.db.get_one(Scan, scan.id, populate_existing=True)
        assert done.state is ScanState.COMPLETED
        return done


@pytest.fixture
def runs(
    db_session: AsyncSession, orchestrator: Orchestrator, outbox: Outbox, make_scan: ScanFactory
) -> Runs:
    return Runs(db_session, orchestrator, outbox, make_scan)


async def alerts_of(db: AsyncSession) -> list[Alert]:
    return list(
        await db.scalars(select(Alert).order_by(Alert.id).execution_options(populate_existing=True))
    )


async def diffs_of(db: AsyncSession) -> list[ScanDiff]:
    return list(await db.scalars(select(ScanDiff).order_by(ScanDiff.id)))


async def events_of(db: AsyncSession, diff_id: int) -> list[tuple[str, str, int | None]]:
    rows = await db.execute(
        select(ChangeEvent.address, ChangeEvent.kind, ChangeEvent.port)
        .where(ChangeEvent.diff_id == diff_id)
        .order_by(ChangeEvent.id)
    )
    return [(str(address), kind, port) for address, kind, port in rows]


async def test_the_first_run_of_a_schedule_has_nothing_to_compare(
    db_session: AsyncSession, runs: Runs
) -> None:
    await runs.run(BEFORE)

    assert await diffs_of(db_session) == []
    assert await alerts_of(db_session) == []


async def test_changes_are_stored_and_raise_one_alert_that_links_to_them(
    db_session: AsyncSession, runs: Runs, outbox: Outbox
) -> None:
    first = await runs.run(BEFORE)
    second = await runs.run(AFTER)

    [diff] = await diffs_of(db_session)
    assert (diff.base_scan_id, diff.target_scan_id) == (first.id, second.id)
    assert diff.summary["host_appeared"] == 1
    assert diff.summary["port_opened"] == diff.summary["port_closed"] == 1
    assert diff.not_comparable == []
    assert sorted(await events_of(db_session, diff.id)) == [
        ("8.8.8.1", "port_closed", 22),
        ("8.8.8.1", "port_opened", 80),
        ("8.8.8.3", "host_appeared", None),
    ]

    [alert] = await alerts_of(db_session)
    assert alert.type == "changes_detected"
    assert alert.project_id == second.project_id
    assert alert.link == {"diff_id": diff.id, "scan_id": second.id, "base_scan_id": first.id}
    assert alert.summary.startswith(f"Nightly edge sweep: 3 changes since scan {first.id}")
    assert alert.read_at is None
    # The UI hears of it, after the commit.
    [created] = [event for event in outbox.events if isinstance(event, AlertCreated)]
    assert (created.alert_id, created.alert_type) == (alert.id, "changes_detected")


async def test_an_empty_diff_raises_no_alert_and_earlier_links_still_resolve(
    db_session: AsyncSession, runs: Runs
) -> None:
    await runs.run(BEFORE)
    await runs.run(AFTER)
    [alert] = await alerts_of(db_session)
    linked = await events_of(db_session, alert.link["diff_id"])

    third = await runs.run(AFTER)
    fourth = await runs.run(AFTER)

    diffs = await diffs_of(db_session)
    assert [d.target_scan_id for d in diffs[1:]] == [third.id, fourth.id]
    assert [await events_of(db_session, d.id) for d in diffs[1:]] == [[], []]
    assert diffs[2].summary["compared_addresses"] == len(TARGETS)
    # Still one alert, and it points at what was detected at the time.
    assert [a.id for a in await alerts_of(db_session)] == [alert.id]
    assert await events_of(db_session, alert.link["diff_id"]) == linked


@pytest.mark.parametrize(
    ("state", "partial"),
    [
        (ScanState.CANCELLED, True),
        (ScanState.FAILED, True),
        (ScanState.COMPLETED, True),  # completed, but a phase was skipped or a chunk lost
    ],
    ids=["cancelled", "failed", "partial"],
)
async def test_runs_that_did_not_complete_in_full_are_passed_over(
    db_session: AsyncSession, runs: Runs, state: ScanState, partial: bool
) -> None:
    first = await runs.run(BEFORE)
    passed_over = await runs.create(state=state, phase=None, partial=partial, chunks=0)
    # A complete scan of the same project that is not a run of this schedule.
    other = await runs.schedule("Weekly")
    await runs.create(state=ScanState.COMPLETED, phase=None, chunks=0, schedule_id=other)
    manual = await runs.create(state=ScanState.COMPLETED, phase=None, chunks=0, manual=True)
    assert manual.schedule_id is None

    latest = await runs.run(AFTER)

    assert (await diff_phase.baseline(db_session, latest)).id == first.id  # type: ignore[union-attr]
    [diff] = await diffs_of(db_session)
    assert (diff.base_scan_id, diff.target_scan_id) == (first.id, latest.id)
    assert passed_over.id not in (diff.base_scan_id, diff.target_scan_id)


async def test_a_deleted_run_is_not_a_baseline(db_session: AsyncSession, runs: Runs) -> None:
    first = await runs.run(BEFORE)
    deleted = await runs.run(AFTER)
    deleted.deleted_at = datetime.now(UTC)
    await db_session.merge(deleted)
    await db_session.commit()

    latest = await runs.run(AFTER)

    diff = (await diffs_of(db_session))[-1]
    assert (diff.base_scan_id, diff.target_scan_id) == (first.id, latest.id)


async def test_a_manual_scan_runs_no_diff_phase(
    db_session: AsyncSession, runs: Runs, outbox: Outbox
) -> None:
    await runs.run(BEFORE, manual=True)
    await runs.run(AFTER, manual=True)

    phases_seen = {event.phase for event in outbox.events if isinstance(event, ScanProgress)}
    assert "diff" not in phases_seen
    assert "port_scan" in phases_seen
    assert await diffs_of(db_session) == []
    assert await alerts_of(db_session) == []


async def test_the_phase_run_twice_stores_one_diff_and_one_alert(
    db_session: AsyncSession, runs: Runs, orchestrator: Orchestrator
) -> None:
    await runs.run(BEFORE)
    second = await runs.run(AFTER)

    # As if the orchestrator stopped after the phase committed and before the
    # scan moved on: the phase runs again on restart.
    assert await diff_phase.run_phase(orchestrator.sessions, second.id) == []

    assert len(await diffs_of(db_session)) == 1
    assert len(await alerts_of(db_session)) == 1
    assert await db_session.scalar(select(func.count()).select_from(ChangeEvent)) == 3


async def test_the_diff_reports_addresses_only_one_run_targeted(
    db_session: AsyncSession, runs: Runs
) -> None:
    await runs.run(BEFORE)
    # The schedule's targets were edited between runs.
    runs.outbox.chunks.clear()
    narrower = await runs.create(state=ScanState.QUEUED, targets=TARGETS[:3])
    await scanner_reports(runs.orchestrator)
    await run_to_end(runs.orchestrator, runs.outbox, BEFORE)
    while await phases.run_pending(runs.orchestrator.sessions, runs.outbox.publish_events):
        pass

    [diff] = await diffs_of(db_session)
    assert diff.target_scan_id == narrower.id
    assert diff.not_comparable == [IPv4Address("8.8.8.4")]
    assert diff.summary["not_comparable"] == 1
    assert await alerts_of(db_session) == []
