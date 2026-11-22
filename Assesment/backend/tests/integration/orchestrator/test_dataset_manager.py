"""The dataset manager: schedule, manual trigger, version, status, audit (FR-ENR-6)."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ndm.db.audit import record_audit
from ndm.db.models import AuditEvent, ReferenceDataset, RirDelegation
from ndm.orchestrator.datasets.manager import Dataset, DatasetError, DatasetManager

NOW = datetime(2026, 11, 16, 9, tzinfo=UTC)
DAY = timedelta(days=1)


@pytest.fixture
def sessions(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine, expire_on_commit=False)


class Loader:
    """A loader that answers with the versions it was given, one per call."""

    def __init__(self, *answers: str | None | Exception) -> None:
        self.answers = list(answers)
        self.seen: list[str | None] = []

    async def __call__(self, db: AsyncSession, current: str | None) -> str | None:
        self.seen.append(current)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def dataset(loader: Any, name: str = "test-data", **values: Any) -> Dataset:
    return Dataset(name=name, interval=values.pop("interval", DAY), update=loader, **values)


async def row(db: AsyncSession, name: str = "test-data") -> ReferenceDataset:
    await db.commit()
    return (
        await db.scalars(
            select(ReferenceDataset)
            .where(ReferenceDataset.name == name)
            .execution_options(populate_existing=True)
        )
    ).one()


async def audit(db: AsyncSession) -> list[tuple[str, str, str | None, dict[str, Any]]]:
    await db.commit()
    entries = await db.execute(
        select(AuditEvent.actor, AuditEvent.action, AuditEvent.object_id, AuditEvent.details)
        .where(AuditEvent.object_type == "dataset")
        .order_by(AuditEvent.id)
    )
    return [tuple(entry) for entry in entries]


# --- A successful update ---


async def test_update_sets_version_and_load_time_and_writes_one_audit_entry(
    db_session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    loader = Loader("2026-11-13")
    manager = DatasetManager(sessions, [dataset(loader)])

    assert await manager.run_due(NOW) == ["test-data"]

    stored = await row(db_session)
    assert (stored.version, stored.status, stored.detail) == ("2026-11-13", "ok", None)
    assert (stored.loaded_at, stored.checked_at) == (NOW, NOW)
    assert await audit(db_session) == [
        (
            "system",
            "dataset.update",
            "test-data",
            {"version": "2026-11-13", "previous_version": None},
        )
    ]
    # The loader was told that nothing was loaded yet.
    assert loader.seen == [None]


async def test_every_dataset_has_a_row_before_its_first_update(
    db_session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    manager = DatasetManager(
        sessions, [dataset(Loader(DatasetError("unreachable")), "a"), dataset(Loader(None), "b")]
    )

    await manager.run_due(NOW)

    assert [
        (r.name, r.version, r.status)
        for r in (await row(db_session, "a"), await row(db_session, "b"))
    ] == [
        ("a", None, "missing"),
        ("b", None, "missing"),
    ]


# --- The schedule and the manual trigger ---


async def test_dataset_is_updated_again_only_when_its_interval_has_passed(
    db_session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    loader = Loader("v1", "v2")
    manager = DatasetManager(sessions, [dataset(loader, interval=DAY)])

    await manager.run_due(NOW)
    assert await manager.run_due(NOW + timedelta(hours=23, minutes=59)) == []
    assert loader.seen == [None]

    assert await manager.run_due(NOW + DAY) == ["test-data"]
    assert loader.seen == [None, "v1"]
    stored = await row(db_session)
    assert (stored.version, stored.loaded_at) == ("v2", NOW + DAY)
    assert [entry[3] for entry in await audit(db_session)] == [
        {"version": "v1", "previous_version": None},
        {"version": "v2", "previous_version": "v1"},
    ]


async def test_requested_update_runs_at_once_and_the_request_is_cleared(
    db_session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    loader = Loader("v1", "v2")
    manager = DatasetManager(sessions, [dataset(loader)])
    await manager.run_due(NOW)

    # The analyst asks for an update: the API sets the request on the row.
    stored = await row(db_session)
    stored.update_requested_at = NOW + timedelta(minutes=1)
    await db_session.commit()

    assert await manager.run_due(NOW + timedelta(minutes=2)) == ["test-data"]
    stored = await row(db_session)
    assert (stored.version, stored.update_requested_at) == ("v2", None)
    assert await manager.run_due(NOW + timedelta(minutes=3)) == []


async def test_check_that_finds_nothing_new_writes_no_audit_entry(
    db_session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    manager = DatasetManager(sessions, [dataset(Loader("v1", None))])
    await manager.run_due(NOW)

    assert await manager.run_due(NOW + DAY) == []

    stored = await row(db_session)
    assert (stored.version, stored.status) == ("v1", "ok")
    assert (stored.loaded_at, stored.checked_at) == (NOW, NOW + DAY)
    assert len(await audit(db_session)) == 1


# --- A failed update keeps the previous version and records the failure ---


async def test_failed_update_keeps_the_previous_version_and_records_the_failure(
    db_session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    loader = Loader("v1", DatasetError("The checksum is not the published one."), "v2")
    manager = DatasetManager(sessions, [dataset(loader)])
    await manager.run_due(NOW)

    assert await manager.run_due(NOW + DAY) == []

    stored = await row(db_session)
    assert (stored.version, stored.status, stored.loaded_at) == ("v1", "ok", NOW)
    assert stored.detail == "The checksum is not the published one."
    assert stored.checked_at == NOW + DAY
    assert (await audit(db_session))[-1] == (
        "system",
        "dataset.update_failed",
        "test-data",
        {"detail": "The checksum is not the published one.", "version": "v1"},
    )

    # It is tried again at the next interval, not before; success clears the reason.
    assert await manager.run_due(NOW + DAY + timedelta(hours=1)) == []
    assert await manager.run_due(NOW + 2 * DAY) == ["test-data"]
    stored = await row(db_session)
    assert (stored.version, stored.detail) == ("v2", None)


async def test_first_update_that_fails_leaves_the_dataset_missing(
    db_session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    manager = DatasetManager(sessions, [dataset(Loader(RuntimeError("disk full")))])

    await manager.run_due(NOW)

    stored = await row(db_session)
    assert (stored.version, stored.status, stored.detail) == (
        None,
        "missing",
        "RuntimeError: disk full",
    )


async def test_one_failing_dataset_does_not_hold_up_the_others(
    db_session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    manager = DatasetManager(
        sessions,
        [dataset(Loader(RuntimeError("broken")), "first"), dataset(Loader("v1"), "second")],
    )

    assert await manager.run_due(NOW) == ["second"]
    assert (await row(db_session, "second")).version == "v1"


async def test_a_secret_in_an_error_is_not_stored(
    db_session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    loader = Loader(RuntimeError("GET https://42:s3cret-key@download.example failed"))
    manager = DatasetManager(sessions, [dataset(loader)], secrets=["s3cret-key", ""])

    await manager.run_due(NOW)

    stored = await row(db_session)
    assert stored.detail == "RuntimeError: GET https://42:[REDACTED]@download.example failed"
    assert "s3cret-key" not in str(await audit(db_session))


# --- The loader's rows, the version and the audit entry are one transaction ---


async def load_one_row(db: AsyncSession, current: str | None) -> str | None:
    db.add(RirDelegation(rir="test", ip_range=Range(1, 2), status="allocated"))
    await db.flush()
    return "v1"


async def test_loaded_rows_version_and_audit_entry_are_committed_together(
    db_session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await DatasetManager(sessions, [dataset(load_one_row)]).run_due(NOW)

    assert await db_session.scalar(select(func.count()).select_from(RirDelegation)) == 1
    assert (await row(db_session)).version == "v1"
    assert [entry[1] for entry in await audit(db_session)] == ["dataset.update"]


async def test_if_the_audit_entry_cannot_be_written_nothing_of_the_update_is_kept(
    db_session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refuse_the_update_entry(db: AsyncSession, **entry: Any) -> None:
        if entry["action"] == "dataset.update":
            raise RuntimeError("audit log unavailable")
        await record_audit(db, **entry)

    monkeypatch.setattr("ndm.orchestrator.datasets.manager.record_audit", refuse_the_update_entry)

    assert await DatasetManager(sessions, [dataset(load_one_row)]).run_due(NOW) == []

    # The loader's row went with the transaction, and no version was recorded.
    assert await db_session.scalar(select(func.count()).select_from(RirDelegation)) == 0
    stored = await row(db_session)
    assert (stored.version, stored.status) == (None, "missing")
    assert stored.detail == "RuntimeError: audit log unavailable"
    assert [entry[1] for entry in await audit(db_session)] == ["dataset.update_failed"]


# --- A dataset this installation cannot update ---


async def test_dataset_that_is_not_configured_is_never_tried(
    db_session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    loader = Loader("v1")
    manager = DatasetManager(sessions, [dataset(loader, not_configured="No key is configured.")])
    await manager.run_due(NOW)
    stored = await row(db_session)
    stored.update_requested_at = NOW
    await db_session.commit()

    assert await manager.run_due(NOW + DAY) == []

    stored = await row(db_session)
    assert (stored.status, stored.detail) == ("missing", "No key is configured.")
    assert (stored.checked_at, stored.update_requested_at) == (None, None)
    assert loader.seen == []
    assert await audit(db_session) == []


# --- Data that has outlived its licence ---


async def test_data_older_than_its_maximum_age_becomes_stale(
    db_session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    built = date(2026, 11, 13)
    failing = DatasetError("MaxMind refused the licence key.")
    loader = Loader(built.isoformat(), failing, failing, built.isoformat(), "2026-12-29")
    manager = DatasetManager(sessions, [dataset(loader, max_age=timedelta(days=30))])
    await manager.run_due(NOW)
    assert (await row(db_session)).status == "ok"

    # Updates fail from here on. On day 30 the data may still be used.
    last_good_day = datetime(2026, 12, 13, 9, tzinfo=UTC)
    await manager.run_due(last_good_day)
    assert (await row(db_session)).status == "ok"

    # A day later it may not, although the version is still recorded.
    await manager.run_due(last_good_day + DAY)
    stored = await row(db_session)
    assert (stored.version, stored.status) == ("2026-11-13", "stale")

    # A download of the same old build does not make it usable again...
    await manager.run_due(last_good_day + 2 * DAY)
    assert (await row(db_session)).status == "stale"

    # ...a new one does.
    await manager.run_due(datetime(2026, 12, 30, 9, tzinfo=UTC))
    stored = await row(db_session)
    assert (stored.version, stored.status) == ("2026-12-29", "ok")


async def test_staleness_is_noticed_even_when_no_update_is_due(
    db_session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    long_interval = timedelta(days=365)
    manager = DatasetManager(
        sessions,
        [dataset(Loader("2026-11-13"), interval=long_interval, max_age=timedelta(days=30))],
    )
    await manager.run_due(NOW)

    assert await manager.run_due(NOW + timedelta(days=60)) == []

    assert (await row(db_session)).status == "stale"


async def test_no_datasets_is_fine(sessions: async_sessionmaker[AsyncSession]) -> None:
    assert await DatasetManager(sessions, []).run_due(NOW) == []
