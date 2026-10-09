"""Saved design versions: snapshot, lineage, duplicate, restore, delete."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import utcnow
from app.deps import CurrentUser, DbSession, current_user
from app.models import DesignVersion, Project
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
    VersionCreate,
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
    db.commit()
    return DraftOut(**draft_payload(project))


@router.delete("/versions/{version_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_version(version_id: int, db: DbSession, user: CurrentUser) -> None:
    """Delete a version. The DDL clears the draft pointer and any child's parent pointer
    (ON DELETE SET NULL); a later-phase RESTRICT reference turns into a 409."""
    version = owned_version(db, user, version_id)
    db.delete(version)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise conflict(
            f"Version {version.number} is still referenced by other records (for example "
            "flight logs or calibrations) and cannot be deleted."
        ) from None
