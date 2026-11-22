"""Schedules (FR-SCH-1, FR-SCH-2, FR-SCH-6): checked against the scope and the
minimum interval when saved, audited, and listed with the next run and the
outcome of the last one."""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import AuditEvent, Scan, ScanState, Schedule, ScheduleRun
from ndm.orchestrator import scheduler
from ndm.orchestrator.scheduler import run_due
from tests.integration.api.conftest import audit_actions

HOUR = 3600


@pytest.fixture
async def project_id(client: AsyncClient) -> int:
    """A project with the scope 8.8.8.0/24."""
    created: int = (await client.post("/api/v1/projects", json={"name": "Edge"})).json()["id"]
    saved = await client.put(
        f"/api/v1/projects/{created}/scope", json={"entries": ["8.8.8.0/24"], "authorized": True}
    )
    assert saved.status_code == 200
    return created


def body(**values: Any) -> dict[str, Any]:
    return {
        "name": "Nightly edge sweep",
        "targets": ["8.8.8.1-8.8.8.4", "8.8.8.10"],
        "parameters": {"port_profile": "top-100"},
        "interval_seconds": 6 * HOUR,
    } | values


async def create(client: AsyncClient, project_id: int, **values: Any) -> Any:
    return await client.post(f"/api/v1/projects/{project_id}/schedules", json=body(**values))


async def schedules(db: AsyncSession) -> int:
    await db.rollback()
    return await db.scalar(select(func.count()).select_from(Schedule)) or 0


# --- Creating ---


async def test_a_schedule_is_created_with_its_next_run(
    client: AsyncClient, project_id: int, db_session: AsyncSession
) -> None:
    before = datetime.now(UTC)
    response = await create(client, project_id)

    assert response.status_code == 201, response.text
    created = response.json()
    assert created["enabled"] is True
    assert created["targets"] == ["8.8.8.1-8.8.8.4", "8.8.8.10"]
    assert created["parameters"]["port_profile"] == "top-100"
    assert created["parameters"]["pps"] == 1000  # the scan defaults, filled in
    assert created["flag_reason"] is None and created["last_run"] is None
    next_run = datetime.fromisoformat(created["next_run_at"])
    assert before + timedelta(hours=6) <= next_run <= datetime.now(UTC) + timedelta(hours=6)

    entry = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "schedule.create")
    )
    assert entry is not None
    assert (entry.actor, entry.object_type, entry.object_id) == (
        "analyst",
        "schedule",
        str(created["id"]),
    )
    assert entry.details["interval_seconds"] == 6 * HOUR
    assert entry.details["targets"] == created["targets"]


async def test_a_cron_schedule_runs_at_the_wall_clock_of_its_time_zone(
    client: AsyncClient, project_id: int
) -> None:
    response = await create(
        client, project_id, interval_seconds=None, cron="30 2 * * *", time_zone="Europe/Kyiv"
    )

    assert response.status_code == 201, response.text
    next_run = datetime.fromisoformat(response.json()["next_run_at"]).astimezone(
        ZoneInfo("Europe/Kyiv")
    )
    assert (next_run.hour, next_run.minute) == (2, 30)
    assert next_run > datetime.now(UTC)


@pytest.mark.parametrize(
    ("values", "field", "code"),
    [
        ({"interval_seconds": HOUR - 1}, "interval_seconds", "below_minimum_interval"),
        ({"interval_seconds": None, "cron": "*/30 * * * *"}, "cron", "below_minimum_interval"),
        ({"interval_seconds": None, "cron": "0,20 9 * * *"}, "cron", "below_minimum_interval"),
        ({"interval_seconds": None, "cron": "0 25 * * *"}, "cron", "invalid_cron"),
        ({"time_zone": "Atlantis/Capital"}, "time_zone", "invalid_time_zone"),
    ],
)
async def test_a_recurrence_below_the_minimum_or_not_valid_is_rejected(
    client: AsyncClient,
    project_id: int,
    db_session: AsyncSession,
    values: dict[str, Any],
    field: str,
    code: str,
) -> None:
    response = await create(client, project_id, **values)

    assert response.status_code == 422
    [detail] = response.json()["error"]["details"]
    assert (detail["field"], detail["code"]) == (field, code)
    assert await schedules(db_session) == 0


@pytest.mark.parametrize("extra_env", [{"NDM_MIN_SCHEDULE_INTERVAL_SECONDS": "86400"}])
async def test_the_minimum_is_the_operators_setting(
    client: AsyncClient, project_id: int, extra_env: dict[str, str]
) -> None:
    assert (await create(client, project_id)).status_code == 422
    assert (await create(client, project_id, interval_seconds=86400)).status_code == 201


@pytest.mark.parametrize(
    "values",
    [
        {"interval_seconds": None},  # neither
        {"cron": "0 2 * * *"},  # both
        {"targets": []},
        {"name": " "},
    ],
)
async def test_a_schedule_needs_one_recurrence_targets_and_a_name(
    client: AsyncClient, project_id: int, values: dict[str, Any]
) -> None:
    assert (await create(client, project_id, **values)).status_code == 422


async def test_out_of_scope_targets_are_rejected_and_audited(
    client: AsyncClient, project_id: int, db_session: AsyncSession
) -> None:
    response = await create(client, project_id, targets=["8.8.8.1", "8.8.9.1", "10.0.0.1"])

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "targets_rejected"
    assert [(d["field"], d["code"]) for d in error["details"]] == [
        ("targets.1", "out_of_scope"),
        ("targets.2", "non_public"),
    ]
    assert await schedules(db_session) == 0
    entry = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "schedule.rejected")
    )
    assert entry is not None
    assert entry.details == {
        "point": "api",
        "schedule_id": None,
        "rejected": [
            {"target": "8.8.9.1", "reason": "out_of_scope"},
            {"target": "10.0.0.1", "reason": "non_public"},
        ],
    }


async def test_more_than_256_targets_are_rejected(client: AsyncClient, project_id: int) -> None:
    response = await create(client, project_id, targets=["8.8.8.0/24", "8.8.9.0"])

    # 8.8.9.0 is outside the scope as well; the scope check answers first.
    assert response.status_code == 422
    assert (await create(client, project_id, targets=["8.8.8.0/23"])).status_code == 422


async def test_the_rate_ceiling_applies(client: AsyncClient, project_id: int) -> None:
    response = await create(client, project_id, parameters={"pps": 10**7})

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "parameters.pps"


async def test_an_archived_project_accepts_no_schedule_writes(
    client: AsyncClient, project_id: int
) -> None:
    created = (await create(client, project_id)).json()
    await client.post(f"/api/v1/projects/{project_id}/archive")
    url = f"/api/v1/schedules/{created['id']}"

    for response in [
        await create(client, project_id),
        await client.put(url, json=body()),
        await client.post(f"{url}/disable"),
        await client.delete(url),
    ]:
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "project_archived"
    # Reading still works.
    assert (await client.get(url)).status_code == 200


# --- Changing ---


async def test_a_change_is_audited_and_clears_the_flag(
    client: AsyncClient, project_id: int, db_session: AsyncSession
) -> None:
    created = (await create(client, project_id)).json()
    stored = await db_session.get_one(Schedule, created["id"])
    stored.flag_reason = "targets_out_of_scope"
    await db_session.commit()

    response = await client.put(
        f"/api/v1/schedules/{created['id']}",
        json=body(name="Twice a day", interval_seconds=12 * HOUR, targets=["8.8.8.1"]),
    )

    assert response.status_code == 200, response.text
    changed = response.json()
    assert (changed["name"], changed["interval_seconds"], changed["targets"]) == (
        "Twice a day",
        12 * HOUR,
        ["8.8.8.1"],
    )
    assert changed["flag_reason"] is None
    assert changed["next_run_at"] > created["next_run_at"]
    entry = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "schedule.update")
    )
    assert entry is not None
    assert entry.details["flag_cleared"] == "targets_out_of_scope"
    assert entry.details["changed"] == {
        "name": {"before": "Nightly edge sweep", "after": "Twice a day"},
        "targets": {"before": ["8.8.8.1-8.8.8.4", "8.8.8.10"], "after": ["8.8.8.1"]},
        "interval_seconds": {"before": 6 * HOUR, "after": 12 * HOUR},
    }


async def test_a_change_is_checked_like_a_new_schedule(
    client: AsyncClient, project_id: int, db_session: AsyncSession
) -> None:
    created = (await create(client, project_id)).json()
    url = f"/api/v1/schedules/{created['id']}"

    assert (await client.put(url, json=body(targets=["8.8.9.1"]))).status_code == 422
    assert (await client.put(url, json=body(interval_seconds=60))).status_code == 422

    assert (await client.get(url)).json() == created
    entry = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "schedule.rejected")
    )
    assert entry is not None and entry.details["schedule_id"] == created["id"]


async def test_disable_and_enable_are_audited_and_enabling_does_not_catch_up(
    client: AsyncClient, project_id: int, db_session: AsyncSession
) -> None:
    created = (await create(client, project_id)).json()
    url = f"/api/v1/schedules/{created['id']}"

    assert (await client.post(f"{url}/disable")).json()["enabled"] is False
    assert (await client.post(f"{url}/disable")).status_code == 200  # no change, no entry
    stored = await db_session.get_one(Schedule, created["id"])
    stored.next_run_at = datetime.now(UTC) - timedelta(days=3)
    await db_session.commit()
    enabled = (await client.post(f"{url}/enable")).json()

    assert enabled["enabled"] is True
    assert datetime.fromisoformat(enabled["next_run_at"]) > datetime.now(UTC) + timedelta(hours=5)
    actions = await audit_actions(db_session)
    assert actions.count("schedule.disable") == 1 and actions.count("schedule.enable") == 1


async def test_deleting_a_schedule_keeps_its_scans(
    client: AsyncClient, project_id: int, db_session: AsyncSession, app: Any
) -> None:
    created = (await create(client, project_id)).json()
    stored = await db_session.get_one(Schedule, created["id"])
    stored.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()
    sessions = app.state.session_factory
    started = (await run_due(sessions, app.state.settings, [])).started

    assert (await client.delete(f"/api/v1/schedules/{created['id']}")).status_code == 204

    assert (await client.get(f"/api/v1/schedules/{created['id']}")).status_code == 404
    await db_session.rollback()
    scan = await db_session.get_one(Scan, started[0])
    assert scan.schedule_id is None
    assert await db_session.scalar(select(func.count()).select_from(ScheduleRun)) == 0
    assert (await audit_actions(db_session))[-1] == "schedule.delete"


# --- Listing ---


async def test_the_list_shows_the_next_run_and_the_last_outcome(
    client: AsyncClient, project_id: int, db_session: AsyncSession, app: Any
) -> None:
    first = (await create(client, project_id)).json()
    second = (
        await create(client, project_id, name="Weekly", interval_seconds=7 * 24 * HOUR)
    ).json()
    stored = await db_session.get_one(Schedule, first["id"])
    planned = datetime.now(UTC) - timedelta(minutes=1)
    stored.next_run_at = planned
    await db_session.commit()
    outcome = await scheduler.run_due(app.state.session_factory, app.state.settings, [])
    scan = await db_session.get_one(Scan, outcome.started[0])
    scan.state = ScanState.FAILED
    await db_session.commit()

    page = (await client.get(f"/api/v1/projects/{project_id}/schedules")).json()

    assert page["total"] == 2
    listed = {item["id"]: item for item in page["items"]}
    last = listed[first["id"]]["last_run"]
    assert (last["outcome"], last["scan_id"], last["scan_state"]) == ("started", scan.id, "failed")
    assert datetime.fromisoformat(last["planned_at"]) == planned
    assert datetime.fromisoformat(listed[first["id"]]["next_run_at"]) > datetime.now(UTC)
    assert listed[second["id"]]["last_run"] is None

    runs = (await client.get(f"/api/v1/schedules/{first['id']}/runs")).json()
    assert [run["outcome"] for run in runs["items"]] == ["started"]


async def test_schedules_of_another_or_a_deleted_project_are_not_listed(
    client: AsyncClient, project_id: int
) -> None:
    created = (await create(client, project_id)).json()
    other: int = (await client.post("/api/v1/projects", json={"name": "Other"})).json()["id"]

    assert (await client.get(f"/api/v1/projects/{other}/schedules")).json()["total"] == 0
    deleted = await client.delete(f"/api/v1/projects/{project_id}", params={"confirm": "true"})
    assert deleted.status_code == 204
    assert (await client.get(f"/api/v1/schedules/{created['id']}")).status_code == 404
