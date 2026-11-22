"""Check 1B.4: idempotent recording (NFR-REL-1).

Delivering every message of the recorded scan twice, and out of order, leaves
the database identical to single in-order delivery.
"""

import random
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.messages import ChunkDone, ChunkRejected, Findings, HostsChanged, ScanProgress
from ndm.db.models import AuditEvent, Scan, ScanChunk, ScanState
from ndm.orchestrator.recording import Result, record_result, sum_chunk_counts
from tests.integration.orchestrator.conftest import ScanFactory, snapshot
from tests.tools.stub_scanner import load_recorded_scan

RECORDED = load_recorded_scan()

Delivery = Callable[[list[Result]], list[Result]]


async def deliver(db: AsyncSession, results: Sequence[Result]) -> list[Any]:
    """Hand messages to the handlers one at a time, each in its own transaction."""
    events: list[Any] = []
    for result in results:
        events += await record_result(db, result)
        await db.commit()
    return events


async def recorded_in_order(db: AsyncSession, make_scan: ScanFactory) -> dict[str, Any]:
    scan = await make_scan()
    await deliver(db, RECORDED.for_scan(scan.id))
    await sum_chunk_counts(db, scan.id)
    return await snapshot(db, scan)


# --- What single in-order delivery stores ---


async def test_recorded_scan_is_stored(db_session: AsyncSession, make_scan: ScanFactory) -> None:
    stored = await recorded_in_order(db_session, make_scan)

    reachable = {address: row[0] for address, row in stored["observations"].items()}
    assert reachable == {"8.8.8.1": True, "8.8.8.2": True, "8.8.8.3": True, "8.8.8.4": False}
    assert stored["ports"] == [("8.8.8.1", 22), ("8.8.8.1", 443), ("8.8.8.2", 443)]
    # Evidence is kept per probe, with the time and the round-trip time (FR-SCN-1).
    evidence = stored["observations"]["8.8.8.1"][1]
    assert sorted(evidence) == ["icmp", "tcp:22", "tcp:443", "tcp:80"]
    assert evidence["icmp"].keys() == {"at", "rtt_ms"}
    assert stored["observations"]["8.8.8.4"][1] == {}
    # Closed and filtered counts, summed over both port-scan chunks (FR-SCN-2).
    counts = {address: row[2:] for address, row in stored["observations"].items()}
    assert counts == {"8.8.8.1": (2, 0), "8.8.8.2": (0, 3), "8.8.8.3": (4, 0), "8.8.8.4": (0, 0)}
    assert {seq: state.value for seq, (state, _) in stored["chunks"].items()} == {
        0: "done",
        1: "done",
        2: "done",
    }
    assert stored["scan"][4] == {
        "chunks_total": 3,
        "chunks_done": 3,
        "probes_sent": 16,
        "receive_drops": 0,
    }
    # The hosts that answered are in the global registry and in the project.
    assert sorted(stored["hosts"]) == ["8.8.8.1", "8.8.8.2", "8.8.8.3"]
    assert all(is_public for _, _, is_public in stored["hosts"].values())
    assert sorted(stored["project_hosts"]) == ["8.8.8.1", "8.8.8.2", "8.8.8.3"]
    assert all(latest for _, _, latest in stored["project_hosts"].values())


# --- The same database, however the messages arrive ---


def twice(results: list[Result]) -> list[Result]:
    return [copy for result in results for copy in (result, result)]


def shuffled(results: list[Result], seed: int) -> list[Result]:
    mixed = list(results)
    random.Random(seed).shuffle(mixed)
    return mixed


def shuffled_twice(seed: int) -> Delivery:
    return lambda results: shuffled(twice(results), seed)


DELIVERIES: dict[str, Delivery] = {
    "twice-in-order": twice,
    "whole-sequence-twice": lambda results: results + results,
    "reversed": lambda results: results[::-1],
    "reversed-twice": lambda results: twice(results[::-1]),
    "completions-before-findings": lambda results: sorted(
        results, key=lambda result: isinstance(result, Findings)
    ),
    **{f"shuffled-twice-seed-{seed}": shuffled_twice(seed) for seed in range(1, 9)},
}


@pytest.mark.parametrize("delivery", DELIVERIES)
async def test_duplicated_and_reordered_delivery_gives_the_same_database(
    db_session: AsyncSession, make_scan: ScanFactory, delivery: str
) -> None:
    expected = await recorded_in_order(db_session, make_scan)

    scan = await make_scan()
    await deliver(db_session, DELIVERIES[delivery](RECORDED.for_scan(scan.id)))
    await sum_chunk_counts(db_session, scan.id)
    # Summing again must not add the counts a second time.
    await sum_chunk_counts(db_session, scan.id)

    assert await snapshot(db_session, scan) == expected


async def test_reprobed_chunk_keeps_the_earliest_evidence(
    db_session: AsyncSession, make_scan: ScanFactory
) -> None:
    """After a scanner crash a chunk is probed again: same findings, later timestamps."""
    expected = await recorded_in_order(db_session, make_scan)
    scan = await make_scan()
    results = RECORDED.for_scan(scan.id)
    again = []
    for result in results:
        if isinstance(result, Findings) and result.chunk_seq == 1:
            data = result.model_dump(mode="json")
            for host in data["hosts"]:
                for reply in host["evidence"].values():
                    reply["at"] = "2026-11-15T10:09:00Z"
            again.append(Findings.model_validate(data))

    await deliver(db_session, [*again, *results, *again])
    await sum_chunk_counts(db_session, scan.id)

    stored = await snapshot(db_session, scan)
    # The observations are those of the uninterrupted run: no duplicate, nothing missing.
    for part in ("scan", "observations", "ports", "chunks"):
        assert stored[part] == expected[part]
    # The hosts were seen again later, and that is all that moved.
    later = datetime(2026, 11, 15, 10, 9, tzinfo=UTC)
    for address in ("8.8.8.1", "8.8.8.2"):
        first_seen, last_seen, _ = stored["hosts"][address]
        assert (first_seen, last_seen) == (expected["hosts"][address][0], later)
    assert stored["hosts"]["8.8.8.3"] == expected["hosts"]["8.8.8.3"]


# --- Events returned for publishing after the commit ---


async def test_events_describe_what_is_stored(
    db_session: AsyncSession, make_scan: ScanFactory
) -> None:
    scan = await make_scan()
    results = RECORDED.for_scan(scan.id)

    events = await deliver(db_session, results[:5])  # discovery, then the first port-scan chunk

    kinds = [type(event) for event in events]
    assert kinds == [HostsChanged, HostsChanged, ScanProgress, HostsChanged, ScanProgress]
    assert all((e.scan_id, e.project_id) == (scan.id, scan.project_id) for e in events)
    # Slim rows: address, reachable, number of open ports.
    last_hosts = events[3]
    assert [(str(h.address), h.reachable, h.open_port_count) for h in last_hosts.hosts] == [
        ("8.8.8.1", True, 2),
        ("8.8.8.2", True, 1),
    ]
    progress = events[4]
    assert (progress.state, progress.phase, progress.chunks_done, progress.chunks_total) == (
        "running",
        "port_scan",
        2,
        3,
    )

    # A repeated completion changes nothing and reports nothing.
    done = [result for result in results[:5] if isinstance(result, ChunkDone)]
    assert await deliver(db_session, done) == []


async def test_finding_returns_an_interrupted_scan_to_running(
    db_session: AsyncSession, make_scan: ScanFactory
) -> None:
    scan = await make_scan(state=ScanState.INTERRUPTED)

    await deliver(db_session, RECORDED.for_scan(scan.id)[:1])

    assert (await snapshot(db_session, scan))["scan"][0] is ScanState.RUNNING


# --- A chunk the scanner rejected ---


async def test_rejected_chunk_leaves_a_failed_scan_and_one_audit_entry(
    db_session: AsyncSession, make_scan: ScanFactory
) -> None:
    scan = await make_scan()
    results = RECORDED.for_scan(scan.id)
    rejection = ChunkRejected(scan_id=scan.id, chunk_seq=2, reason="out_of_scope")

    # Delivered twice, with a late finding after it.
    events = await deliver(db_session, [*results[:5], rejection, rejection, results[3]])

    stored = await snapshot(db_session, scan)
    state, phase, partial, reason, counters = stored["scan"]
    assert (state, phase, partial) == (ScanState.FAILED, None, True)
    assert reason == "chunk_rejected: out_of_scope"
    # The results collected before the rejection are kept, with their counts.
    assert stored["ports"] == [("8.8.8.1", 22), ("8.8.8.1", 443), ("8.8.8.2", 443)]
    assert stored["observations"]["8.8.8.3"][2:] == (2, 0)
    assert (counters["chunks_done"], counters["chunks_total"]) == (2, 3)
    # The chunk rows are gone; the totals stay on the scan.
    assert stored["chunks"] == {}
    assert await db_session.scalar(select(func.count()).select_from(ScanChunk)) == 0

    entries = (await db_session.scalars(select(AuditEvent))).all()
    assert [(e.actor, e.action, e.object_type, e.object_id) for e in entries] == [
        ("system", "scan.rejected", "scan", str(scan.id))
    ]
    assert entries[0].details == {"point": "scanner", "chunk_seq": 2, "reason": "out_of_scope"}

    final = events[-1]
    assert isinstance(final, ScanProgress) and final.state == "failed"
    assert (final.chunks_done, final.chunks_total) == (2, 3)
    assert (await db_session.get_one(Scan, scan.id)).finished_at is not None
