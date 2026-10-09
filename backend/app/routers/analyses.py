"""Full analyses and scale-to-weight jobs (docs/phases/PHASE3.md section 3).

``POST`` stores a row with ``status: "queued"`` and answers 202 at once; the single analysis
worker (``app.jobs``) runs it and writes progress to the row; the browser polls
``GET /api/analyses/{id}``. Identical inputs (same ``inputs_hash``: parameters, mission, the
settings document, the engine and job versions, and for scale jobs the target mass) reuse a
finished analysis instead of running again; a matching job still queued or running for the same
project and source is returned as is.
"""

from __future__ import annotations

import contextlib
import hashlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import parts_service
from app.db import utcnow
from app.deps import CurrentUser, DbSession, current_user
from app.engine.analysis import ENGINE_VERSION
from app.flight_data import analysis_calibration
from app.jobs import JOB_VERSION, AnalysisWorker, canonical_json
from app.models import Analysis, DesignVersion, Project, User
from app.routers.common import current_mission, current_parameters, not_found, owned_project
from app.routers.settings import effective_settings
from app.schemas.analysis import (
    AnalysisCreate,
    AnalysisListItem,
    AnalysisOut,
    ScaleCreate,
    Source,
    VersionSource,
)

router = APIRouter(prefix="/api", tags=["analyses"], dependencies=[Depends(current_user)])

HEADLINE_KEYS = (
    "takeoff_mass",
    "endurance_cruise",
    "range",
    "cruise_power",
    "hover_power",
    "static_margin_min_payload",
)
REUSED_STAGE = "Reused an identical earlier analysis"


def _worker(request: Request) -> AnalysisWorker | None:
    return getattr(request.app.state, "analysis_worker", None)


def _headline(row: Analysis) -> dict[str, Any] | None:
    if row.result is None or row.status != "done":
        return None
    result = row.result
    if row.kind == "scale":
        result = result.get("analysis") or {}
    summary = result.get("summary") or {}
    out = {k: summary[k] for k in HEADLINE_KEYS if k in summary}
    if not out:
        return None
    checks = result.get("checks") or []
    out["checks"] = {
        level: sum(1 for c in checks if c.get("level") == level) for level in ("ok", "warn", "fail")
    }
    return out


def _version_numbers(db: Session, rows: list[Analysis]) -> dict[int, int]:
    ids = {r.version_id for r in rows if r.version_id is not None}
    if not ids:
        return {}
    return dict(
        db.execute(
            select(DesignVersion.id, DesignVersion.number).where(DesignVersion.id.in_(ids))
        ).all()
    )


def list_item(
    row: Analysis, version_number: int | None, worker: AnalysisWorker | None
) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "version_id": row.version_id,
        "version_number": version_number,
        "source": "version" if row.version_id is not None else "draft",
        "kind": row.kind,
        "status": row.status,
        "progress": row.progress,
        "stage": row.stage,
        "error": row.error,
        "duration_s": row.duration_s,
        "inputs_hash": row.inputs_hash,
        "reused_from_id": row.reused_from_id,
        "queue_position": (
            worker.queue_position(row.id) if worker and row.status == "queued" else None
        ),
        "created_at": row.created_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "headline": _headline(row),
        "parts": "selected" if row.inputs.get("parts") else "generic",
    }


def analysis_out(
    row: Analysis, version_number: int | None, worker: AnalysisWorker | None
) -> AnalysisOut:
    return AnalysisOut(
        **list_item(row, version_number, worker),
        target_takeoff_mass_kg=row.inputs.get("target_takeoff_mass_kg"),
        result=row.result,
    )


def owned_analysis(db: Session, user: User, analysis_id: int) -> Analysis:
    row = db.get(Analysis, analysis_id)
    if row is None or row.owner_id != user.id:
        raise not_found("Analysis")
    return row


def resolve_source(
    db: Session, project: Project, source: Source
) -> tuple[DesignVersion | None, dict[str, Any], dict[str, Any]]:
    """(version or None for the draft, parameters, mission), upgraded to the current schema."""
    if isinstance(source, VersionSource):
        version = db.get(DesignVersion, source.version_id)
        if version is None or version.project_id != project.id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="That version does not belong to this project.",
            )
        return (
            version,
            current_parameters(version.parameters),
            current_mission(version.mission),
        )
    return (
        None,
        current_parameters(project.draft_parameters),
        current_mission(project.draft_mission),
    )


def inputs_hash(inputs: dict[str, Any]) -> str:
    """Everything that changes the numbers; the settings labels and sources (``settings_meta``)
    only change wording and are left out."""
    hashed = {
        "kind": inputs["kind"],
        "parameters": inputs["parameters"],
        "mission": inputs["mission"],
        "settings": inputs["settings"],
        "engine_version": inputs["engine_version"],
        "job_version": inputs["job_version"],
        "target_takeoff_mass_kg": inputs.get("target_takeoff_mass_kg"),
        "parts": inputs.get("parts"),
    }
    if inputs.get("calibration"):  # Phase 6; absent keeps earlier hashes (and reuse) valid
        hashed["calibration"] = inputs["calibration"]
    return hashlib.sha256(canonical_json(hashed).encode("ascii")).hexdigest()


def enqueue(
    request: Request,
    db: Session,
    user: User,
    project: Project,
    source: Source,
    kind: str,
    target_takeoff_mass_kg: float | None = None,
    parts_mode: str = "generic",
) -> Any:
    worker = _worker(request)
    if worker is None or not worker.running:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "The analysis engine is starting up. Try again in a moment."},
        )
    version, parameters, mission = resolve_source(db, project, source)
    settings_doc, meta = effective_settings(db, user.id)
    inputs: dict[str, Any] = {
        "kind": kind,
        "source": "version" if version else "draft",
        "version_id": version.id if version else None,
        "parameters": parameters,
        "mission": mission,
        "settings": settings_doc,
        "settings_meta": meta,
        "engine_version": ENGINE_VERSION,
        "job_version": JOB_VERSION,
    }
    if target_takeoff_mass_kg is not None:
        inputs["target_takeoff_mass_kg"] = float(target_takeoff_mass_kg)
    if kind == "full" and parts_mode == "selected":
        # Phase 4: the parts list (locked parts and the engine's picks) goes into the inputs,
        # so it is part of the hash; the picks are stored as the draft's or version's record.
        parts, summary = parts_service.analysis_parts_for(
            db,
            request.app.state.settings,
            user.id,
            project,
            version,
            parameters,
            mission,
            settings_doc,
        )
        if parts:
            inputs["parts"] = parts
            inputs["parts_selection"] = summary
    if kind == "full":
        # Phase 6: the project's applied calibration factors (flight logs, built weights).
        calibration = analysis_calibration(db, project.id)
        if calibration:
            inputs["calibration"] = calibration
    digest = inputs_hash(inputs)
    version_id = version.id if version else None
    version_number = version.number if version else None

    pending = db.scalar(
        select(Analysis)
        .where(
            Analysis.owner_id == user.id,
            Analysis.project_id == project.id,
            Analysis.inputs_hash == digest,
            Analysis.status.in_(("queued", "running")),
            (
                Analysis.version_id.is_(None)
                if version_id is None
                else Analysis.version_id == version_id
            ),
        )
        .order_by(Analysis.id.desc())
    )
    if pending is not None:
        return _accepted(pending, version_number, worker)

    done = db.scalar(
        select(Analysis)
        .where(
            Analysis.owner_id == user.id,
            Analysis.inputs_hash == digest,
            Analysis.status == "done",
            Analysis.result.is_not(None),
        )
        .order_by(Analysis.id.desc())
    )
    now = utcnow()
    if done is not None:
        row = Analysis(
            owner_id=user.id,
            project_id=project.id,
            version_id=version_id,
            kind=kind,
            inputs=inputs,
            inputs_hash=digest,
            status="done",
            progress=1.0,
            stage=REUSED_STAGE,
            result=done.result,
            duration_s=0.0,
            started_at=now,
            finished_at=now,
            reused_from_id=done.reused_from_id or done.id,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _accepted(row, version_number, worker)

    row = Analysis(
        owner_id=user.id,
        project_id=project.id,
        version_id=version_id,
        kind=kind,
        inputs=inputs,
        inputs_hash=digest,
        status="queued",
        progress=0.0,
        stage="Waiting for the analysis engine",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    # When the worker is stopping, the row stays queued and runs after the restart.
    with contextlib.suppress(RuntimeError):
        worker.submit_analysis(row.id)
    return _accepted(row, version_number, worker)


def _accepted(row: Analysis, version_number: int | None, worker: AnalysisWorker | None) -> Any:
    body = AnalysisListItem(**list_item(row, version_number, worker))
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=body.model_dump(mode="json"))


@router.post(
    "/projects/{project_id}/analyses",
    response_model=AnalysisListItem,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_analysis(
    project_id: int, body: AnalysisCreate, request: Request, db: DbSession, user: CurrentUser
) -> Any:
    project = owned_project(db, user, project_id)
    return enqueue(request, db, user, project, body.source, body.kind, parts_mode=body.parts)


@router.post(
    "/projects/{project_id}/scale",
    response_model=AnalysisListItem,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_scale(
    project_id: int, body: ScaleCreate, request: Request, db: DbSession, user: CurrentUser
) -> Any:
    project = owned_project(db, user, project_id)
    return enqueue(request, db, user, project, body.source, "scale", body.target_takeoff_mass_kg)


@router.get("/projects/{project_id}/analyses", response_model=list[AnalysisListItem])
def list_analyses(
    project_id: int, request: Request, db: DbSession, user: CurrentUser, limit: int = 50
) -> list[dict[str, Any]]:
    project = owned_project(db, user, project_id)
    rows = list(
        db.scalars(
            select(Analysis)
            .where(Analysis.project_id == project.id)
            .order_by(Analysis.id.desc())
            .limit(max(1, min(limit, 200)))
        ).all()
    )
    numbers = _version_numbers(db, rows)
    worker = _worker(request)
    return [list_item(r, numbers.get(r.version_id or 0), worker) for r in rows]


@router.get("/analyses/{analysis_id}", response_model=AnalysisOut)
def get_analysis(
    analysis_id: int, request: Request, db: DbSession, user: CurrentUser
) -> AnalysisOut:
    row = owned_analysis(db, user, analysis_id)
    numbers = _version_numbers(db, [row])
    return analysis_out(row, numbers.get(row.version_id or 0), _worker(request))
