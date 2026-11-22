"""FR-AUD-1 in full: every action on the list is performed once, and each
leaves its audit entry with the actor of FR-AUD-2 (the account, or the
system for scheduled and background actions).

The list of FR-AUD-1: login and failed login; project creation, archival and
deletion; every scope change with its attestation; scan launch, cancellation
and deletion; schedule creation and change; traceroute execution;
classification override; reference-dataset updates; and any scan or run
rejected by a scope check.
"""

# Fixtures imported by name are used as arguments below.
# ruff: noqa: F811

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import AuditEvent, Schedule
from ndm.orchestrator import scheduler
from ndm.orchestrator.datasets.manager import Dataset, DatasetManager
from ndm.orchestrator.tick import Orchestrator, tick
from tests.integration.api.conftest import (  # noqa: F401
    USERNAME,
    account,
    anonymous_client,
    app,
    client,
    extra_env,
    settings,
)
from tests.integration.orchestrator.conftest import (  # noqa: F401
    Outbox,
    orchestrator,
    outbox,
    scanner_reports,
)

# Each item of FR-AUD-1, with the entries that record it: (action, actor, object type).
SYSTEM = "system"
EXPECTED: dict[str, list[tuple[str, str, str]]] = {
    "login": [("login", USERNAME, "account")],
    # Nobody is logged in yet: the actor is "anonymous", the name tried is in the details.
    "failed login": [("login.failed", "anonymous", "account")],
    "project creation": [("project.create", USERNAME, "project")],
    "project archival": [("project.archive", USERNAME, "project")],
    "project deletion": [("project.delete", USERNAME, "project")],
    "scope change with its attestation": [("scope.change", USERNAME, "project")],
    "scan launch": [("scan.launch", USERNAME, "scan"), ("scan.launch", SYSTEM, "scan")],
    "scan cancellation": [("scan.cancel", USERNAME, "scan")],
    "scan deletion": [("scan.delete", USERNAME, "scan")],
    "schedule creation": [("schedule.create", USERNAME, "schedule")],
    "schedule change": [
        ("schedule.update", USERNAME, "schedule"),
        ("schedule.disable", USERNAME, "schedule"),
        ("schedule.enable", USERNAME, "schedule"),
        ("schedule.delete", USERNAME, "schedule"),
    ],
    "traceroute execution": [("traceroute.run", USERNAME, "traceroute")],
    "classification override": [
        ("host.override_set", USERNAME, "host"),
        ("host.override_cleared", USERNAME, "host"),
    ],
    "reference-dataset update": [("dataset.update", SYSTEM, "dataset")],
    "scan or run rejected by a scope check": [
        ("scan.rejected", USERNAME, "project"),
        ("traceroute.rejected", USERNAME, "project"),
        ("schedule.rejected", USERNAME, "project"),
        ("scan.rejected", SYSTEM, "project"),
    ],
}


async def ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json() if response.status_code != 204 else None


async def entries(db: AsyncSession) -> list[AuditEvent]:
    await db.rollback()
    return list(await db.scalars(select(AuditEvent).order_by(AuditEvent.id)))


async def test_every_action_of_fr_aud_1_leaves_its_entry(
    app: FastAPI,
    client: AsyncClient,
    anonymous_client: AsyncClient,
    db_session: AsyncSession,
    orchestrator: Orchestrator,
) -> None:
    api = "/api/v1"
    # Login happened when the client fixture logged in. A failed login:
    refused = await anonymous_client.post(
        f"{api}/login", json={"username": USERNAME, "password": "wrong password"}
    )
    assert refused.status_code == 401

    # Projects and scope.
    project = (await ok(await client.post(f"{api}/projects", json={"name": "Edge"}), 201))["id"]
    await ok(
        await client.put(
            f"{api}/projects/{project}/scope",
            json={"entries": ["8.8.8.0/24"], "authorized": True},
        )
    )
    other = (await ok(await client.post(f"{api}/projects", json={"name": "Old"}), 201))["id"]
    await ok(await client.post(f"{api}/projects/{other}/archive"))
    await ok(await client.delete(f"{api}/projects/{other}", params={"confirm": "true"}), 204)

    # Scans: launched, started by the orchestrator, cancelled, deleted; one rejected.
    body = {"targets": ["8.8.8.1-8.8.8.4"], "port_profile": "top-100"}
    scan = (await ok(await client.post(f"{api}/projects/{project}/scans", json=body), 201))["id"]
    await scanner_reports(orchestrator)
    await tick(orchestrator)  # the targets are now known to the project
    await ok(await client.post(f"{api}/scans/{scan}/cancel"))
    await ok(await client.delete(f"{api}/scans/{scan}", params={"confirm": "true"}), 204)
    rejected = await client.post(
        f"{api}/projects/{project}/scans", json=body | {"targets": ["9.9.9.9"]}
    )
    assert rejected.status_code == 422

    # Classification override, set and cleared.
    override = f"{api}/projects/{project}/hosts/8.8.8.1/override"
    await ok(await client.put(override, json={"device_type": "router"}))
    await ok(await client.delete(override))

    # Traceroutes: one run, one rejected.
    traceroutes = f"{api}/projects/{project}/traceroutes"
    await ok(await client.post(traceroutes, json={"target": "8.8.8.1"}), 201)
    assert (await client.post(traceroutes, json={"target": "9.9.9.9"})).status_code == 422

    # Schedules: created, changed, disabled, enabled; one rejected when saved.
    schedule_body = {
        "name": "Nightly",
        "targets": ["8.8.8.1-8.8.8.4"],
        "parameters": {"port_profile": "top-100"},
        "interval_seconds": 86400,
    }
    schedules = f"{api}/projects/{project}/schedules"
    first = (await ok(await client.post(schedules, json=schedule_body), 201))["id"]
    await ok(await client.put(f"{api}/schedules/{first}", json=schedule_body | {"name": "Daily"}))
    await ok(await client.post(f"{api}/schedules/{first}/disable"))
    await ok(await client.post(f"{api}/schedules/{first}/enable"))
    bad = await client.post(schedules, json=schedule_body | {"targets": ["9.9.9.9"]})
    assert bad.status_code == 422

    # The scheduler: one run started, then one rejected after the scope shrank.
    sessions = app.state.session_factory
    stored = await db_session.get_one(Schedule, first)
    stored.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()
    started = (await scheduler.run_due(sessions, app.state.settings, [])).started
    assert started
    # That run ends before the next is due; otherwise the next would be skipped.
    await ok(await client.post(f"{api}/scans/{started[0]}/cancel"))
    await tick(orchestrator)
    await ok(
        await client.put(
            f"{api}/projects/{project}/scope",
            json={"entries": ["8.8.8.1"], "authorized": True},
        )
    )
    stored = await db_session.get_one(Schedule, first, populate_existing=True)
    stored.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()
    outcome = await scheduler.run_due(sessions, app.state.settings, [])
    assert [alert.alert_type for alert in outcome.alerts] == ["scheduled_run_rejected"]
    await ok(await client.delete(f"{api}/schedules/{first}"), 204)

    # A reference-dataset update, by the orchestrator's dataset manager.
    async def load(db: AsyncSession, current: str | None) -> str:
        return "2026-11-13"

    manager = DatasetManager(
        sessions, [Dataset(name="test-data", interval=timedelta(days=1), update=load)]
    )
    assert await manager.run_due(datetime.now(UTC)) == ["test-data"]

    # Every item of the list has its entries.
    found = await entries(db_session)
    recorded = {(e.action, e.actor, e.object_type) for e in found}
    missing = {
        item: [entry for entry in wanted if entry not in recorded]
        for item, wanted in EXPECTED.items()
    }
    assert {item: gaps for item, gaps in missing.items() if gaps} == {}

    # Each entry names its object, and a scope change names its attestation.
    for entry in found:
        if entry.action not in ("login", "login.failed"):
            assert entry.object_id, entry.action
    scope_changes = [e for e in found if e.action == "scope.change"]
    assert len(scope_changes) == 2
    assert all(e.details.get("attestation_id") for e in scope_changes)
    by_scheduler = [e for e in found if e.actor == SYSTEM and e.action == "scan.rejected"]
    assert by_scheduler[0].details["point"] == "scheduler"
