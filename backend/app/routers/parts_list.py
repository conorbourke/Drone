"""Phase 4: the recommended parts list of a project's draft or version (owner-scoped).

Every route takes ``?source=draft`` (default) or ``?source=<version id>``.

* ``GET  /api/projects/{id}/parts-list`` - the list, computed now (read-only).
* ``POST /api/projects/{id}/parts-list/recompute`` - compute and store the engine's picks.
* ``PUT  /api/projects/{id}/parts-list/roles/{role}`` ``{part_id, quantity?, locked?}`` - replace
  a role's part (locked by default, so the engine keeps it), then recompute and store.
* ``DELETE /api/projects/{id}/parts-list/roles/{role}/lock`` - hand the role back to the
  engine, then recompute and store.
* ``POST /api/projects/{id}/parts-list/refresh-all`` - queue a supplier-listing refresh for
  every part in the list on the worker (parts refreshed in the last hour are skipped).

The response shape is documented in docs/phases/PHASE4.md section 6.
"""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app import parts_service as svc
from app import suppliers
from app.deps import CurrentUser, DbSession, current_user
from app.engine.selection import ROLE_CATEGORIES, ROLE_LABELS, ROLE_ORDER
from app.jobs import AnalysisWorker
from app.models import DesignVersion, Part, Project, User
from app.routers.common import current_mission, current_parameters, not_found, owned_project
from app.routers.settings import effective_settings

router = APIRouter(prefix="/api", tags=["parts-list"], dependencies=[Depends(current_user)])

SOURCE_HELP = '"draft" (default) or the id of a saved version of this project.'


class RoleChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    part_id: int = Field(description="Catalogue part for this role.")
    quantity: int | None = Field(
        None,
        ge=1,
        le=500,
        description="Only for a battery built from cells: the number of cells (a multiple of "
        "the series count). Other roles take the quantity the design needs.",
    )
    locked: bool = Field(True, description="Keep this part when the list is recomputed.")


def _resolve(
    db: Session, user: User, project_id: int, source: str
) -> tuple[Project, DesignVersion | None, dict[str, Any], dict[str, Any]]:
    project = owned_project(db, user, project_id)
    source = (source or "draft").strip()
    if source == "draft":
        return (
            project,
            None,
            current_parameters(project.draft_parameters),
            current_mission(project.draft_mission),
        )
    try:
        version_id = int(source)
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='source must be "draft" or a version id.',
        ) from None
    version = db.get(DesignVersion, version_id)
    if version is None or version.project_id != project.id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="That version does not belong to this project.",
        )
    return (
        project,
        version,
        current_parameters(version.parameters),
        current_mission(version.mission),
    )


def _compute(
    request: Request,
    db: Session,
    user: User,
    project: Project,
    version: DesignVersion | None,
    parameters: dict[str, Any],
    mission: dict[str, Any],
) -> dict[str, Any]:
    settings_doc, _meta = effective_settings(db, user.id)
    try:
        return svc.compute(
            db,
            request.app.state.settings,
            user.id,
            project,
            version,
            parameters,
            mission,
            settings_doc,
        )
    except svc.PartsListError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None


def _store_and_return(
    request: Request,
    db: Session,
    user: User,
    project: Project,
    version: DesignVersion | None,
    parameters: dict[str, Any],
    mission: dict[str, Any],
) -> dict[str, Any]:
    result = _compute(request, db, user, project, version, parameters, mission)
    svc.persist_selection(
        db, user.id, project.id, version.id if version else None, result["selections"]
    )
    db.commit()
    # The stored record now matches what was computed.
    result["stored"] = {"in_sync": True, "stored_at": result["generated_at"]}
    return svc.public(result)


def _check_role(role: str, layout: str) -> None:
    if role not in ROLE_ORDER:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown role '{role}'. Roles: {', '.join(ROLE_ORDER)}.",
        )
    if role in ("cruise_motor", "pusher_prop") and layout != "quad_pusher":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="This layout has no pusher motor or pusher propeller.",
        )
    if role == "tilt_servo" and layout == "quad_pusher":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A quad + pusher layout has no tilt servos.",
        )


@router.get("/projects/{project_id}/parts-list")
def get_parts_list(
    project_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    source: str = Query("draft", description=SOURCE_HELP),
) -> dict[str, Any]:
    project, version, parameters, mission = _resolve(db, user, project_id, source)
    result = _compute(request, db, user, project, version, parameters, mission)
    return svc.public(result)


@router.post("/projects/{project_id}/parts-list/recompute")
def recompute_parts_list(
    project_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    source: str = Query("draft", description=SOURCE_HELP),
) -> dict[str, Any]:
    project, version, parameters, mission = _resolve(db, user, project_id, source)
    return _store_and_return(request, db, user, project, version, parameters, mission)


@router.put("/projects/{project_id}/parts-list/roles/{role}")
def set_role(
    project_id: int,
    role: str,
    body: RoleChoice,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    source: str = Query("draft", description=SOURCE_HELP),
) -> dict[str, Any]:
    project, version, parameters, mission = _resolve(db, user, project_id, source)
    _check_role(role, parameters["layout"])
    part = db.get(Part, body.part_id)
    if part is None:
        raise not_found("Part")
    allowed = ROLE_CATEGORIES[role]
    if part.category not in allowed:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{ROLE_LABELS[role]} must be a {' or '.join(allowed)} part, not a "
            f"{part.category}.",
        )
    quantity = body.quantity
    series = int(parameters["battery"]["cells_series"])
    if role == "battery" and part.category == "cell":
        if quantity is None:
            quantity = series * 2
        if quantity % series:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"A custom pack needs a multiple of {series} cells (the design's series "
                "count).",
            )
    elif role == "battery" and part.spec.get("cells_series") != series:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"This pack is {part.spec.get('cells_series')}S; the design is {series}S. "
            "Change the design's battery series count first.",
        )
    svc.set_lock(
        db,
        user.id,
        project.id,
        version.id if version else None,
        role,
        part,
        quantity,
        body.locked,
    )
    return _store_and_return(request, db, user, project, version, parameters, mission)


@router.delete("/projects/{project_id}/parts-list/roles/{role}/lock")
def unlock_role(
    project_id: int,
    role: str,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    source: str = Query("draft", description=SOURCE_HELP),
) -> dict[str, Any]:
    project, version, parameters, mission = _resolve(db, user, project_id, source)
    _check_role(role, parameters["layout"])
    svc.clear_lock(db, project.id, version.id if version else None, role)
    return _store_and_return(request, db, user, project, version, parameters, mission)


@router.post("/projects/{project_id}/parts-list/refresh-all", status_code=status.HTTP_202_ACCEPTED)
def refresh_all(
    project_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    source: str = Query("draft", description=SOURCE_HELP),
) -> Any:
    settings = request.app.state.settings
    if not suppliers.is_available(settings):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": suppliers.MISSING_KEY_MESSAGE},
        )
    worker: AnalysisWorker | None = getattr(request.app.state, "analysis_worker", None)
    if worker is None or not worker.running:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "The worker is starting up. Try again in a moment."},
        )
    project, version, parameters, mission = _resolve(db, user, project_id, source)
    result = _compute(request, db, user, project, version, parameters, mission)
    ids: list[int] = []
    for row in result["roles"]:
        if row["part"] and row["part"]["id"] not in ids:
            ids.append(row["part"]["id"])
    queued, skipped = [], []
    for part_id in ids:
        part = db.get(Part, part_id)
        if part is None:
            continue
        name = f"{part.manufacturer} {part.model}"
        until = suppliers.rate_limited_until(part)
        if until is not None:
            skipped.append(
                {
                    "part_id": part.id,
                    "name": name,
                    "reason": "Refreshed less than an hour ago; next refresh after "
                    f"{until:%H:%M} UTC.",
                }
            )
            continue
        suppliers.mark_queued(db, part)
        queued.append({"part_id": part.id, "name": name})
    db.commit()
    for item in queued:
        with contextlib.suppress(RuntimeError):
            worker.submit_refresh(item["part_id"])
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "queued": queued,
            "skipped": skipped,
            "message": f"{len(queued)} part(s) queued for a listing refresh"
            + (f", {len(skipped)} skipped." if skipped else "."),
        },
    )
