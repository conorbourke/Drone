"""Projects and their drafts."""

from __future__ import annotations

import copy

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db import utcnow
from app.defaults import DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION
from app.deps import AppSettings, CurrentUser, DbSession, current_user
from app.imaging import remove_project_files
from app.models import DesignVersion, Project, User
from app.routers.common import conflict, draft_payload, owned_project
from app.schemas.project import (
    DraftIn,
    DraftOut,
    ProjectCreate,
    ProjectListItem,
    ProjectOut,
    ProjectUpdate,
)

router = APIRouter(prefix="/api/projects", tags=["projects"], dependencies=[Depends(current_user)])


def _version_count(db: DbSession, project_id: int) -> int:
    return (
        db.scalar(
            select(func.count(DesignVersion.id)).where(DesignVersion.project_id == project_id)
        )
        or 0
    )


def _project_out(db: DbSession, project: Project) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        created_at=project.created_at,
        updated_at=project.updated_at,
        draft=DraftOut(**draft_payload(project)),
        version_count=_version_count(db, project.id),
        next_version_number=project.next_version_number,
    )


def _name_taken(db: DbSession, user: User, name: str, exclude_id: int | None = None) -> bool:
    stmt = select(Project.id).where(Project.owner_id == user.id, Project.name == name)
    if exclude_id is not None:
        stmt = stmt.where(Project.id != exclude_id)
    return db.scalar(stmt) is not None


def _name_conflict(name: str) -> HTTPException:
    return conflict(f"A project named '{name}' already exists.")


@router.get("", response_model=list[ProjectListItem])
def list_projects(db: DbSession, user: CurrentUser) -> list[ProjectListItem]:
    projects = db.scalars(
        select(Project).where(Project.owner_id == user.id).order_by(Project.updated_at.desc())
    ).all()
    counts: dict[int, int] = {}
    latest: dict[int, DesignVersion] = {}
    for version in db.scalars(
        select(DesignVersion)
        .where(DesignVersion.owner_id == user.id)
        .order_by(DesignVersion.number.desc())
    ):
        counts[version.project_id] = counts.get(version.project_id, 0) + 1
        latest.setdefault(version.project_id, version)
    out: list[ProjectListItem] = []
    for p in projects:
        v = latest.get(p.id)
        out.append(
            ProjectListItem(
                id=p.id,
                name=p.name,
                description=p.description,
                created_at=p.created_at,
                updated_at=p.updated_at,
                version_count=counts.get(p.id, 0),
                latest_version={"id": v.id, "number": v.number, "name": v.name} if v else None,
                next_version_number=p.next_version_number,
            )
        )
    return out


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreate, db: DbSession, user: CurrentUser, settings: AppSettings
) -> ProjectOut:
    if _name_taken(db, user, body.name):
        raise _name_conflict(body.name)
    project = Project(
        owner_id=user.id,
        name=body.name,
        description=body.description,
        draft_parameters=copy.deepcopy(DEFAULT_DESIGN_PARAMETERS),
        draft_mission=copy.deepcopy(DEFAULT_MISSION),
        draft_updated_at=utcnow(),
        next_version_number=1,
    )
    db.add(project)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _name_conflict(body.name) from None
    # SQLite can reuse the id of a deleted project; never let a new project inherit files
    # left behind by an interrupted delete.
    remove_project_files(settings.images_dir, project.id)
    return _project_out(db, project)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: DbSession, user: CurrentUser) -> ProjectOut:
    return _project_out(db, owned_project(db, user, project_id))


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int, body: ProjectUpdate, db: DbSession, user: CurrentUser
) -> ProjectOut:
    project = owned_project(db, user, project_id)
    if body.name is not None and body.name != project.name:
        if _name_taken(db, user, body.name, exclude_id=project.id):
            raise _name_conflict(body.name)
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _name_conflict(body.name or project.name) from None
    return _project_out(db, project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: int, db: DbSession, user: CurrentUser, settings: AppSettings
) -> None:
    """Delete a project. The DDL cascades its versions, images and readings; the image files
    are removed with the project's directory once the rows are gone."""
    project = owned_project(db, user, project_id)
    db.delete(project)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise conflict(
            "This project cannot be deleted because other records still refer to it."
        ) from None
    remove_project_files(settings.images_dir, project_id)


@router.get("/{project_id}/draft", response_model=DraftOut)
def get_draft(project_id: int, db: DbSession, user: CurrentUser) -> DraftOut:
    return DraftOut(**draft_payload(owned_project(db, user, project_id)))


@router.put("/{project_id}/draft", response_model=DraftOut)
def put_draft(project_id: int, body: DraftIn, db: DbSession, user: CurrentUser) -> DraftOut:
    """Save the draft. Only shape and sanity are validated: an over-limit take-off mass is
    accepted, because rejecting it would strand the autosaved edits."""
    project = owned_project(db, user, project_id)
    project.draft_parameters = body.parameters.model_dump()
    project.draft_mission = body.mission.model_dump()
    project.draft_updated_at = utcnow()
    db.commit()
    return DraftOut(**draft_payload(project))
