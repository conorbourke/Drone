"""File exports (Phase 5): generate the print, CAD, drawing, BOM and notes files of a draft or
version on the worker, then download them one by one or as a ZIP. Design notes in
:mod:`app.exports`."""

from __future__ import annotations

import contextlib
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import exports as ex
from app import parts_service
from app.cad import MANIFEST_SCHEMA
from app.db import utcnow
from app.deps import AppSettings, CurrentUser, DbSession, current_user
from app.engine.analysis import ENGINE_VERSION
from app.jobs import AnalysisWorker
from app.models import DesignVersion, Export, Part, Project, User
from app.routers.analyses import resolve_source
from app.routers.common import not_found, owned_project
from app.routers.settings import effective_settings
from app.schemas.export import ExportCreate, ExportListItem, ExportOut

router = APIRouter(prefix="/api", tags=["exports"], dependencies=[Depends(current_user)])

DOWNLOAD_CACHE = "private, max-age=3600"
ZIP_CHUNK = 1 << 16


def _worker(request: Request) -> AnalysisWorker | None:
    return getattr(request.app.state, "analysis_worker", None)


def _version_number(db: Session, version_id: int | None) -> int | None:
    if version_id is None:
        return None
    return db.scalar(select(DesignVersion.number).where(DesignVersion.id == version_id))


def _item(db: Session, row: Export, worker: AnalysisWorker | None) -> dict[str, Any]:
    pos = worker.queue_position(row.id, "export") if worker and row.status == "queued" else None
    return ex.list_item(row, _version_number(db, row.version_id), pos)


def _owned_export(db: Session, user: User, export_id: int) -> Export:
    row = db.get(Export, export_id)
    # Mould sets (Phase 7) share the table but have their own routes (app.routers.moulds).
    if row is None or row.owner_id != user.id or row.kind != "files":
        raise not_found("Export")
    return row


def _accepted(db: Session, row: Export, worker: AnalysisWorker | None) -> JSONResponse:
    body = ExportListItem(**_item(db, row, worker))
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=body.model_dump(mode="json"))


def _parts_selection(
    request: Request,
    db: Session,
    user: User,
    project: Project,
    version: DesignVersion | None,
    parameters: dict[str, Any],
    mission: dict[str, Any],
    settings_doc: dict[str, Any],
) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    """The Phase 4 parts list as CAD selection items (stored as the source's picks, like an
    analysis being queued); (None, None) with an empty catalogue or a design the selection
    cannot analyse (the export then uses generic sizes, or reports why the CAD failed)."""
    if db.scalar(select(Part.id).limit(1)) is None:
        return None, None
    try:
        result = parts_service.compute(
            db,
            request.app.state.settings,
            user.id,
            project,
            version,
            parameters,
            mission,
            settings_doc,
        )
    except parts_service.PartsListError:
        return None, None
    parts_service.persist_selection(
        db, user.id, project.id, version.id if version else None, result["selections"]
    )
    items = ex.cad_selection(result)
    picks = {
        role: {"part_id": s["part_id"], "quantity": s["quantity"], "locked": s["locked"]}
        for role, s in result["selections"].items()
    }
    return (items or None), (picks or None)


@router.post(
    "/projects/{project_id}/exports",
    response_model=ExportListItem,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_export(
    project_id: int,
    body: ExportCreate,
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
            content={"detail": "The file generator is starting up. Try again in a moment."},
        )
    version, parameters, mission = resolve_source(db, project, body.source)
    settings_doc, _meta = effective_settings(db, user.id)
    analysis_id, analysis = ex.matching_analysis(db, project, version, parameters, mission)
    selection, picks = _parts_selection(
        request, db, user, project, version, parameters, mission, settings_doc
    )
    inputs: dict[str, Any] = {
        "source": "version" if version else "draft",
        "version_id": version.id if version else None,
        "parameters": parameters,
        "mission": mission,
        "settings": settings_doc,
        "analysis_id": analysis_id,
        "analysis": analysis,
        "parts_selection": selection,
        "parts_picks": picks,
        "project": {
            "project": project.name,
            "version": f"Version {version.number}: {version.name}" if version else "Draft",
            "date": utcnow().date().isoformat(),
        },
        "mesh_tolerance_mm": ex.MESH_TOLERANCE_MM,
        "engine_version": ENGINE_VERSION,
        "job_version": ex.EXPORT_JOB_VERSION,
        "cad_schema": MANIFEST_SCHEMA,
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
            Export.kind == "files",
            Export.inputs_hash == digest,
            Export.status.in_(("queued", "running")),
            same_source,
        )
        .order_by(Export.id.desc())
    )
    if pending is not None:
        db.commit()  # the stored picks
        return _accepted(db, pending, worker)

    for done in db.scalars(
        select(Export)
        .where(
            Export.owner_id == user.id,
            Export.kind == "files",
            Export.inputs_hash == digest,
            Export.status == "done",
        )
        .order_by(Export.id.desc())
        .limit(5)
    ):
        if not ex.files_complete(settings, done):
            continue
        if done.project_id == project.id and done.version_id == version_id:
            db.commit()
            return _accepted(db, done, worker)
        now = utcnow()
        row = Export(
            owner_id=user.id,
            project_id=project.id,
            version_id=version_id,
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


@router.get("/projects/{project_id}/exports", response_model=list[ExportListItem])
def list_exports(
    project_id: int, request: Request, db: DbSession, user: CurrentUser, limit: int = 50
) -> list[dict[str, Any]]:
    project = owned_project(db, user, project_id)
    rows = db.scalars(
        select(Export)
        .where(Export.project_id == project.id, Export.kind == "files")
        .order_by(Export.id.desc())
        .limit(max(1, min(limit, 200)))
    ).all()
    worker = _worker(request)
    return [_item(db, r, worker) for r in rows]


@router.get("/exports/{export_id}", response_model=ExportOut)
def get_export(export_id: int, request: Request, db: DbSession, user: CurrentUser) -> Any:
    row = _owned_export(db, user, export_id)
    return ExportOut(
        **_item(db, row, _worker(request)),
        manifest=row.manifest if row.status == "done" else None,
    )


@router.delete("/exports/{export_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_export(
    export_id: int, request: Request, db: DbSession, user: CurrentUser, settings: AppSettings
) -> Response:
    row = _owned_export(db, user, export_id)
    worker = _worker(request)
    if worker is not None:
        worker.cancel_export(row.id)
    db.delete(row)
    db.commit()
    ex.remove_export_files(settings, export_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _done_export(db: Session, user: User, export_id: int) -> Export:
    row = _owned_export(db, user, export_id)
    if row.status != "done" or not row.manifest:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="The files are not ready.")
    return row


@router.get("/exports/{export_id}/files/{path:path}")
def download_file(
    export_id: int, path: str, db: DbSession, user: CurrentUser, settings: AppSettings
) -> FileResponse:
    row = _done_export(db, user, export_id)
    file = ex.resolve_export_file(settings, row, path)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="File not found.")
    return FileResponse(
        file,
        media_type=ex.content_type_for(file.name),
        filename=file.name,
        headers={"Cache-Control": DOWNLOAD_CACHE},
    )


class _Sink:
    """A write-only, non-seekable buffer for streaming a ZIP as it is written."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.pos = 0

    def write(self, data: bytes) -> int:
        self.buf += data
        self.pos += len(data)
        return len(data)

    def tell(self) -> int:
        return self.pos

    def flush(self) -> None:
        pass

    def drain(self) -> bytes:
        out = bytes(self.buf)
        self.buf.clear()
        return out


def _zip_stream(entries: list[tuple[str, Path]], root: str) -> Iterator[bytes]:
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel, path in entries:
            info = zipfile.ZipInfo(f"{root}/{rel}", date_time=(2026, 1, 1, 0, 0, 0))
            # 3MF is already a deflated package; PDF streams are compressed too.
            info.compress_type = (
                zipfile.ZIP_STORED if path.suffix.lower() in (".3mf",) else zipfile.ZIP_DEFLATED
            )
            info.external_attr = 0o644 << 16
            with zf.open(info, "w") as dst, path.open("rb") as src:
                while chunk := src.read(ZIP_CHUNK):
                    dst.write(chunk)
                    if len(sink.buf) >= ZIP_CHUNK:
                        yield sink.drain()
            if sink.buf:
                yield sink.drain()
    if sink.buf:
        yield sink.drain()


@router.get("/exports/{export_id}/zip")
def download_zip(
    export_id: int, db: DbSession, user: CurrentUser, settings: AppSettings
) -> StreamingResponse:
    row = _done_export(db, user, export_id)
    entries: list[tuple[str, Path]] = []
    for rel in ex.manifest_file_paths(row.manifest):
        file = ex.resolve_export_file(settings, row, rel)
        if file is None:
            raise HTTPException(
                status.HTTP_410_GONE,
                detail="Some files of this export are missing. Generate the files again.",
            )
        entries.append((rel, file))
    project = (row.inputs.get("project") or {}).get("project") or "project"
    label = "draft" if row.version_id is None else f"v{_version_number(db, row.version_id)}"
    root = f"{ex.slug(project)}-{label}-files-{row.id}"
    return StreamingResponse(
        _zip_stream(entries, root),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{root}.zip"',
            "Cache-Control": DOWNLOAD_CACHE,
        },
    )


@router.get("/exports/{export_id}/pieces/{piece_id}/mesh")
def piece_mesh(
    export_id: int, piece_id: str, db: DbSession, user: CurrentUser, settings: AppSettings
) -> JSONResponse:
    row = _done_export(db, user, export_id)
    assert row.manifest is not None
    found = ex.find_piece(row.manifest, piece_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Piece not found.")
    part, pc = found
    file = ex.resolve_export_file(settings, row, str(pc.get("stl", "")))
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Piece not found.")
    try:
        mesh = ex.piece_mesh(file)
    except (OSError, ValueError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Piece not found.") from None
    printer = row.manifest.get("printer") or {}
    orientation = pc.get("orientation") or {}
    body = {
        "piece_id": piece_id,
        "part_key": part.get("key"),
        "part_label": part.get("label"),
        "label": pc.get("label"),
        "number": pc.get("number"),
        "count": pc.get("count"),
        "fits": pc.get("fits"),
        "size_mm": pc.get("size_mm"),
        "filament": pc.get("filament"),
        "orientation": {
            k: orientation.get(k) for k in ("policy", "description", "reason", "tilt_deg")
        },
        "printer": printer.get("name"),
        "bed_mm": printer.get("bed_mm"),
        "envelope_mm": printer.get("usable_envelope_mm"),
        "stl_path": pc.get("stl"),
        **mesh,
    }
    return JSONResponse(body, headers={"Cache-Control": DOWNLOAD_CACHE})
