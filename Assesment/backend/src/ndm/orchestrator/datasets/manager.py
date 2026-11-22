"""The dataset manager: when each reference dataset is updated, and what is
recorded about it (FR-ENR-6).

A dataset is a name, an interval and a function that updates it. The manager
decides when the function runs, and keeps the ``reference_dataset`` row: the
version, when it was loaded, when it was last checked and why the last update
failed. Loaders plug in by being listed; the manager knows nothing about what
they download.

Status of a dataset:

- ``missing``: there is no data.
- ``ok``: there is data and it may be used.
- ``stale``: there is data, but it is older than its licence allows.

A failed update leaves the version and the status as they were, and puts the
reason into ``detail``.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ndm.db.audit import SYSTEM_ACTOR, record_audit
from ndm.db.models import ReferenceDataset

log = structlog.get_logger(__name__)

STATUS_MISSING = "missing"
STATUS_OK = "ok"
STATUS_STALE = "stale"

DETAIL_LENGTH = 500


class DatasetError(Exception):
    """An update failed for a reason the operator can read as it is."""


class NotConfigured(Exception):
    """There is nothing to update from, for example no file from the operator.

    Not a failure: the reason is shown with the dataset, and nothing else happens.
    """


Update = Callable[[AsyncSession, str | None], Awaitable[str | None]]
"""Updates one dataset. It gets a session and the version that is loaded now.

It returns the new version, or None if there is nothing new, and raises if the
update failed. What it writes through the session is committed together with
the new version and the audit entry, or not at all.
"""


@dataclass(frozen=True)
class Dataset:
    name: str
    interval: timedelta
    update: Update
    not_configured: str | None = None
    """Why this installation cannot update the dataset. It is then never tried."""
    max_age: timedelta | None = None
    """Data older than this is stale. The version must then be an ISO date."""


class DatasetManager:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        datasets: Sequence[Dataset],
        secrets: Sequence[str] = (),
    ) -> None:
        self._sessions = sessions
        self._datasets = list(datasets)
        self._secrets = [secret for secret in secrets if secret]

    async def run_due(self, now: datetime | None = None) -> list[str]:
        """Update every dataset that is due. Returns the names of those that changed.

        A dataset is due when it was never checked, when its interval has
        passed since the last check, or when an update was requested. This is
        the one entry point; the orchestrator calls it in a loop.
        """
        now = now or datetime.now(UTC)
        due: list[Dataset] = []
        async with self._sessions() as db:
            for dataset in self._datasets:
                row = await self._row(db, dataset)
                if dataset.not_configured is not None:
                    row.detail = dataset.not_configured
                    row.update_requested_at = None
                elif (
                    row.update_requested_at is not None
                    or row.checked_at is None
                    or now - row.checked_at >= dataset.interval
                ):
                    due.append(dataset)
                _set_age(dataset, row, now)
            await db.commit()

        changed = []
        for dataset in due:
            if await self._update(dataset, now):
                changed.append(dataset.name)
        return changed

    async def _row(self, db: AsyncSession, dataset: Dataset) -> ReferenceDataset:
        """The dataset's row, locked. Created on first use."""
        await db.execute(
            insert(ReferenceDataset).values(name=dataset.name).on_conflict_do_nothing()
        )
        return (
            await db.scalars(
                select(ReferenceDataset)
                .where(ReferenceDataset.name == dataset.name)
                .with_for_update()
            )
        ).one()

    async def _update(self, dataset: Dataset, now: datetime) -> bool:
        async with self._sessions() as db:
            try:
                current = await db.scalar(
                    select(ReferenceDataset.version).where(ReferenceDataset.name == dataset.name)
                )
                version = await dataset.update(db, current)
                row = await self._row(db, dataset)
                row.checked_at = now
                row.update_requested_at = None
                row.detail = None
                if version is not None:
                    await record_audit(
                        db,
                        actor=SYSTEM_ACTOR,
                        action="dataset.update",
                        object_type="dataset",
                        object_id=dataset.name,
                        details={"version": version, "previous_version": row.version},
                    )
                    row.version = version
                    row.loaded_at = now
                    row.status = STATUS_OK
                _set_age(dataset, row, now)
                # The loader's rows, the version and the audit entry: together or not at all.
                await db.commit()
            except NotConfigured as reason:
                await db.rollback()
                row = await self._row(db, dataset)
                row.checked_at = now
                row.update_requested_at = None
                row.detail = str(reason)
                await db.commit()
                return False
            except Exception as error:
                await db.rollback()
                detail = self._describe(error)
                log.warning(
                    "dataset_update_failed",
                    dataset=dataset.name,
                    detail=detail,
                    exc_info=not isinstance(error, DatasetError),
                )
                # The previous version stays in use; the failure is recorded.
                row = await self._row(db, dataset)
                row.checked_at = now
                row.update_requested_at = None
                row.detail = detail
                await record_audit(
                    db,
                    actor=SYSTEM_ACTOR,
                    action="dataset.update_failed",
                    object_type="dataset",
                    object_id=dataset.name,
                    details={"detail": detail, "version": row.version},
                )
                await db.commit()
                return False
        if version is not None:
            log.info("dataset_updated", dataset=dataset.name, version=version)
        return version is not None

    def _describe(self, error: Exception) -> str:
        """The reason of a failure, for the operator. No secret gets into it."""
        text = str(error) if isinstance(error, DatasetError) else f"{type(error).__name__}: {error}"
        for secret in self._secrets:
            text = text.replace(secret, "[REDACTED]")
        return text[:DETAIL_LENGTH]


def _set_age(dataset: Dataset, row: ReferenceDataset, now: datetime) -> None:
    """Mark data that has outlived its licence as stale, and newer data as usable."""
    if dataset.max_age is None or row.version is None:
        return
    too_old = date.fromisoformat(row.version) + dataset.max_age < now.date()
    row.status = STATUS_STALE if too_old else STATUS_OK
