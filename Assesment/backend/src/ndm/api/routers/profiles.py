"""Scan profiles: named parameter sets for manual scans and schedules (FR-CFG-8).

A profile is checked like a scan: the limits of each parameter, and the
operator's rate ceiling. Profiles belong to the installation, not to a
project. Every change is in the audit log, in the same transaction.
"""

from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ndm.api.deps import CurrentSessionDep, DbDep, SettingsDep
from ndm.api.errors import ApiError
from ndm.api.schemas.common import ErrorDetail
from ndm.api.schemas.profiles import ProfileIn, ProfileOut
from ndm.api.schemas.scans import ScanSettings
from ndm.core.settings import Settings
from ndm.db.audit import record_audit
from ndm.db.models import ScanProfile

router = APIRouter(tags=["profiles"])


def check_ceiling(parameters: ScanSettings, settings: Settings, field: str) -> None:
    """Reject a rate above the operator ceiling (FR-CFG-7)."""
    if parameters.pps > settings.global_rate_pps:
        raise ApiError(
            422,
            "validation_error",
            "The request is not valid.",
            [
                ErrorDetail(
                    field=field,
                    code="above_ceiling",
                    message=f"The rate ceiling is {settings.global_rate_pps} packets per second.",
                )
            ],
        )


def _out(profile: ScanProfile) -> ProfileOut:
    parameters = ScanSettings.model_validate(profile.parameters)
    return ProfileOut(
        id=profile.id,
        name=profile.name,
        parameters=parameters,
        port_count=len(parameters.ports()),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


async def _profile(db: DbDep, profile_id: int) -> ScanProfile:
    profile = await db.get(ScanProfile, profile_id)
    if profile is None:
        raise ApiError(404, "not_found", "The scan profile does not exist.")
    return profile


def _name_taken() -> ApiError:
    return ApiError(409, "name_taken", "A scan profile with this name exists already.")


@router.get("/scan-profiles")
async def list_profiles(db: DbDep) -> list[ProfileOut]:
    """Every profile, by name. There are few of them."""
    profiles = await db.scalars(select(ScanProfile).order_by(func.lower(ScanProfile.name)))
    return [_out(profile) for profile in profiles]


@router.post("/scan-profiles", status_code=201)
async def create_profile(
    body: ProfileIn, current: CurrentSessionDep, db: DbDep, settings: SettingsDep
) -> ProfileOut:
    check_ceiling(body.parameters, settings, "parameters.pps")
    profile = ScanProfile(name=body.name, parameters=body.parameters.model_dump(mode="json"))
    db.add(profile)
    try:
        await db.flush()
    except IntegrityError as error:
        raise _name_taken() from error
    await db.refresh(profile)
    await record_audit(
        db,
        actor=current.account.username,
        action="profile.create",
        object_type="scan_profile",
        object_id=profile.id,
        details={"name": profile.name, "parameters": profile.parameters},
    )
    await db.commit()
    return _out(profile)


@router.get("/scan-profiles/{profile_id}")
async def get_profile(profile_id: int, db: DbDep) -> ProfileOut:
    return _out(await _profile(db, profile_id))


@router.put("/scan-profiles/{profile_id}")
async def update_profile(
    profile_id: int, body: ProfileIn, current: CurrentSessionDep, db: DbDep, settings: SettingsDep
) -> ProfileOut:
    profile = await _profile(db, profile_id)
    check_ceiling(body.parameters, settings, "parameters.pps")
    before = {"name": profile.name, "parameters": profile.parameters}
    profile.name = body.name
    profile.parameters = body.parameters.model_dump(mode="json")
    profile.updated_at = datetime.now(UTC)
    try:
        await db.flush()
    except IntegrityError as error:
        raise _name_taken() from error
    await record_audit(
        db,
        actor=current.account.username,
        action="profile.update",
        object_type="scan_profile",
        object_id=profile.id,
        details={
            "before": before,
            "after": {"name": profile.name, "parameters": profile.parameters},
        },
    )
    await db.commit()
    return _out(profile)


@router.delete("/scan-profiles/{profile_id}", status_code=204)
async def delete_profile(profile_id: int, current: CurrentSessionDep, db: DbDep) -> None:
    """Scans and schedules made from the profile keep their own copy of the parameters."""
    profile = await _profile(db, profile_id)
    await record_audit(
        db,
        actor=current.account.username,
        action="profile.delete",
        object_type="scan_profile",
        object_id=profile.id,
        details={"name": profile.name},
    )
    await db.delete(profile)
    await db.commit()
