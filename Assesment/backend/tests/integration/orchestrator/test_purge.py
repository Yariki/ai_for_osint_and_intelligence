"""The deletion purge (I3; delivery plan 2.2, item 5).

After a scan and a project are deleted and the purge has run, none of their
rows remain except in ``host`` and ``audit_event``.
"""

from datetime import UTC, datetime
from ipaddress import IPv4Address
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ndm.db.audit import record_audit
from ndm.db.models import (
    Alert,
    Attestation,
    AuditEvent,
    ChangeEvent,
    ClassificationResult,
    EnrichmentSnapshot,
    GeoPoint,
    Host,
    HostObservation,
    PortObservation,
    Project,
    ProjectHost,
    Scan,
    ScanChunk,
    ScanDiff,
    ScanState,
    Schedule,
    ScheduleRun,
    ScopeEntry,
    TracerouteProbe,
    TracerouteResponder,
    TracerouteRun,
)
from ndm.orchestrator import purge as purge_module
from ndm.orchestrator.purge import NOT_PURGED, PROJECT_DATA, SCAN_DATA, purge
from ndm.orchestrator.recording import finish_scan, record_result
from tests.integration.orchestrator.conftest import ScanFactory
from tests.tools.stub_scanner import load_recorded_scan

RECORDED = load_recorded_scan()
NOW = datetime(2026, 11, 15, 12, tzinfo=UTC)


@pytest.fixture
def sessions(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine, expire_on_commit=False)


async def completed_scan(db: AsyncSession, make_scan: ScanFactory) -> Scan:
    """A scan with observations, open ports and hosts, as a real one leaves them."""
    scan = await make_scan()
    for result in RECORDED.for_scan(scan.id):
        await record_result(db, result)
    # Two enrichment runs for one of its hosts, as a re-run leaves them.
    address = IPv4Address(str(scan.targets[0]))
    db.add_all(
        [
            EnrichmentSnapshot(scan_id=scan.id, address=address, run=1, latest=False),
            EnrichmentSnapshot(
                scan_id=scan.id, address=address, run=2, location=GeoPoint(50.45, 30.52)
            ),
            ClassificationResult(
                scan_id=scan.id,
                address=address,
                device_type="router",
                confidence=0.8,
                rules=[],
                ruleset_version="1",
            ),
        ]
    )
    # A traceroute to it, with a hop that answered and was enriched.
    run = TracerouteRun(
        project_id=scan.project_id,
        attestation_id=scan.attestation_id,
        target=address,
        method="icmp",
        parameters={},
    )
    db.add(run)
    await db.flush()
    hop = IPv4Address("192.0.2.1")
    db.add_all(
        [
            TracerouteProbe(run_id=run.id, ttl=1, probe=0, responder=hop, rtt_ms=1.0),
            TracerouteResponder(run_id=run.id, address=hop, unlocatable=False),
            EnrichmentSnapshot(traceroute_run_id=run.id, address=hop, asn=64496),
        ]
    )
    await record_audit(
        db, actor="analyst", action="scan.launch", object_type="scan", object_id=scan.id
    )
    await db.commit()
    return scan


async def schedule_with_history(db: AsyncSession, scan: Scan) -> None:
    """A schedule that started the scan, a diff of the scan with itself, and an alert."""
    nightly = Schedule(
        project_id=scan.project_id,
        name="Nightly",
        targets=["8.8.8.0/24"],
        parameters={},
        interval_seconds=86400,
        next_run_at=NOW,
    )
    db.add(nightly)
    await db.flush()
    scan.schedule_id = nightly.id
    diff = ScanDiff(
        base_scan_id=scan.id,
        target_scan_id=scan.id,
        compared_ports=[],
        not_comparable=[],
        summary={},
    )
    db.add_all(
        [
            diff,
            ScheduleRun(schedule_id=nightly.id, planned_at=NOW, outcome="started", scan_id=scan.id),
        ]
    )
    await db.flush()
    db.add_all(
        [
            ChangeEvent(
                diff_id=diff.id, address=IPv4Address("8.8.8.1"), kind="port_opened", port=22
            ),
            Alert(
                project_id=scan.project_id,
                type="changes_detected",
                summary="1 change",
                link={"diff_id": diff.id},
            ),
        ]
    )
    await db.commit()


async def rows(db: AsyncSession, model: Any, *conditions: Any) -> int:
    await db.commit()
    return await db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


# --- The registry covers every table ---


async def test_every_table_is_registered_or_named_as_kept(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as connection:
        tables = set(
            await connection.scalars(
                text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
                    "AND NOT c.relispartition"
                )
            )
        )

    registered = set(SCAN_DATA) | set(PROJECT_DATA) | set(NOT_PURGED) | {"scan", "project"}
    assert tables - registered == set(), "tables the purge does not know about"
    assert registered - tables == set(), "the registry names tables that do not exist"


async def test_table_that_refers_to_a_scan_or_a_project_must_be_purged(
    owner_engine: AsyncEngine,
) -> None:
    """A table with a foreign key to scan or project holds their data. If a later
    phase adds one and does not register it, this fails."""
    async with owner_engine.connect() as connection:
        referring = (
            await connection.execute(
                text(
                    "SELECT DISTINCT child.relname, parent.relname FROM pg_constraint k "
                    "JOIN pg_class child ON child.oid = k.conrelid "
                    "JOIN pg_class parent ON parent.oid = k.confrelid "
                    "WHERE k.contype = 'f' AND parent.relname IN ('scan', 'project') "
                    "AND NOT child.relispartition"
                )
            )
        ).all()

    purged = set(SCAN_DATA) | set(PROJECT_DATA) | {"scan", "project"}
    forgotten = sorted({child for child, _ in referring} - purged)
    assert forgotten == [], f"hold scan or project data but are not purged: {forgotten}"
    # A table of SCAN_DATA belongs to a scan directly, or to a table that does
    # (change events belong to a diff of the scan).
    of_scan = {child for child, parent in referring if parent == "scan"}
    async with owner_engine.connect() as connection:
        through = set(
            await connection.scalars(
                text(
                    "SELECT DISTINCT child.relname FROM pg_constraint k "
                    "JOIN pg_class child ON child.oid = k.conrelid "
                    "JOIN pg_class parent ON parent.oid = k.confrelid "
                    "WHERE k.contype = 'f' AND parent.relname = ANY(:tables)"
                ),
                {"tables": sorted(of_scan)},
            )
        )
    assert of_scan | through >= set(SCAN_DATA)


# --- A deleted scan ---


async def test_deleted_scan_is_removed_and_the_rest_stays(
    db_session: AsyncSession, make_scan: ScanFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    deleted = await completed_scan(db_session, make_scan)
    kept = await completed_scan(db_session, make_scan)
    await finish_scan(db_session, deleted, ScanState.COMPLETED)
    deleted.deleted_at = NOW
    await db_session.commit()

    removed = await purge(sessions)

    assert removed == {"scans": 1, "projects": 0}
    for model in (
        HostObservation,
        PortObservation,
        ScanChunk,
        EnrichmentSnapshot,
        ClassificationResult,
    ):
        assert await rows(db_session, model, model.scan_id == deleted.id) == 0
        assert await rows(db_session, model, model.scan_id == kept.id) > 0
    # Traceroutes belong to the project, which is still there.
    assert await rows(db_session, TracerouteRun) == 2
    assert await rows(db_session, Scan) == 1
    # The project of the deleted scan is untouched, and so is what is global.
    assert await rows(db_session, Project) == 2
    assert await rows(db_session, ProjectHost, ProjectHost.project_id == deleted.project_id) == 3
    assert await rows(db_session, Host) == 3
    assert await rows(db_session, AuditEvent) == 2
    # The project no longer points at the scan that is gone.
    latest = await db_session.scalars(
        select(ProjectHost.latest_scan_id).where(ProjectHost.project_id == deleted.project_id)
    )
    assert set(latest) == {None}


async def test_scan_that_is_still_running_is_not_purged_yet(
    db_session: AsyncSession, make_scan: ScanFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    scan = await completed_scan(db_session, make_scan)  # in fact still running
    scan.deleted_at = NOW
    await db_session.commit()

    assert await purge(sessions) == {"scans": 0, "projects": 0}
    assert await rows(db_session, HostObservation) == 4

    # The tick stops it; then it goes.
    await finish_scan(db_session, scan, ScanState.CANCELLED)
    await db_session.commit()
    assert await purge(sessions) == {"scans": 1, "projects": 0}
    assert await rows(db_session, HostObservation) == 0


# --- A deleted project ---


async def test_deleted_project_is_removed_with_everything_it_owns(
    db_session: AsyncSession, make_scan: ScanFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    first = await completed_scan(db_session, make_scan)
    kept = await completed_scan(db_session, make_scan)
    await finish_scan(db_session, first, ScanState.COMPLETED)
    project = await db_session.get_one(Project, first.project_id)
    project.deleted_at = NOW
    await record_audit(
        db_session,
        actor="analyst",
        action="project.delete",
        object_type="project",
        object_id=project.id,
    )
    await db_session.commit()

    removed = await purge(sessions)

    assert removed == {"scans": 1, "projects": 1}
    # Nothing of the project remains...
    assert await rows(db_session, Project, Project.id == project.id) == 0
    assert await rows(db_session, Scan, Scan.project_id == project.id) == 0
    assert await rows(db_session, Attestation, Attestation.project_id == project.id) == 0
    assert await rows(db_session, ProjectHost, ProjectHost.project_id == project.id) == 0
    assert await rows(db_session, ScopeEntry) == 1
    for model in (HostObservation, PortObservation, EnrichmentSnapshot, ClassificationResult):
        assert await rows(db_session, model, model.scan_id == first.id) == 0
    # The project's traceroutes are gone with it, their hops' snapshots too.
    assert await rows(db_session, TracerouteRun, TracerouteRun.project_id == project.id) == 0
    assert await rows(db_session, TracerouteProbe) == 1
    assert await rows(db_session, TracerouteResponder) == 1
    assert await rows(db_session, EnrichmentSnapshot, EnrichmentSnapshot.scan_id.is_(None)) == 1
    # ...except in the host registry and the audit log.
    assert await rows(db_session, Host) == 3
    actions = await db_session.scalars(select(AuditEvent.action).order_by(AuditEvent.id))
    assert list(actions) == ["scan.launch", "scan.launch", "project.delete"]
    # The other project is complete.
    assert await rows(db_session, Project) == 1
    assert await rows(db_session, HostObservation, HostObservation.scan_id == kept.id) == 4
    assert await rows(db_session, PortObservation, PortObservation.scan_id == kept.id) == 3
    assert await rows(db_session, EnrichmentSnapshot, EnrichmentSnapshot.scan_id == kept.id) == 2


async def test_project_waits_until_its_running_scan_has_ended(
    db_session: AsyncSession, make_scan: ScanFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    scan = await completed_scan(db_session, make_scan)  # still running
    project = await db_session.get_one(Project, scan.project_id)
    project.deleted_at = NOW
    await db_session.commit()

    assert await purge(sessions) == {"scans": 0, "projects": 0}
    assert await rows(db_session, Project) == 1


# --- Batches, and nothing to do ---


async def test_large_scan_is_deleted_in_batches(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(purge_module, "BATCH_ROWS", 2)
    scan = await completed_scan(db_session, make_scan)
    await finish_scan(db_session, scan, ScanState.COMPLETED)
    scan.deleted_at = NOW
    await db_session.commit()

    assert await purge(sessions) == {"scans": 1, "projects": 0}

    assert await rows(db_session, HostObservation) == 0
    assert await rows(db_session, PortObservation) == 0


async def test_purge_with_nothing_deleted_removes_nothing(
    db_session: AsyncSession, make_scan: ScanFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    scan = await completed_scan(db_session, make_scan)
    await finish_scan(db_session, scan, ScanState.COMPLETED)
    await db_session.commit()

    assert await purge(sessions) == {"scans": 0, "projects": 0}
    assert await purge(sessions) == {"scans": 0, "projects": 0}

    assert await rows(db_session, Scan) == 1
    assert await rows(db_session, HostObservation) == 4


# --- Schedules, diffs and alerts (Phase 4) ---


async def test_deleted_scan_takes_its_diffs_and_leaves_the_schedule(
    db_session: AsyncSession, make_scan: ScanFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    deleted = await completed_scan(db_session, make_scan)
    await schedule_with_history(db_session, deleted)
    await finish_scan(db_session, deleted, ScanState.COMPLETED)
    deleted.deleted_at = NOW
    await db_session.commit()

    assert await purge(sessions) == {"scans": 1, "projects": 0}

    assert await rows(db_session, ScanDiff) == 0
    assert await rows(db_session, ChangeEvent) == 0
    # The schedule and its record of runs stay with the project; the run no longer names the scan.
    assert await rows(db_session, Schedule) == 1
    run = await db_session.scalar(select(ScheduleRun).execution_options(populate_existing=True))
    assert run is not None and run.scan_id is None
    # The alert stays too: its summary still says what was found.
    assert await rows(db_session, Alert) == 1


async def test_deleted_project_takes_its_schedules_runs_and_alerts(
    db_session: AsyncSession, make_scan: ScanFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    scan = await completed_scan(db_session, make_scan)
    await schedule_with_history(db_session, scan)
    kept = await completed_scan(db_session, make_scan)
    await schedule_with_history(db_session, kept)
    await finish_scan(db_session, scan, ScanState.COMPLETED)
    project = await db_session.get_one(Project, scan.project_id)
    project.deleted_at = NOW
    await db_session.commit()

    assert await purge(sessions) == {"scans": 1, "projects": 1}

    for model in (Schedule, Alert):
        assert await rows(db_session, model, model.project_id == project.id) == 0
        assert await rows(db_session, model, model.project_id == kept.project_id) == 1
    assert await rows(db_session, ScheduleRun) == 1
    assert await rows(db_session, ScanDiff) == 1
    assert await rows(db_session, ChangeEvent) == 1
