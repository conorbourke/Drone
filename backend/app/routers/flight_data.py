"""Flight data (Phase 6): upload ArduPilot logs (or load the sample), read them on the worker,
compare them with the design, apply calibration factors, and record built weights. Design
notes in :mod:`app.flight_data`."""

from __future__ import annotations

import contextlib
from pathlib import PurePath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import flight_data as fd
from app.db import utcnow
from app.deps import AppSettings, CurrentUser, DbSession, current_user
from app.disk_space import ensure_free_space
from app.flightlog.logging_guide import logging_guide
from app.imaging import sanitise_filename
from app.jobs import AnalysisWorker
from app.models import DesignVersion, FlightLog, Project, User
from app.routers.common import not_found, owned_project
from app.schemas.flight_data import (
    BuiltWeightsPut,
    CalibrationApply,
    FlightLogDetail,
    FlightLogItem,
    FlightLogPatch,
)

router = APIRouter(prefix="/api", tags=["flight-data"], dependencies=[Depends(current_user)])

WORKER_STARTING = "The analysis worker is starting up. Try again in a moment."


def _worker(request: Request) -> AnalysisWorker | None:
    return getattr(request.app.state, "analysis_worker", None)


def _running_worker(request: Request) -> AnalysisWorker:
    worker = _worker(request)
    if worker is None or not worker.running:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=WORKER_STARTING)
    return worker


def _queue_pos(worker: AnalysisWorker | None, row: FlightLog) -> int | None:
    if worker is None or row.status != "queued":
        return None
    return worker.queue_position(row.id, "flightlog")


def _item(db: Session, row: FlightLog, worker: AnalysisWorker | None) -> FlightLogItem:
    return FlightLogItem(
        **fd.list_item(row, fd.version_number(db, row.version_id), _queue_pos(worker, row))
    )


def _accepted(db: Session, row: FlightLog, worker: AnalysisWorker | None) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=_item(db, row, worker).model_dump(mode="json"),
    )


def _owned_log(db: Session, user: User, log_id: int) -> FlightLog:
    row = db.get(FlightLog, log_id)
    if row is None or row.owner_id != user.id:
        raise not_found("Flight log")
    return row


def _project_version(db: Session, project: Project, version_id: int | None) -> int | None:
    if version_id is None:
        return None
    version = db.get(DesignVersion, version_id)
    if version is None or version.project_id != project.id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="That version does not belong to this project.",
        )
    return version.id


def _clean_name(filename: str, ext: str) -> str:
    name = sanitise_filename(filename)
    return name if name.lower().endswith(ext) else f"log{ext}"


def _submit(worker: AnalysisWorker, row: FlightLog) -> None:
    with contextlib.suppress(RuntimeError):  # stopping: runs after the restart
        worker.submit_flight_log(row.id)


def _requeue(db: Session, row: FlightLog) -> None:
    if row.status in ("queued", "running"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This log is still being read. Wait until it finishes, then try again.",
        )
    row.status = "queued"
    row.progress = 0.0
    row.stage = fd.WAITING_STAGE
    row.error = None
    row.started_at = None
    row.finished_at = None
    db.commit()


# ---------------------------------------------------------------------------------------------
# Guide
# ---------------------------------------------------------------------------------------------


@router.get("/flight-data/guide")
def get_guide() -> dict[str, Any]:
    """The "Set up logging" card: ArduPilot parameters with a reason each, how to download."""
    return {
        **logging_guide(),
        "max_upload_mb": fd.MAX_LOG_BYTES // (1024 * 1024),
        "accepted": sorted(fd.LOG_EXTENSIONS),
        "sample_available": fd.SAMPLE_LOG_PATH.is_file(),
    }


# ---------------------------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/flight-logs",
    response_model=FlightLogItem,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_flight_log(
    project_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
    filename: str,
    version_id: int | None = None,
    takeoff_mass_kg: float | None = None,
) -> Any:
    """The raw file is the request body (``application/octet-stream``), at most 200 MB; it is
    streamed to disk in chunks. ``filename`` names it (its extension picks the format)."""
    project = owned_project(db, user, project_id)
    worker = _running_worker(request)
    vid = _project_version(db, project, version_id)
    if takeoff_mass_kg is not None and not (0 < takeoff_mass_kg <= 30):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The take-off mass must be between 0 and 30 kg.",
        )
    ext = PurePath(filename.replace("\\", "/")).suffix.lower()
    if ext == ".tlog":
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=fd.TLOG_MESSAGE)
    if ext not in fd.LOG_EXTENSIONS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Upload an ArduPilot DataFlash log: a .bin file (or a .log text log).",
        )
    try:
        declared = int(request.headers.get("content-length") or fd.MAX_LOG_BYTES)
    except ValueError:
        declared = fd.MAX_LOG_BYTES
    ensure_free_space(settings.flight_logs_dir, declared, "store this log")
    settings.flight_logs_dir.mkdir(parents=True, exist_ok=True)
    storage_name = fd.new_storage_name(ext)
    path = fd.log_file(settings, storage_name)
    size = 0
    head = b""
    try:
        with path.open("wb") as fh:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > fd.MAX_LOG_BYTES:
                    raise HTTPException(
                        status.HTTP_413_CONTENT_TOO_LARGE,
                        detail="The log is larger than 200 MB. Lower LOG_BITMASK (or set "
                        "LOG_DISARMED to 0) so each flight makes a smaller log.",
                    )
                if len(head) < 8000:
                    head += chunk[: 8000 - len(head)]
                fh.write(chunk)
        if size == 0:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail="The uploaded file is empty."
            )
        problem = fd.header_problem(ext, head)
        if problem:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=problem)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    row = FlightLog(
        owner_id=user.id,
        project_id=project.id,
        version_id=vid,
        filename=_clean_name(filename, ext),
        storage_name=storage_name,
        size_bytes=size,
        sample=False,
        takeoff_mass_kg=takeoff_mass_kg,
        status="queued",
        progress=0.0,
        stage=fd.WAITING_STAGE,
    )
    db.add(row)
    try:
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    db.refresh(row)
    _submit(worker, row)
    return _accepted(db, row, worker)


@router.post(
    "/projects/{project_id}/flight-logs/sample",
    response_model=FlightLogItem,
    status_code=status.HTTP_202_ACCEPTED,
)
def load_sample_flight(
    project_id: int, request: Request, db: DbSession, user: CurrentUser, settings: AppSettings
) -> Any:
    """Add the bundled simulator flight (ArduPlane SITL QuadPlane) to this project."""
    project = owned_project(db, user, project_id)
    worker = _running_worker(request)
    if not fd.SAMPLE_LOG_PATH.is_file():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="The sample log is not part of this installation."
        )
    ensure_free_space(
        settings.flight_logs_dir, fd.SAMPLE_LOG_PATH.stat().st_size, "add the sample flight"
    )
    storage_name, size = fd.copy_sample(settings)
    row = FlightLog(
        owner_id=user.id,
        project_id=project.id,
        version_id=None,
        filename=fd.SAMPLE_FILENAME,
        storage_name=storage_name,
        size_bytes=size,
        sample=True,
        takeoff_mass_kg=fd.SAMPLE_MASS_KG,
        status="queued",
        progress=0.0,
        stage=fd.WAITING_STAGE,
    )
    db.add(row)
    try:
        db.commit()
    except Exception:
        db.rollback()
        fd.remove_log_file(settings, storage_name)
        raise
    db.refresh(row)
    _submit(worker, row)
    return _accepted(db, row, worker)


@router.get("/projects/{project_id}/flight-logs", response_model=list[FlightLogItem])
def list_flight_logs(
    project_id: int, request: Request, db: DbSession, user: CurrentUser
) -> list[FlightLogItem]:
    project = owned_project(db, user, project_id)
    rows = db.scalars(
        select(FlightLog).where(FlightLog.project_id == project.id).order_by(FlightLog.id.desc())
    ).all()
    worker = _worker(request)
    return [_item(db, r, worker) for r in rows]


@router.get("/flight-logs/{log_id}", response_model=FlightLogDetail)
def get_flight_log(
    log_id: int, request: Request, db: DbSession, user: CurrentUser
) -> FlightLogDetail:
    row = _owned_log(db, user, log_id)
    return FlightLogDetail(
        **fd.detail(row, fd.version_number(db, row.version_id), _queue_pos(_worker(request), row))
    )


@router.get("/flight-logs/{log_id}/series")
def get_flight_log_series(
    log_id: int, db: DbSession, user: CurrentUser, points: int = fd.DEFAULT_SERIES_POINTS
) -> dict[str, Any]:
    """Chart series averaged down to at most ``points`` per channel, with the phases."""
    row = _owned_log(db, user, log_id)
    if row.status != "done" or not row.result:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="The log has not been read yet.")
    series = row.result.get("series") or {}
    out = fd.downsample_series(series, max(10, min(points, fd.MAX_SERIES_POINTS)))
    out["phases"] = (row.summary or {}).get("phases") or []
    return out


@router.patch("/flight-logs/{log_id}", response_model=FlightLogItem)
def update_flight_log(
    log_id: int, body: FlightLogPatch, request: Request, db: DbSession, user: CurrentUser
) -> Any:
    """Change the weighed take-off mass or the version that flew; the log is read and
    compared again."""
    row = _owned_log(db, user, log_id)
    worker = _running_worker(request)
    project = db.get(Project, row.project_id)
    assert project is not None
    fields = body.model_fields_set
    if "takeoff_mass_kg" in fields:
        row.takeoff_mass_kg = body.takeoff_mass_kg
    if "version_id" in fields:
        row.version_id = _project_version(db, project, body.version_id)
    _requeue(db, row)
    _submit(worker, row)
    return _accepted(db, row, worker)


@router.post(
    "/flight-logs/{log_id}/reprocess",
    response_model=FlightLogItem,
    status_code=status.HTTP_202_ACCEPTED,
)
def reprocess_flight_log(log_id: int, request: Request, db: DbSession, user: CurrentUser) -> Any:
    """Read the log and compare it again (for example after a new full analysis)."""
    row = _owned_log(db, user, log_id)
    worker = _running_worker(request)
    _requeue(db, row)
    _submit(worker, row)
    return _accepted(db, row, worker)


@router.delete("/flight-logs/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_flight_log(
    log_id: int, db: DbSession, user: CurrentUser, settings: AppSettings
) -> Response:
    """Delete the log and its file. An applied calibration keeps its factors (they record the
    source log ids); undo or re-apply it to drop this log's contribution."""
    row = _owned_log(db, user, log_id)
    storage_name = row.storage_name
    db.delete(row)
    db.commit()
    fd.remove_log_file(settings, storage_name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------------------------


def _iso(value: Any) -> Any:
    return value.isoformat().replace("+00:00", "Z") if hasattr(value, "isoformat") else value


def _calibration_state(db: Session, project: Project) -> dict[str, Any]:
    applied = fd.applied_calibration(db, project.id)
    if applied:
        applied["applied_at"] = _iso(applied["applied_at"])
    return {"proposed": fd.proposed_calibration(db, project), "applied": applied}


@router.get("/projects/{project_id}/calibration")
def get_calibration(project_id: int, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    """``{proposed, applied}``: the factors the project's compared logs and built weights
    give now, and the ones applied to its analyses (null when none)."""
    return _calibration_state(db, owned_project(db, user, project_id))


@router.get("/projects/{project_id}/calibration/preview")
def preview_calibration(
    project_id: int, db: DbSession, user: CurrentUser, settings: AppSettings
) -> dict[str, Any]:
    """What applying the offered factors changes in the draft's headline numbers (quick
    analyses with and without them)."""
    project = owned_project(db, user, project_id)
    proposed = fd.proposed_calibration(db, project)
    factors = {
        name: {"value": f["value"], "uncertainty": f["uncertainty"]}
        for name, f in proposed["factors"].items()
        if f.get("applicable")
    }
    preview = fd.calibration_preview(
        settings, db, user.id, project, factors, len(proposed.get("log_ids") or [])
    )
    preview["factors"] = sorted(factors)
    return preview


@router.post("/projects/{project_id}/calibration")
def apply_calibration(
    project_id: int, body: CalibrationApply, db: DbSession, user: CurrentUser
) -> dict[str, Any]:
    """Apply the offered factors: analyses queued from now on use them ("Calibrated with N
    flights"). Replaces any calibration applied before."""
    project = owned_project(db, user, project_id)
    applied = fd.apply_calibration(db, user.id, project, body.factors)
    if not applied:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="There is no factor to apply yet: read a flight log with steady hover or "
            "cruise, or enter built weights of the structure.",
        )
    project.updated_at = utcnow()
    db.commit()
    return _calibration_state(db, project)


@router.delete("/projects/{project_id}/calibration")
def undo_calibration(project_id: int, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    """Undo: remove the applied factors; analyses go back to the uncalibrated model."""
    project = owned_project(db, user, project_id)
    fd.undo_calibration(db, project.id)
    return _calibration_state(db, project)


# ---------------------------------------------------------------------------------------------
# Built weights
# ---------------------------------------------------------------------------------------------


@router.get("/projects/{project_id}/built-weights")
def get_built_weights(
    project_id: int, db: DbSession, user: CurrentUser, settings: AppSettings
) -> dict[str, Any]:
    """Every component of the draft's mass breakdown with its predicted and weighed mass, the
    totals and the structural mass factor."""
    project = owned_project(db, user, project_id)
    return fd.built_weights_payload(settings, db, user.id, project)


@router.put("/projects/{project_id}/built-weights")
def put_built_weights(
    project_id: int,
    body: BuiltWeightsPut,
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
) -> dict[str, Any]:
    project = owned_project(db, user, project_id)
    try:
        fd.save_built_weights(settings, db, user.id, project, [i.model_dump() for i in body.items])
    except KeyError as exc:
        db.rollback()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{exc.args[0]!r} is not a component of this design's mass breakdown.",
        ) from None
    return fd.built_weights_payload(settings, db, user.id, project)
