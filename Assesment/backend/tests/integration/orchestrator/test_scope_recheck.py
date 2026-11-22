"""Check 1B.8, middle part: the scope is checked again before each chunk (FR-PRJ-7, I4).

A scope reduced mid-scan fails the scan with ``scope_changed``, keeps its
results and leaves an audit entry. The check fails closed.
"""

from datetime import UTC, datetime
from ipaddress import IPv4Address
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.scope import parse_never_scan
from ndm.db.models import (
    Attestation,
    AuditEvent,
    Project,
    ProjectState,
    Scan,
    ScanState,
    ScopeEntry,
    ScopeEntryKind,
)
from ndm.orchestrator.tick import Orchestrator, tick
from tests.integration.orchestrator.conftest import (
    Outbox,
    ScanFactory,
    answer,
    scanner_reports,
    snapshot,
)
from tests.tools.stub_scanner import load_recorded_scan

WORLD = load_recorded_scan().world


async def replace_scope(db: AsyncSession, scan: Scan, entries: list[tuple[str, str]]) -> int:
    """What saving a new scope does: a new attestation becomes the current one."""
    project = await db.get_one(Project, scan.project_id, populate_existing=True)
    old = await db.get_one(Attestation, scan.attestation_id)
    attestation = Attestation(project_id=project.id, account_id=old.account_id)
    db.add(attestation)
    await db.flush()
    for low, high in entries:
        db.add(
            ScopeEntry(
                attestation_id=attestation.id,
                kind=ScopeEntryKind.RANGE,
                original_text=f"{low}-{high}",
                start_address=IPv4Address(low),
                end_address=IPv4Address(high),
            )
        )
    project.current_attestation_id = attestation.id
    await db.commit()
    return attestation.id


async def discovery_done(
    make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox, **scan: Any
) -> Scan:
    """A running scan whose discovery chunk has been answered."""
    created = await make_scan(state=ScanState.QUEUED, **scan)
    await scanner_reports(orchestrator)
    await tick(orchestrator)
    await answer(orchestrator, outbox.chunks[0], WORLD)
    return created


async def rejections(db: AsyncSession) -> list[dict[str, Any]]:
    await db.commit()
    entries = await db.scalars(select(AuditEvent).where(AuditEvent.action == "scan.rejected"))
    return [
        {"actor": e.actor, "object": (e.object_type, e.object_id), **e.details} for e in entries
    ]


# --- A scope reduced mid-scan ---


async def test_scope_reduced_mid_scan_fails_the_scan_and_keeps_its_results(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan = await discovery_done(make_scan, orchestrator, outbox)
    # The analyst saves a smaller scope: 8.8.8.2 and 8.8.8.3 are no longer in it.
    await replace_scope(db_session, scan, [("8.8.8.0", "8.8.8.1")])

    await tick(orchestrator)
    await tick(orchestrator)

    stored = await snapshot(db_session, scan)
    state, phase, partial, reason, counters = stored["scan"]
    assert (state, phase, partial, reason) == (ScanState.FAILED, None, True, "scope_changed")
    # No chunk was sent after the scope changed.
    assert [chunk.chunk_seq for chunk in outbox.chunks] == [0]
    # What was found before is kept.
    assert [a for a, row in stored["observations"].items() if row[0]] == [
        "8.8.8.1",
        "8.8.8.2",
        "8.8.8.3",
    ]
    assert (counters["chunks_done"], counters["chunks_total"]) == (1, 2)
    assert await rejections(db_session) == [
        {
            "actor": "system",
            "object": ("scan", str(scan.id)),
            "point": "orchestrator",
            "chunk_seq": 1,
            "reason": "out_of_scope",
        }
    ]


async def test_scope_change_that_still_covers_the_targets_lets_the_scan_go_on(
    make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox, db_session: AsyncSession
) -> None:
    scan = await discovery_done(make_scan, orchestrator, outbox)
    attestation_id = await replace_scope(
        db_session, scan, [("8.8.8.0", "8.8.8.127"), ("9.9.9.0", "9.9.9.255")]
    )

    await tick(orchestrator)

    # The next chunk goes out, with the scope as it is now and its attestation.
    port_scan = outbox.chunks[1]
    assert [(str(low), str(high)) for low, high in port_scan.scope] == [
        ("8.8.8.0", "8.8.8.127"),
        ("9.9.9.0", "9.9.9.255"),
    ]
    assert port_scan.attestation_id == attestation_id
    assert await rejections(db_session) == []


async def test_scope_removed_before_the_first_chunk_fails_the_scan(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan = await make_scan(state=ScanState.QUEUED)
    await replace_scope(db_session, scan, [])  # an empty scope covers nothing
    await scanner_reports(orchestrator)

    await tick(orchestrator)

    assert outbox.chunks == []
    assert (await snapshot(db_session, scan))["scan"][3] == "scope_changed"
    assert [entry["reason"] for entry in await rejections(db_session)] == ["out_of_scope"]


# --- The project is no longer active ---


@pytest.mark.parametrize(
    ("change", "cause"),
    [
        ({"state": ProjectState.ARCHIVED}, "project_archived"),
        ({"deleted_at": datetime(2026, 11, 15, 12, tzinfo=UTC)}, "project_deleted"),
    ],
)
async def test_archived_or_deleted_project_stops_its_scan(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    orchestrator: Orchestrator,
    outbox: Outbox,
    change: dict[str, Any],
    cause: str,
) -> None:
    scan = await discovery_done(make_scan, orchestrator, outbox)
    project = await db_session.get_one(Project, scan.project_id)
    for name, value in change.items():
        setattr(project, name, value)
    await db_session.commit()

    await tick(orchestrator)

    assert (await snapshot(db_session, scan))["scan"][3] == "scope_changed"
    assert len(outbox.chunks) == 1
    assert [entry["reason"] for entry in await rejections(db_session)] == [cause]


# --- The orchestrator's own never-scan list and public-address table ---


async def test_target_on_the_orchestrators_never_scan_list_is_not_sent(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    orchestrator.never_scan = parse_never_scan(["8.8.8.3"])
    scan = await make_scan(state=ScanState.QUEUED)
    await scanner_reports(orchestrator)

    await tick(orchestrator)

    assert outbox.chunks == []
    assert (await snapshot(db_session, scan))["scan"][0] is ScanState.FAILED
    assert [entry["reason"] for entry in await rejections(db_session)] == ["never_scan"]


async def test_non_public_target_is_not_sent(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    # A scan that should never have been created: private targets in a private scope.
    scan = await make_scan(
        state=ScanState.QUEUED,
        targets=[IPv4Address("10.0.0.5")],
        scope=("10.0.0.0", "10.0.0.255"),
    )
    await scanner_reports(orchestrator)

    await tick(orchestrator)

    assert outbox.chunks == []
    assert (await snapshot(db_session, scan))["scan"][3] == "scope_changed"
    assert [entry["reason"] for entry in await rejections(db_session)] == ["non_public"]


async def test_one_scan_failing_its_check_does_not_stop_another(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    out_of_scope = await make_scan(state=ScanState.QUEUED)
    in_scope = await make_scan(state=ScanState.QUEUED)
    await replace_scope(db_session, out_of_scope, [("9.9.9.0", "9.9.9.255")])
    await scanner_reports(orchestrator)

    await tick(orchestrator)

    assert [chunk.scan_id for chunk in outbox.chunks] == [in_scope.id]
    assert (await snapshot(db_session, out_of_scope))["scan"][0] is ScanState.FAILED
    assert (await snapshot(db_session, in_scope))["scan"][0] is ScanState.RUNNING
