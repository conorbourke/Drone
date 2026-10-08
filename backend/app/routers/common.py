"""Helpers shared by the routers: owner-scoped lookups and document serialisation."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import DesignVersion, Project, User
from app.schemas.design import DesignParameters
from app.schemas.migrate import upgrade_mission, upgrade_parameters
from app.schemas.mission import Mission


def not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{what} not found.")


def conflict(message: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail=message)


def owned_project(db: Session, user: User, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.owner_id != user.id:
        raise not_found("Project")
    return project


def owned_version(db: Session, user: User, version_id: int) -> DesignVersion:
    version = db.get(DesignVersion, version_id)
    if version is None or version.owner_id != user.id:
        raise not_found("Version")
    return version


def current_parameters(doc: dict[str, Any]) -> dict[str, Any]:
    """A stored design document upgraded to the current schema, defaults filled in."""
    return DesignParameters.model_validate(upgrade_parameters(doc)).model_dump()


def current_mission(doc: dict[str, Any]) -> dict[str, Any]:
    return Mission.model_validate(upgrade_mission(doc)).model_dump()


def draft_payload(project: Project) -> dict[str, Any]:
    return {
        "parameters": current_parameters(project.draft_parameters),
        "mission": current_mission(project.draft_mission),
        "based_on_version_id": project.draft_based_on_version_id,
        "updated_at": project.draft_updated_at,
    }


def version_payload(version: DesignVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "project_id": version.project_id,
        "number": version.number,
        "name": version.name,
        "notes": version.notes,
        "parameters": current_parameters(version.parameters),
        "mission": current_mission(version.mission),
        "parent_version_id": version.parent_version_id,
        "created_at": version.created_at,
    }
