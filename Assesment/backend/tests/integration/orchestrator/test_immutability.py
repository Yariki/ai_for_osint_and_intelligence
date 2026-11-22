"""Check 1B.5: immutability (FR-SCN-9).

Findings that arrive after a scan reached a final state change nothing.
Neither do findings for a scan that is not probing, or for an address that is
not a target of the scan.
"""

from datetime import UTC, datetime
from ipaddress import IPv4Address

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.messages import ChunkRejected, Evidence, Findings, HostFinding
from ndm.db.models import AuditEvent, Host, PortObservation, ScanPhase, ScanState
from ndm.orchestrator.recording import finish_scan, record_result
from tests.integration.orchestrator.conftest import ScanFactory, snapshot
from tests.tools.stub_scanner import load_recorded_scan

RECORDED = load_recorded_scan()

NOT_PROBING = [
    (ScanState.COMPLETED, None),
    (ScanState.CANCELLED, None),
    (ScanState.FAILED, None),
    # Probing is over; the phases that follow read stored data only.
    (ScanState.RUNNING, ScanPhase.ENRICHMENT),
    (ScanState.RUNNING, ScanPhase.CLASSIFICATION),
    (ScanState.RUNNING, ScanPhase.DIFF),
    # Not started yet.
    (ScanState.QUEUED, None),
]


@pytest.mark.parametrize(("state", "phase"), NOT_PROBING)
async def test_results_for_a_scan_that_is_not_probing_change_nothing(
    db_session: AsyncSession, make_scan: ScanFactory, state: ScanState, phase: ScanPhase | None
) -> None:
    scan = await make_scan(state=state, phase=phase)
    before = await snapshot(db_session, scan)
    late = [*RECORDED.for_scan(scan.id), ChunkRejected(scan_id=scan.id, chunk_seq=1, reason="late")]

    for result in late:
        assert await record_result(db_session, result) == []
        await db_session.commit()

    assert await snapshot(db_session, scan) == before
    assert await db_session.scalar(select(func.count()).select_from(PortObservation)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Host)) == 0
    assert await db_session.scalar(select(func.count()).select_from(AuditEvent)) == 0


async def test_completed_scan_keeps_exactly_what_it_had(
    db_session: AsyncSession, make_scan: ScanFactory
) -> None:
    scan = await make_scan()
    results = RECORDED.for_scan(scan.id)
    for result in results:
        await record_result(db_session, result)
    await finish_scan(
        db_session, await db_session.get_one(type(scan), scan.id), ScanState.COMPLETED
    )
    completed = await snapshot(db_session, scan)
    assert completed["scan"][:3] == (ScanState.COMPLETED, None, False)

    # The whole sequence arrives again, and a finding that would add a port and a host.
    extra = Findings(
        scan_id=scan.id,
        chunk_seq=1,
        hosts=[
            HostFinding(
                address=IPv4Address("8.8.8.4"),
                evidence={
                    "tcp:8080": Evidence(at=datetime(2026, 11, 15, 11, tzinfo=UTC), rtt_ms=1.0)
                },
                open_ports=[8080],
            ),
            HostFinding(address=IPv4Address("8.8.8.1"), open_ports=[8080]),
        ],
    )
    for result in [*results, extra]:
        assert await record_result(db_session, result) == []
        await db_session.commit()

    assert await snapshot(db_session, scan) == completed


async def test_deleted_scan_accepts_nothing(
    db_session: AsyncSession, make_scan: ScanFactory
) -> None:
    scan = await make_scan(deleted_at=func.now())
    before = await snapshot(db_session, scan)

    for result in RECORDED.for_scan(scan.id):
        assert await record_result(db_session, result) == []

    assert await snapshot(db_session, scan) == before


async def test_unknown_scan_is_ignored(db_session: AsyncSession) -> None:
    for result in RECORDED.for_scan(424242):
        assert await record_result(db_session, result) == []


async def test_finding_for_an_address_that_is_not_a_target_is_discarded(
    db_session: AsyncSession, make_scan: ScanFactory
) -> None:
    scan = await make_scan()
    before = await snapshot(db_session, scan)
    stranger = Findings(
        scan_id=scan.id,
        chunk_seq=1,
        hosts=[
            HostFinding(
                address=IPv4Address("9.9.9.9"),
                evidence={"icmp": Evidence(at=datetime(2026, 11, 15, 10, tzinfo=UTC), rtt_ms=1.0)},
                open_ports=[443],
            )
        ],
    )

    assert await record_result(db_session, stranger) == []
    await db_session.commit()

    assert await snapshot(db_session, scan) == before
    assert await db_session.scalar(select(func.count()).select_from(Host)) == 0


async def test_results_of_one_scan_do_not_touch_another(
    db_session: AsyncSession, make_scan: ScanFactory
) -> None:
    finished = await make_scan(state=ScanState.COMPLETED, phase=None)
    running = await make_scan()

    async def of_finished_scan() -> dict[str, object]:
        stored = await snapshot(db_session, finished)
        del stored["hosts"]  # the host registry is global, not part of a scan
        return stored

    before = await of_finished_scan()

    for result in RECORDED.for_scan(running.id):
        await record_result(db_session, result)
        await db_session.commit()

    # Both scans target the same addresses; only the running one changed.
    assert await of_finished_scan() == before
    assert (await snapshot(db_session, running))["ports"] != []
