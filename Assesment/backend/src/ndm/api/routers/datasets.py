"""Reference datasets: versions, ages and status, and a trigger for an update.

The datasets are kept by the orchestrator. The API reads what it recorded,
and passes on the analyst's wish for an update; it downloads nothing itself
and holds no licence key.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Path, Request
from sqlalchemy import select

from ndm.api.commands import CommandPublisher
from ndm.api.deps import CurrentSessionDep, DbDep
from ndm.api.errors import ApiError
from ndm.api.schemas.datasets import DatasetOut
from ndm.core.messages import DatasetUpdateRequested
from ndm.db.audit import record_audit
from ndm.db.models import ReferenceDataset

router = APIRouter(prefix="/datasets", tags=["datasets"])


def _dataset_out(row: ReferenceDataset, now: datetime) -> DatasetOut:
    return DatasetOut(
        name=row.name,
        version=row.version,
        status=row.status,
        detail=row.detail,
        loaded_at=row.loaded_at,
        age_seconds=int((now - row.loaded_at).total_seconds()) if row.loaded_at else None,
        checked_at=row.checked_at,
        update_requested=row.update_requested_at is not None,
    )


@router.get("")
async def list_datasets(db: DbDep) -> list[DatasetOut]:
    """Every reference dataset of the installation, in a fixed order."""
    now = datetime.now(UTC)
    rows = await db.scalars(select(ReferenceDataset).order_by(ReferenceDataset.id))
    return [_dataset_out(row, now) for row in rows]


@router.post("/{name}/update", status_code=202)
async def request_update(
    name: Annotated[str, Path(pattern="^[a-z0-9-]{1,64}$")],
    request: Request,
    current: CurrentSessionDep,
    db: DbDep,
) -> DatasetOut:
    """Ask for a dataset to be updated now. The orchestrator carries it out
    within seconds; the result shows in the dataset's version and detail."""
    row = await db.scalar(
        select(ReferenceDataset).where(ReferenceDataset.name == name).with_for_update()
    )
    if row is None:
        raise ApiError(404, "not_found", "There is no such dataset.")
    now = datetime.now(UTC)
    if row.update_requested_at is None:
        row.update_requested_at = now
        await record_audit(
            db,
            actor=current.account.username,
            action="dataset.update_requested",
            object_type="dataset",
            object_id=row.name,
        )
    result = _dataset_out(row, now)
    # The request is in the database first; the command only makes it come sooner.
    await db.commit()
    publisher: CommandPublisher = request.app.state.commands
    await publisher.publish(DatasetUpdateRequested(dataset=row.name))
    return result
