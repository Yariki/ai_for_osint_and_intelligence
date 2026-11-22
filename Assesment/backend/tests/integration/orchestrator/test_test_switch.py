"""The test switch in the orchestrator (NFR-SEC-6).

It has no effect unless every process reports it, and its use leaves an audit
entry that includes the value the scanner reports in its hello message.
"""

from ipaddress import IPv4Address
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.settings import Settings
from ndm.db.models import AuditEvent, Scan, ScanState
from ndm.orchestrator.tick import Orchestrator, tick
from tests.integration.orchestrator.conftest import (
    Outbox,
    ScanFactory,
    scanner_reports,
    snapshot,
)


@pytest.fixture
def with_switch(monkeypatch: pytest.MonkeyPatch, orchestrator: Orchestrator) -> Orchestrator:
    """The orchestrator with NDM_ALLOW_NON_PUBLIC_TARGETS set in its environment."""
    monkeypatch.setenv("NDM_ALLOW_NON_PUBLIC_TARGETS", "true")
    orchestrator.settings = Settings()
    assert orchestrator.settings.allow_non_public_targets is True
    return orchestrator


async def lab_scan(make_scan: ScanFactory) -> Scan:
    """A scan of lab addresses, which are private."""
    return await make_scan(
        state=ScanState.QUEUED,
        targets=[IPv4Address("10.77.3.10"), IPv4Address("10.77.3.20")],
        scope=("10.77.3.0", "10.77.3.255"),
    )


async def switch_entries(db: AsyncSession) -> list[dict[str, Any]]:
    await db.commit()
    entries = await db.scalars(
        select(AuditEvent).where(AuditEvent.action == "test_switch.enabled").order_by(AuditEvent.id)
    )
    return [{"actor": entry.actor, **entry.details} for entry in entries]


# --- Effect ---


async def test_with_the_switch_on_both_sides_lab_targets_are_scanned(
    db_session: AsyncSession, make_scan: ScanFactory, with_switch: Orchestrator, outbox: Outbox
) -> None:
    scan = await lab_scan(make_scan)
    await scanner_reports(with_switch, allow_non_public=True)

    await tick(with_switch)

    assert [str(target) for target in sorted(outbox.chunks[0].targets)] == [
        "10.77.3.10",
        "10.77.3.20",
    ]
    assert (await snapshot(db_session, scan))["scan"][0] is ScanState.RUNNING


async def test_switch_on_the_orchestrator_alone_has_no_effect(
    db_session: AsyncSession, make_scan: ScanFactory, with_switch: Orchestrator, outbox: Outbox
) -> None:
    scan = await lab_scan(make_scan)
    await scanner_reports(with_switch, allow_non_public=False)

    await tick(with_switch)

    assert outbox.chunks == []
    assert (await snapshot(db_session, scan))["scan"][3] == "scope_changed"


async def test_switch_on_the_scanner_alone_has_no_effect(
    db_session: AsyncSession, make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    scan = await lab_scan(make_scan)
    await scanner_reports(orchestrator, allow_non_public=True)

    await tick(orchestrator)

    assert outbox.chunks == []
    assert (await snapshot(db_session, scan))["scan"][3] == "scope_changed"


async def test_switch_is_off_by_default_and_public_scans_are_not_affected(
    make_scan: ScanFactory, orchestrator: Orchestrator, outbox: Outbox
) -> None:
    await lab_scan(make_scan)
    await make_scan(state=ScanState.QUEUED)
    await scanner_reports(orchestrator)

    await tick(orchestrator)

    assert orchestrator.settings.allow_non_public_targets is False
    assert [str(chunk.targets[0])[:6] for chunk in outbox.chunks] == ["8.8.8."]


# --- Audit ---


async def test_use_of_the_switch_leaves_an_audit_entry_with_the_scanners_value(
    db_session: AsyncSession, with_switch: Orchestrator
) -> None:
    await scanner_reports(with_switch, allow_non_public=True)

    assert await switch_entries(db_session) == [
        {
            "actor": "system",
            "process": "orchestrator",
            "orchestrator": True,
            "scanner": True,
            "vantage_point": "test",
            "effective": True,
        }
    ]


@pytest.mark.parametrize(
    ("orchestrator_on", "scanner_on"), [(True, False), (False, True)], ids=["orch", "scanner"]
)
async def test_switch_on_one_side_is_audited_as_not_effective(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    orchestrator: Orchestrator,
    orchestrator_on: bool,
    scanner_on: bool,
) -> None:
    if orchestrator_on:
        monkeypatch.setenv("NDM_ALLOW_NON_PUBLIC_TARGETS", "true")
        orchestrator.settings = Settings()

    await scanner_reports(orchestrator, allow_non_public=scanner_on)

    (entry,) = await switch_entries(db_session)
    assert (entry["orchestrator"], entry["scanner"], entry["effective"]) == (
        orchestrator_on,
        scanner_on,
        False,
    )


async def test_no_entry_when_the_switch_is_off_everywhere(
    db_session: AsyncSession, orchestrator: Orchestrator
) -> None:
    await scanner_reports(orchestrator)
    await scanner_reports(orchestrator, hello=False)

    assert await switch_entries(db_session) == []


async def test_heartbeats_do_not_repeat_the_entry(
    db_session: AsyncSession, with_switch: Orchestrator
) -> None:
    await scanner_reports(with_switch, allow_non_public=True)
    for _ in range(3):
        await scanner_reports(with_switch, hello=False, allow_non_public=True)

    assert len(await switch_entries(db_session)) == 1
