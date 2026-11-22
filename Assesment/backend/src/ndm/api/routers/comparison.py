"""Comparison of two scans (FR-HIS-2 to FR-HIS-5; architecture 11.3).

Any two scans of a project can be compared. The diff is computed on request
by the same function as the automatic diff of scheduled scans, and is not
stored. An automatic diff is stored, and is returned as it was detected, also
after later scans.
"""

from typing import Annotated

from fastapi import APIRouter, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.api import export
from ndm.api.deps import DbDep
from ndm.api.errors import ApiError
from ndm.api.routers.scans import _project, _scan
from ndm.api.schemas.comparison import BaselineOut, ChangeOut, DiffOut
from ndm.db.models import ChangeEvent, Project, Scan, ScanDiff, ScanState
from ndm.orchestrator import diff_phase
from ndm.orchestrator.diff import Diff, NotComparable, compute_diff

router = APIRouter(tags=["comparison"])

# ``format=csv`` or ``format=json`` gives the diff as a file (FR-EXP-2).
FormatQuery = Annotated[export.ExportFormat | None, Query(alias="format")]


async def _answer(
    db: AsyncSession, out: DiffOut, form: export.ExportFormat | None
) -> DiffOut | Response:
    if form is None:
        return out
    return export.response(await export.diff(db, out), form)


async def previous_scan(db: AsyncSession, scan: Scan) -> Scan | None:
    """The scan that ``scan`` is compared with when no other is named.

    For a scheduled run, the baseline of its automatic diff. Otherwise, and
    for the first run of a schedule, the latest earlier completed scan of the
    project (Q3).
    """
    if scan.schedule_id is not None:
        base = await diff_phase.baseline(db, scan)
        if base is not None:
            return base
    return await db.scalar(
        select(Scan)
        .where(
            Scan.project_id == scan.project_id,
            Scan.id < scan.id,
            Scan.state == ScanState.COMPLETED,
            Scan.deleted_at.is_(None),
        )
        .order_by(Scan.id.desc())
        .limit(1)
    )


def computed_out(diff: Diff) -> DiffOut:
    return DiffOut(
        id=None,
        stored=False,
        base_scan_id=diff.base_scan_id,
        target_scan_id=diff.target_scan_id,
        compared_ports=diff.compared_ports,
        not_comparable=[str(address) for address in diff.not_comparable],
        summary=diff.summary,
        changes=[
            ChangeOut(
                address=str(change.address),
                kind=change.kind,
                port=change.port,
                field=change.field,
                before=change.before,
                after=change.after,
                dataset_change=change.dataset_change,
            )
            for change in diff.changes
        ],
        created_at=None,
    )


async def stored_out(db: AsyncSession, diff: ScanDiff) -> DiffOut:
    events = await db.scalars(
        select(ChangeEvent).where(ChangeEvent.diff_id == diff.id).order_by(ChangeEvent.id)
    )
    return DiffOut(
        id=diff.id,
        stored=True,
        base_scan_id=diff.base_scan_id,
        target_scan_id=diff.target_scan_id,
        compared_ports=[(span.lower or 0, (span.upper or 0) - 1) for span in diff.compared_ports],
        not_comparable=[str(address) for address in diff.not_comparable],
        summary=diff.summary,
        changes=[
            ChangeOut(
                address=str(event.address),
                kind=event.kind,
                port=event.port,
                field=event.field,
                before=event.before,
                after=event.after,
                dataset_change=event.dataset_change,
            )
            for event in events
        ],
        created_at=diff.created_at,
    )


async def stored_diff(db: AsyncSession, diff_id: int) -> ScanDiff:
    """A stored diff, or 404. A diff of a deleted scan or project does not exist."""
    query = (
        select(ScanDiff)
        .join(Scan, Scan.id == ScanDiff.target_scan_id)
        .join(Project, Project.id == Scan.project_id)
        .where(ScanDiff.id == diff_id, Scan.deleted_at.is_(None), Project.deleted_at.is_(None))
    )
    diff = await db.scalar(query)
    if diff is None:
        raise ApiError(404, "not_found", "The diff does not exist.")
    return diff


@router.get("/projects/{project_id}/compare", response_model=DiffOut)
async def compare(
    project_id: int,
    db: DbDep,
    base: int = Query(description="The earlier scan."),
    target: int = Query(description="The later scan."),
    form: FormatQuery = None,
) -> DiffOut | Response:
    """The diff of two scans of the project, computed now and not stored."""
    project = await _project(db, project_id)
    base_scan, target_scan = await _scan(db, base), await _scan(db, target)
    if base_scan.project_id != project.id or target_scan.project_id != project.id:
        raise ApiError(422, "not_comparable", "Only scans of this project can be compared.")
    try:
        diff = await compute_diff(db, base_scan.id, target_scan.id)
    except NotComparable as error:
        raise ApiError(422, "not_comparable", str(error)) from error
    return await _answer(db, computed_out(diff), form)


@router.get("/diffs/{diff_id}", response_model=DiffOut)
async def get_diff(diff_id: int, db: DbDep, form: FormatQuery = None) -> DiffOut | Response:
    """An automatic diff, as it was detected."""
    return await _answer(db, await stored_out(db, await stored_diff(db, diff_id)), form)


@router.get("/scans/{scan_id}/diff", response_model=DiffOut)
async def get_scan_diff(scan_id: int, db: DbDep, form: FormatQuery = None) -> DiffOut | Response:
    """The automatic diff of a scheduled scan against its baseline, if it has one."""
    scan = await _scan(db, scan_id)
    diff = await db.scalar(select(ScanDiff).where(ScanDiff.target_scan_id == scan.id))
    if diff is None:
        raise ApiError(404, "not_found", "The scan has no automatic diff.")
    return await _answer(db, await stored_out(db, diff), form)


@router.get("/scans/{scan_id}/baseline")
async def get_baseline(scan_id: int, db: DbDep) -> BaselineOut:
    """The scan the change-status filter compares this scan with."""
    scan = await _scan(db, scan_id)
    base = await previous_scan(db, scan)
    return BaselineOut(scan_id=scan.id, base_scan_id=base.id if base else None)
