"""Mould sets (Phase 7): generate the two-part, tiled female moulds of the nose bay shell, the
fuselage shell and the wing-root fairing of a draft or version on the worker, then download
the tile print files, the STEP mould halves and the PDF sheets one by one or as a ZIP, preview
a tile on the printer bed and delete a set.

A mould set is a row of ``exports`` with ``kind = "moulds"`` (migration 0007), so it shares the
Phase 5 job (child process, time limit and memory guard, progress, reuse, storage and cascade
deletes); see :mod:`app.exports`.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import exports as ex
from app.db import utcnow
from app.deps import AppSettings, CurrentUser, DbSession, current_user
from app.engine.analysis import ENGINE_VERSION
from app.jobs import AnalysisWorker
from app.models import Export, User
from app.routers.analyses import resolve_source
from app.routers.common import not_found, owned_project
from app.routers.exports import DOWNLOAD_CACHE, _version_number, _worker, _zip_stream
from app.routers.settings import effective_settings
from app.schemas.moulds import MouldCreate, MouldListItem, MouldOut

router = APIRouter(prefix="/api", tags=["moulds"], dependencies=[Depends(current_user)])

KIND = "moulds"


def _item(db: Session, row: Export, worker: AnalysisWorker | None) -> dict[str, Any]:
    pos = worker.queue_position(row.id, "export") if worker and row.status == "queued" else None
    return ex.mould_list_item(row, _version_number(db, row.version_id), pos)


def _owned(db: Session, user: User, mould_id: int) -> Export:
    row = db.get(Export, mould_id)
    if row is None or row.owner_id != user.id or row.kind != KIND:
        raise not_found("Mould set")
    return row


def _done(db: Session, user: User, mould_id: int) -> Export:
    row = _owned(db, user, mould_id)
    if row.status != "done" or not row.manifest:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="The mould files are not ready.")
    return row


def _accepted(db: Session, row: Export, worker: AnalysisWorker | None) -> JSONResponse:
    body = MouldListItem(**_item(db, row, worker))
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=body.model_dump(mode="json"))


@router.post(
    "/projects/{project_id}/moulds",
    response_model=MouldListItem,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_moulds(
    project_id: int,
    body: MouldCreate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
) -> Any:
    project = owned_project(db, user, project_id)
    worker = _worker(request)
    if worker is None or not worker.running:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "The mould generator is starting up. Try again in a moment."},
        )
    version, parameters, mission = resolve_source(db, project, body.source)
    settings_doc, _meta = effective_settings(db, user.id)
    inputs: dict[str, Any] = {
        "kind": KIND,
        "source": "version" if version else "draft",
        "version_id": version.id if version else None,
        "parameters": parameters,
        "mission": mission,
        "settings": settings_doc,
        "mould_parts": list(body.parts),
        "mould_options": {
            "min_draft_deg": float(body.min_draft_deg),
            "vent_channels": bool(body.vent_channels),
        },
        "project": {
            "project": project.name,
            "version": f"Version {version.number}: {version.name}" if version else "Draft",
            "date": utcnow().date().isoformat(),
        },
        "engine_version": ENGINE_VERSION,
        "job_version": ex.MOULD_JOB_VERSION,
        "cad_schema": ex.MOULD_SCHEMA,
    }
    digest = ex.inputs_hash(inputs)
    version_id = version.id if version else None
    same_source = (
        Export.version_id.is_(None) if version_id is None else Export.version_id == version_id
    )

    pending = db.scalar(
        select(Export)
        .where(
            Export.owner_id == user.id,
            Export.project_id == project.id,
            Export.kind == KIND,
            Export.inputs_hash == digest,
            Export.status.in_(("queued", "running")),
            same_source,
        )
        .order_by(Export.id.desc())
    )
    if pending is not None:
        return _accepted(db, pending, worker)

    for done in db.scalars(
        select(Export)
        .where(
            Export.owner_id == user.id,
            Export.kind == KIND,
            Export.inputs_hash == digest,
            Export.status == "done",
        )
        .order_by(Export.id.desc())
        .limit(5)
    ):
        if not ex.files_complete(settings, done):
            continue
        if done.project_id == project.id and done.version_id == version_id:
            return _accepted(db, done, worker)
        now = utcnow()
        row = Export(
            owner_id=user.id,
            project_id=project.id,
            version_id=version_id,
            kind=KIND,
            source=inputs["source"],
            inputs=inputs,
            inputs_hash=digest,
            status="running",
            progress=0.5,
            stage=ex.REUSED_STAGE,
        )
        db.add(row)
        db.commit()
        src = settings.files_dir / str(done.files_dir)
        dst = ex.export_dir(settings, row.id)
        try:
            ex.link_files(src, dst, ex.manifest_file_paths(done.manifest))
        except OSError:
            ex.remove_export_files(settings, row.id)
            db.delete(row)
            db.commit()
            break
        row.status = "done"
        row.progress = 1.0
        row.manifest = done.manifest
        row.files_dir = f"exports/{row.id}"
        row.total_size_bytes = done.total_size_bytes
        row.duration_s = 0.0
        row.peak_rss_mb = None
        row.started_at = now
        row.finished_at = now
        row.reused_from_id = done.reused_from_id or done.id
        db.commit()
        db.refresh(row)
        return _accepted(db, row, worker)

    row = Export(
        owner_id=user.id,
        project_id=project.id,
        version_id=version_id,
        kind=KIND,
        source=inputs["source"],
        inputs=inputs,
        inputs_hash=digest,
        status="queued",
        progress=0.0,
        stage=ex.WAITING_STAGE,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    with contextlib.suppress(RuntimeError):  # stopping: runs after the restart
        worker.submit_export(row.id)
    return _accepted(db, row, worker)


@router.get("/projects/{project_id}/moulds", response_model=list[MouldListItem])
def list_moulds(
    project_id: int, request: Request, db: DbSession, user: CurrentUser, limit: int = 50
) -> list[dict[str, Any]]:
    project = owned_project(db, user, project_id)
    rows = db.scalars(
        select(Export)
        .where(Export.project_id == project.id, Export.kind == KIND)
        .order_by(Export.id.desc())
        .limit(max(1, min(limit, 200)))
    ).all()
    worker = _worker(request)
    return [_item(db, r, worker) for r in rows]


@router.get("/moulds/{mould_id}", response_model=MouldOut)
def get_moulds(mould_id: int, request: Request, db: DbSession, user: CurrentUser) -> Any:
    row = _owned(db, user, mould_id)
    return MouldOut(
        **_item(db, row, _worker(request)),
        manifest=row.manifest if row.status == "done" else None,
    )


@router.delete("/moulds/{mould_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_moulds(
    mould_id: int, request: Request, db: DbSession, user: CurrentUser, settings: AppSettings
) -> Response:
    row = _owned(db, user, mould_id)
    worker = _worker(request)
    if worker is not None:
        worker.cancel_export(row.id)
    db.delete(row)
    db.commit()
    ex.remove_export_files(settings, mould_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/moulds/{mould_id}/files/{path:path}")
def download_mould_file(
    mould_id: int, path: str, db: DbSession, user: CurrentUser, settings: AppSettings
) -> FileResponse:
    row = _done(db, user, mould_id)
    file = ex.resolve_export_file(settings, row, path)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="File not found.")
    return FileResponse(
        file,
        media_type=ex.content_type_for(file.name),
        filename=file.name,
        headers={"Cache-Control": DOWNLOAD_CACHE},
    )


@router.get("/moulds/{mould_id}/zip")
def download_mould_zip(
    mould_id: int, db: DbSession, user: CurrentUser, settings: AppSettings
) -> StreamingResponse:
    row = _done(db, user, mould_id)
    entries: list[tuple[str, Path]] = []
    for rel in ex.manifest_file_paths(row.manifest):
        file = ex.resolve_export_file(settings, row, rel)
        if file is None:
            raise HTTPException(
                status.HTTP_410_GONE,
                detail="Some files of this mould set are missing. Generate the moulds again.",
            )
        entries.append((rel, file))
    project = (row.inputs.get("project") or {}).get("project") or "project"
    label = "draft" if row.version_id is None else f"v{_version_number(db, row.version_id)}"
    root = f"{ex.slug(project)}-{label}-moulds-{row.id}"
    return StreamingResponse(
        _zip_stream(entries, root),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{root}.zip"',
            "Cache-Control": DOWNLOAD_CACHE,
        },
    )


@router.get("/moulds/{mould_id}/tiles/{tile_id}/mesh")
def tile_mesh(
    mould_id: int, tile_id: str, db: DbSession, user: CurrentUser, settings: AppSettings
) -> JSONResponse:
    row = _done(db, user, mould_id)
    assert row.manifest is not None
    found = ex.find_tile(row.manifest, tile_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tile not found.")
    part, half, tile = found
    stl = str((tile.get("files") or {}).get("stl", ""))
    file = ex.resolve_export_file(settings, row, stl)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tile not found.")
    try:
        mesh = ex.piece_mesh(file)
    except (OSError, ValueError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tile not found.") from None
    printer = ((row.inputs or {}).get("settings") or {}).get("printer") or {}
    volume = printer.get("build_volume_mm") or {}
    bed = [volume["x"], volume["y"]] if "x" in volume and "y" in volume else None
    orientation = tile.get("print_orientation") or {}
    body = {
        "piece_id": tile_id,
        "part_key": part.get("key"),
        "part_label": part.get("label"),
        "half": half.get("key"),
        "half_label": half.get("label"),
        "label": tile.get("label"),
        "number": tile.get("index"),
        "count": tile.get("count"),
        "fits": tile.get("fits"),
        "size_mm": tile.get("size_mm"),
        "filament": tile.get("filament"),
        "estimated_mass_g": tile.get("estimated_mass_g"),
        "estimated_print_time": tile.get("estimated_print_time"),
        "orientation": {
            "policy": orientation.get("name"),
            "description": orientation.get("name"),
            "reason": orientation.get("reason"),
            "tilt_deg": None,
        },
        "printer": row.manifest.get("printer") or printer.get("name"),
        "bed_mm": bed,
        "envelope_mm": tile.get("envelope_mm") or row.manifest.get("envelope_mm"),
        "stl_path": stl,
        **mesh,
    }
    return JSONResponse(body, headers={"Cache-Control": DOWNLOAD_CACHE})
