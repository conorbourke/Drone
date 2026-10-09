"""Saved design versions: snapshot, lineage, duplicate, restore, delete."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import utcnow
from app.deps import AppSettings, CurrentUser, DbSession, current_user
from app.exports import delete_exports, remove_export_files
from app.models import Analysis, DesignVersion, Project
from app.parts_service import copy_selection
from app.patching import PatchError, apply_patch
from app.routers.common import (
    conflict,
    current_mission,
    current_parameters,
    draft_payload,
    owned_project,
    owned_version,
    version_payload,
)
from app.schemas.project import DraftOut
from app.schemas.version import (
    VERSION_NAME_MAX_LENGTH,
    DuplicateRequest,
    PatchBase,
    VersionCreate,
    VersionFromPatch,
    VersionListItem,
    VersionOut,
    VersionUpdate,
)

router = APIRouter(prefix="/api", tags=["versions"], dependencies=[Depends(current_user)])


def _name_taken(db: Session, project_id: int, name: str, exclude_id: int | None = None) -> bool:
    stmt = select(DesignVersion.id).where(
        DesignVersion.project_id == project_id, DesignVersion.name == name
    )
    if exclude_id is not None:
        stmt = stmt.where(DesignVersion.id != exclude_id)
    return db.scalar(stmt) is not None


def _name_conflict(name: str) -> HTTPException:
    return conflict(f"A version named '{name}' already exists in this project.")


def _claim_number(db: Session, project_id: int) -> int:
    """Atomically take the next version number from the project counter.

    Runs in the same transaction as the version insert, so numbers are monotonic and never
    reused after a delete.
    """
    new_value = db.execute(
        update(Project)
        .where(Project.id == project_id)
        .values(next_version_number=Project.next_version_number + 1)
        .returning(Project.next_version_number)
    ).scalar_one()
    return new_value - 1


def _insert_version(
    db: Session,
    project: Project,
    *,
    name: str,
    notes: str,
    parameters: dict[str, Any],
    mission: dict[str, Any],
    parent_version_id: int | None,
) -> DesignVersion:
    version = DesignVersion(
        project_id=project.id,
        owner_id=project.owner_id,
        name=name,
        notes=notes,
        number=_claim_number(db, project.id),
        parameters=parameters,
        mission=mission,
        parent_version_id=parent_version_id,
    )
    db.add(version)
    db.flush()
    return version


def _commit_or_conflict(db: Session, name: str) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _name_conflict(name) from None


@router.get("/projects/{project_id}/versions", response_model=list[VersionListItem])
def list_versions(project_id: int, db: DbSession, user: CurrentUser) -> list[VersionListItem]:
    project = owned_project(db, user, project_id)
    rows = db.scalars(
        select(DesignVersion)
        .where(DesignVersion.project_id == project.id)
        .order_by(DesignVersion.number.desc())
    ).all()
    return [VersionListItem.model_validate(v) for v in rows]


@router.post(
    "/projects/{project_id}/versions",
    response_model=VersionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_version(
    project_id: int, body: VersionCreate, db: DbSession, user: CurrentUser
) -> VersionOut:
    project = owned_project(db, user, project_id)
    if _name_taken(db, project.id, body.name):
        raise _name_conflict(body.name)
    if body.is_explicit:
        assert body.parameters is not None and body.mission is not None
        version = _insert_version(
            db,
            project,
            name=body.name,
            notes=body.notes,
            parameters=body.parameters.model_dump(),
            mission=body.mission.model_dump(),
            parent_version_id=None,
        )
    else:
        version = _insert_version(
            db,
            project,
            name=body.name,
            notes=body.notes,
            parameters=current_parameters(project.draft_parameters),
            mission=current_mission(project.draft_mission),
            parent_version_id=project.draft_based_on_version_id,
        )
        project.draft_based_on_version_id = version.id
        # Phase 4: the version keeps the draft's parts list (locked choices and picks).
        copy_selection(db, project.id, None, version.id)
    _commit_or_conflict(db, body.name)
    return VersionOut(**version_payload(version))


@router.post(
    "/projects/{project_id}/versions/from-patch",
    response_model=VersionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_version_from_patch(
    project_id: int, body: VersionFromPatch, db: DbSession, user: CurrentUser
) -> VersionOut:
    """ "Try as new version": apply a parameter patch (a recommendation, a scale result or an
    assistant proposal) to the draft or a saved version and save the result as a new version.
    Lineage: the parent is the base version, or the version the draft is based on. The draft
    itself is left untouched."""
    project = owned_project(db, user, project_id)
    if isinstance(body.base, PatchBase):
        base = db.get(DesignVersion, body.base.version_id)
        if base is None or base.project_id != project.id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="The base version does not belong to this project.",
            )
        parameters, mission = current_parameters(base.parameters), current_mission(base.mission)
        parent_id: int | None = base.id
    else:
        parameters = current_parameters(project.draft_parameters)
        mission = current_mission(project.draft_mission)
        parent_id = project.draft_based_on_version_id
    try:
        parameters, mission = apply_patch(parameters, mission, body.patch)
    except PatchError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    if _name_taken(db, project.id, body.name):
        raise _name_conflict(body.name)
    version = _insert_version(
        db,
        project,
        name=body.name,
        notes=body.notes,
        parameters=parameters,
        mission=mission,
        parent_version_id=parent_id,
    )
    # Phase 4: the owner's locked parts carry over; the engine picks the rest for the patch.
    copy_selection(
        db,
        project.id,
        body.base.version_id if isinstance(body.base, PatchBase) else None,
        version.id,
        locked_only=True,
    )
    _commit_or_conflict(db, body.name)
    return VersionOut(**version_payload(version))


@router.get("/versions/{version_id}", response_model=VersionOut)
def get_version(version_id: int, db: DbSession, user: CurrentUser) -> VersionOut:
    return VersionOut(**version_payload(owned_version(db, user, version_id)))


@router.patch("/versions/{version_id}", response_model=VersionOut)
def update_version(
    version_id: int, body: VersionUpdate, db: DbSession, user: CurrentUser
) -> VersionOut:
    version = owned_version(db, user, version_id)
    if body.name is not None and body.name != version.name:
        if _name_taken(db, version.project_id, body.name, exclude_id=version.id):
            raise _name_conflict(body.name)
        version.name = body.name
    if body.notes is not None:
        version.notes = body.notes
    _commit_or_conflict(db, body.name or version.name)
    return VersionOut(**version_payload(version))


def _copy_name(db: Session, project_id: int, base_name: str) -> str:
    """'<name> (copy)', then '<name> (copy 2)', '<name> (copy 3)', ... until unused.

    The base is shortened so the result always fits the 200-character name limit.
    """
    n = 1
    while True:
        suffix = " (copy)" if n == 1 else f" (copy {n})"
        base = base_name[: VERSION_NAME_MAX_LENGTH - len(suffix)].rstrip()
        candidate = f"{base}{suffix}"
        if not _name_taken(db, project_id, candidate):
            return candidate
        n += 1


@router.post(
    "/versions/{version_id}/duplicate",
    response_model=VersionOut,
    status_code=status.HTTP_201_CREATED,
)
def duplicate_version(
    version_id: int, db: DbSession, user: CurrentUser, body: DuplicateRequest | None = None
) -> VersionOut:
    source = owned_version(db, user, version_id)
    project = owned_project(db, user, source.project_id)
    explicit_name = body.name if body is not None else None
    if explicit_name is not None and _name_taken(db, project.id, explicit_name):
        raise _name_conflict(explicit_name)
    # Two overlapping duplicates of the same version can pick the same "(copy N)" name;
    # the unique constraint catches that and the loser simply takes the next suffix.
    for _attempt in range(5):
        name = explicit_name or _copy_name(db, project.id, source.name)
        copy = _insert_version(
            db,
            project,
            name=name,
            notes=source.notes,
            parameters=current_parameters(source.parameters),
            mission=current_mission(source.mission),
            parent_version_id=source.id,
        )
        copy_selection(db, project.id, source.id, copy.id)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if explicit_name is not None:
                raise _name_conflict(explicit_name) from None
            continue
        return VersionOut(**version_payload(copy))
    raise conflict("Could not find an unused name for the copy. Please try again.")


@router.post("/versions/{version_id}/restore", response_model=DraftOut)
def restore_version(version_id: int, db: DbSession, user: CurrentUser) -> DraftOut:
    version = owned_version(db, user, version_id)
    project = owned_project(db, user, version.project_id)
    project.draft_parameters = current_parameters(version.parameters)
    project.draft_mission = current_mission(version.mission)
    project.draft_based_on_version_id = version.id
    project.draft_updated_at = utcnow()
    # Phase 4: the draft takes the version's parts list with it.
    copy_selection(db, project.id, version.id, None)
    db.commit()
    return DraftOut(**draft_payload(project))


@router.delete(
    "/versions/{version_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    responses={409: {"description": "The version still has analyses (or later records)"}},
)
def delete_version(
    version_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
    with_analyses: bool = False,
) -> Any:
    """Delete a version. The DDL clears the draft pointer and any child's parent pointer
    (ON DELETE SET NULL). Analyses reference versions with ON DELETE RESTRICT: without
    ``with_analyses=true`` a version that has analyses answers 409 with a plain message and
    ``analyses`` (the count); with it, the analyses are deleted first, then the version."""
    version = owned_version(db, user, version_id)
    count = db.scalar(
        select(func.count()).select_from(Analysis).where(Analysis.version_id == version.id)
    )
    if count and not with_analyses:
        noun = "analysis" if count == 1 else "analyses"
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": f"Version {version.number} has {count} saved {noun}. Delete "
                f"{'it' if count == 1 else 'them'} together with the version, or keep the "
                "version.",
                "analyses": count,
            },
        )
    if count:
        db.execute(delete(Analysis).where(Analysis.version_id == version.id))
    # Exports are reproducible and never block the delete (ON DELETE CASCADE); their rows go
    # here so a running one is stopped, and their files once the delete is committed.
    export_ids = delete_exports(
        db, settings, getattr(request.app.state, "analysis_worker", None), version_id=version.id
    )
    db.delete(version)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise conflict(
            f"Version {version.number} is still referenced by other records (for example "
            "flight logs or calibrations) and cannot be deleted."
        ) from None
    for export_id in export_ids:
        remove_export_files(settings, export_id)
