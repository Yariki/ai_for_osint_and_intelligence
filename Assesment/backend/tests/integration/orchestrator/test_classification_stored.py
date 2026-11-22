"""The classification phase (TASK-053; checks 3.4 and 2.5 for classification).

Every discovered host of a scan gets a result with its evidence and the
ruleset version. A newer ruleset changes no stored result; a re-run adds a
result beside the original. A failing phase keeps the observations and can
be retried.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import ClassificationResult, Scan, ScanPhase, ScanState
from ndm.orchestrator import phases
from ndm.orchestrator.classification import load_ruleset, parse_ruleset
from ndm.orchestrator.classification_phase import Classifier
from ndm.orchestrator.tick import Orchestrator
from tests.integration.orchestrator.conftest import Outbox, ScanFactory, snapshot
from tests.tools import reference_data
from tests.tools.enrichment_scan import REACHABLE, RECORDED, probe, reachable, run_phases, stored


@pytest.fixture(autouse=True)
async def datasets(classifying_orchestrator: Orchestrator) -> None:
    assert classifying_orchestrator.enricher is not None
    await reference_data.load(
        classifying_orchestrator.sessions,
        classifying_orchestrator.settings,
        classifying_orchestrator.enricher.geolite,
        RECORDED.reference,
    )


async def results(db: AsyncSession, scan: Scan) -> list[dict[str, Any]]:
    await db.commit()
    rows = await db.scalars(
        select(ClassificationResult)
        .where(ClassificationResult.scan_id == scan.id)
        .order_by(ClassificationResult.address, ClassificationResult.run)
        .execution_options(populate_existing=True)
    )
    return [
        {
            "address": str(row.address),
            "run": row.run,
            "latest": row.latest,
            "device_type": row.device_type,
            "sub_role": row.sub_role,
            "confidence": row.confidence,
            "rules": row.rules,
            "ruleset_version": row.ruleset_version,
        }
        for row in rows
    ]


def latest(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["address"]: row for row in rows if row["latest"]}


async def scan_to_end(orchestrator: Orchestrator, outbox: Outbox, make_scan: ScanFactory) -> Scan:
    scan = await probe(orchestrator, outbox, make_scan)
    await run_phases(orchestrator)
    return scan


async def test_every_discovered_host_is_classified_with_evidence_and_version(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    classifying_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    scan = await scan_to_end(classifying_orchestrator, outbox, make_scan)

    assert (await stored(db_session, scan)).state is ScanState.COMPLETED
    found = latest(await results(db_session, scan))
    assert set(found) == await reachable(db_session, scan)
    assert len(found) == REACHABLE
    assert {row["ruleset_version"] for row in found.values()} == {"1"}
    # 193.0.6.2 has 25, 587 and 993 open, but only 22, 80, 443 and 8080 were probed:
    # the classification sees what the scan found, nothing more.
    web = found["193.0.6.1"]  # 22, 80, 443 found open
    assert (web["device_type"], web["sub_role"], web["confidence"]) == ("server", "web", 0.5)
    assert web["rules"] == [
        {
            "rule": "server-web-port",
            "type": "server",
            "sub_role": "web",
            "confidence": 0.5,
            "evidence": {"open_ports": [80, 443]},
        }
    ]
    # A host with nothing to go on is unknown, with no rule.
    assert found["193.0.6.9"]["device_type"] == "unknown"
    assert found["193.0.6.9"]["rules"] == []


async def test_the_phase_reads_the_enrichment_snapshot(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    classifying_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    """The hosting rule looks at the organization, which only enrichment knows."""
    hosting = parse_ruleset(
        {
            "version": "hosting-test",
            "taxonomy": {"server": {"label": "Server"}},
            "rules": [
                {
                    "id": "hosting",
                    "type": "server",
                    "confidence": 0.9,
                    "when": {"enrichment": {"organization": "University"}},
                }
            ],
        }
    )
    phases.PHASES[ScanPhase.CLASSIFICATION] = Classifier(hosting, 0.5).run_phase

    scan = await scan_to_end(classifying_orchestrator, outbox, make_scan)

    found = latest(await results(db_session, scan))
    assert found["193.0.6.1"]["device_type"] == "server"
    assert found["193.0.6.1"]["rules"][0]["evidence"] == {
        "enrichment": {"organization": "University of Example"}
    }
    assert found["8.8.8.8"]["device_type"] == "unknown"


async def test_a_newer_ruleset_leaves_stored_results_unchanged_and_a_rerun_adds_one_beside(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    classifying_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    scan = await scan_to_end(classifying_orchestrator, outbox, make_scan)
    original = await results(db_session, scan)

    # Version 2 arrives: it calls everything with port 443 open a gateway.
    data = {
        "version": "2",
        "taxonomy": {"gateway": {"label": "Gateway"}},
        "rules": [
            {
                "id": "all-gateways",
                "type": "gateway",
                "confidence": 0.9,
                "when": {"open_any": [443]},
            }
        ],
    }
    newer = Classifier(parse_ruleset(data), 0.5)
    phases.PHASES[ScanPhase.CLASSIFICATION] = newer.run_phase
    await run_phases(classifying_orchestrator)

    # Loading it changed no stored result (FR-CLS-4).
    assert await results(db_session, scan) == original

    # A re-run, asked for by the analyst, adds results beside the original (FR-CLS-7).
    stored_scan = await stored(db_session, scan)
    stored_scan.classification_requested_at = datetime.now(UTC)
    await db_session.commit()
    assert await newer.rerun_requested(classifying_orchestrator.sessions) == [scan.id]

    after = await results(db_session, scan)
    assert [row | {"latest": False} for row in after if row["run"] == 1] == [
        row | {"latest": False} for row in original
    ]
    second = {row["address"]: row for row in after if row["run"] == 2}
    assert len(second) == REACHABLE and all(row["latest"] for row in second.values())
    assert second["193.0.6.1"]["device_type"] == "gateway"
    assert second["193.0.6.1"]["ruleset_version"] == "2"
    assert (await stored(db_session, scan)).classification_requested_at is None


async def test_rerun_waits_until_the_scan_has_ended(
    db_session: AsyncSession, make_scan: ScanFactory
) -> None:
    scan = await make_scan()  # running
    scan.classification_requested_at = datetime.now(UTC)
    await db_session.commit()
    classifier = Classifier(load_ruleset(), 0.5)

    from sqlalchemy.ext.asyncio import async_sessionmaker

    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    assert await classifier.rerun_requested(sessions) == []


async def test_failing_classification_keeps_the_observations_and_can_be_retried(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    classifying_orchestrator: Orchestrator,
    outbox: Outbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = phases.PHASES[ScanPhase.CLASSIFICATION]

    async def breaks(sessions: Any, scan_id: int) -> None:
        raise RuntimeError("the ruleset reader broke")

    scan = await probe(classifying_orchestrator, outbox, make_scan)
    before = await snapshot(db_session, scan)
    monkeypatch.setitem(phases.PHASES, ScanPhase.CLASSIFICATION, breaks)
    await run_phases(classifying_orchestrator)

    failed = await stored(db_session, scan)
    assert (failed.state, failed.failed_phase, failed.failure_reason) == (
        ScanState.FAILED,
        ScanPhase.CLASSIFICATION,
        "classification_failed",
    )
    after = await snapshot(db_session, scan)
    assert after["observations"] == before["observations"]
    assert after["ports"] == before["ports"]

    monkeypatch.setitem(phases.PHASES, ScanPhase.CLASSIFICATION, real)
    failed.retry_requested_at = datetime.now(UTC)
    await db_session.commit()
    await run_phases(classifying_orchestrator)

    assert (await stored(db_session, scan)).state is ScanState.COMPLETED
    assert len(latest(await results(db_session, scan))) == REACHABLE


async def test_a_phase_that_runs_again_adds_nothing_twice(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    classifying_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    scan = await scan_to_end(classifying_orchestrator, outbox, make_scan)
    once = await results(db_session, scan)

    classifier = Classifier(load_ruleset(), 0.5)
    await classifier.run_phase(classifying_orchestrator.sessions, scan.id)

    assert await results(db_session, scan) == once
