"""Projects and their attested scope (FR-PRJ-1 to FR-PRJ-6).

Every endpoint that changes data writes its audit entry and commits as its last
step, so the change and the entry are one transaction.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.api.deps import CurrentSessionDep, DbDep, SettingsDep
from ndm.api.errors import ApiError
from ndm.api.pagination import Page, PageParamsDep
from ndm.api.schemas.common import ErrorDetail
from ndm.api.schemas.projects import (
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    ScopeEntryOut,
    ScopeOut,
    ScopeReplace,
)
from ndm.core import scope as scope_rules
from ndm.db.audit import record_audit
from ndm.db.models import Account, Attestation, Project, ProjectState, ScopeEntry, ScopeEntryKind

router = APIRouter(prefix="/projects", tags=["projects"])


def _project_out(project: Project) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        state=project.state,
        created_at=project.created_at,
    )


async def _get_project(db: AsyncSession, project_id: int, *, for_write: bool = False) -> Project:
    """Load a project, or answer 404. A deleted project does not exist for any endpoint (I3).

    ``for_write`` locks the row, so two changes to one project run one after the
    other, and rejects an archived project, which is read-only.
    """
    query = select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
    if for_write:
        query = query.with_for_update()
    project = await db.scalar(query)
    if project is None:
        raise ApiError(404, "not_found", "The project does not exist.")
    if for_write and project.state is not ProjectState.ACTIVE:
        raise ApiError(409, "project_archived", "The project is archived and read-only.")
    return project


# --- Projects ---


@router.get("")
async def list_projects(db: DbDep, page: PageParamsDep) -> Page[ProjectOut]:
    visible = Project.deleted_at.is_(None)
    total = await db.scalar(select(func.count()).select_from(Project).where(visible))
    projects = await db.scalars(
        select(Project).where(visible).order_by(Project.id).limit(page.limit).offset(page.offset)
    )
    return Page(
        items=[_project_out(project) for project in projects],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("", status_code=201)
async def create_project(body: ProjectCreate, current: CurrentSessionDep, db: DbDep) -> ProjectOut:
    project = Project(name=body.name, description=body.description)
    db.add(project)
    await db.flush()
    await db.refresh(project)
    await record_audit(
        db,
        actor=current.account.username,
        action="project.create",
        object_type="project",
        object_id=project.id,
        details={"name": project.name},
    )
    await db.commit()
    return _project_out(project)


@router.get("/{project_id}")
async def get_project(project_id: int, db: DbDep) -> ProjectOut:
    return _project_out(await _get_project(db, project_id))


@router.patch("/{project_id}")
async def update_project(project_id: int, body: ProjectUpdate, db: DbDep) -> ProjectOut:
    """Rename or describe a project."""
    project = await _get_project(db, project_id, for_write=True)
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    await db.commit()
    return _project_out(project)


@router.post("/{project_id}/archive")
async def archive_project(project_id: int, current: CurrentSessionDep, db: DbDep) -> ProjectOut:
    """Archive a project. It stays readable and rejects every later change."""
    project = await _get_project(db, project_id, for_write=True)
    project.state = ProjectState.ARCHIVED
    await record_audit(
        db,
        actor=current.account.username,
        action="project.archive",
        object_type="project",
        object_id=project.id,
        details={"name": project.name},
    )
    await db.commit()
    return _project_out(project)


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: int, current: CurrentSessionDep, db: DbDep, confirm: bool = False
) -> None:
    """Delete a project. It is hidden from every endpoint at once; audit entries stay (I3)."""
    if not confirm:
        raise ApiError(
            400, "confirmation_required", "Deleting a project needs explicit confirmation."
        )
    # An archived project can be deleted, so the archive check of for_write is not used.
    project = await db.scalar(
        select(Project)
        .where(Project.id == project_id, Project.deleted_at.is_(None))
        .with_for_update()
    )
    if project is None:
        raise ApiError(404, "not_found", "The project does not exist.")
    project.deleted_at = datetime.now(UTC)
    await record_audit(
        db,
        actor=current.account.username,
        action="project.delete",
        object_type="project",
        object_id=project.id,
        details={"name": project.name},
    )
    await db.commit()


# --- Scope ---


async def _scope_out(db: AsyncSession, attestation: Attestation) -> ScopeOut:
    username = await db.scalar(select(Account.username).where(Account.id == attestation.account_id))
    entries = await db.scalars(
        select(ScopeEntry)
        .where(ScopeEntry.attestation_id == attestation.id)
        .order_by(ScopeEntry.id)
    )
    return ScopeOut(
        attestation_id=attestation.id,
        attested_at=attestation.created_at,
        attested_by=username,
        entries=[
            ScopeEntryOut(
                kind=entry.kind,
                text=entry.original_text,
                start=str(entry.start_address),
                end=str(entry.end_address),
            )
            for entry in entries
        ],
    )


@router.get("/{project_id}/scope")
async def get_scope(project_id: int, db: DbDep) -> ScopeOut:
    """The current scope with its attestation."""
    project = await _get_project(db, project_id)
    if project.current_attestation_id is None:
        return ScopeOut(attestation_id=None, attested_at=None, attested_by=None, entries=[])
    attestation = await db.get_one(Attestation, project.current_attestation_id)
    return await _scope_out(db, attestation)


@router.put("/{project_id}/scope")
async def replace_scope(
    project_id: int,
    body: ScopeReplace,
    request: Request,
    current: CurrentSessionDep,
    db: DbDep,
    settings: SettingsDep,
) -> ScopeOut:
    """Replace the scope. Each save is a new attestation; earlier ones are never changed."""
    if not body.authorized:
        raise ApiError(
            422,
            "attestation_required",
            "Confirm that you are authorized to scan the listed ranges.",
        )
    project = await _get_project(db, project_id, for_write=True)

    try:
        entries = scope_rules.validate_scope(
            body.entries,
            never_scan=request.app.state.never_scan,
            ceiling=settings.project_scope_ceiling,
            allow_non_public=settings.allow_non_public_targets,
        )
    except scope_rules.ScopeRejected as rejected:
        raise ApiError(
            422,
            "scope_rejected",
            "The scope was not saved.",
            details=[
                ErrorDetail(
                    field=None if error.index is None else f"entries.{error.index}",
                    code=error.code,
                    message=error.message,
                )
                for error in rejected.errors
            ],
        ) from rejected

    attestation = Attestation(project_id=project.id, account_id=current.account.id)
    db.add(attestation)
    await db.flush()
    await db.refresh(attestation)
    if entries:
        # One statement for all entries, with no identifiers returned.
        await db.execute(
            insert(ScopeEntry),
            [
                {
                    "attestation_id": attestation.id,
                    "kind": ScopeEntryKind(entry.kind),
                    "original_text": entry.text,
                    "start_address": entry.start,
                    "end_address": entry.end,
                }
                for entry in entries
            ],
        )
    project.current_attestation_id = attestation.id
    await record_audit(
        db,
        actor=current.account.username,
        action="scope.change",
        object_type="project",
        object_id=project.id,
        details={"attestation_id": attestation.id, "entries": [entry.text for entry in entries]},
    )
    await db.flush()
    result = await _scope_out(db, attestation)
    await db.commit()
    return result


@router.get("/{project_id}/scope/history")
async def scope_history(project_id: int, db: DbDep, page: PageParamsDep) -> Page[ScopeOut]:
    """Every attested version of the scope, newest first."""
    project = await _get_project(db, project_id)
    of_project = Attestation.project_id == project.id
    total = await db.scalar(select(func.count()).select_from(Attestation).where(of_project))
    attestations = await db.scalars(
        select(Attestation)
        .where(of_project)
        .order_by(Attestation.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return Page(
        items=[await _scope_out(db, attestation) for attestation in attestations.all()],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )
