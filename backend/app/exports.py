"""Phase 5 file exports: inputs snapshot, the job that runs the CAD kernel in a child process,
file storage and the piece preview meshes.

Design (docs/phases/PHASE5.md section 3; the job and API choices are recorded here):

* **Inputs** are snapshotted into the ``exports`` row when the export is queued: the design
  parameters and mission of the source (draft or version), the owner's settings document, the
  latest finished full analysis of the same source *with the same parameters and mission*
  (only its ``balance`` and ``structure`` blocks, which the CAD reads), the Phase 4 parts
  list converted to the CAD library's ``parts_selection`` items (the BOM lists exactly those
  parts, at their best listing), the title-block text and the mesh tolerance.
  ``inputs_hash`` covers everything except the title-block date.
* **Reuse.** A queued or running export with the same hash for the same project and source is
  returned as is; a finished one whose files are all still on disk is returned (same project
  and source) or hard-linked into a new export (``reused_from_id``) instead of running again.
* **The job** runs on the single analysis worker (``app.jobs``, kind ``export``) but the CAD
  work happens in a child process (``python -m app.export_child``): loading OpenCascade costs
  about 470 MB that is never given back, and a child returns all of it to the OS when it ends.
  The child reports progress as JSON lines; the worker writes them to the row (throttled),
  kills the child after :attr:`Settings.export_timeout_s` (10 min) or when its resident memory
  goes above :attr:`Settings.export_memory_limit_mb`, and records the child's peak RSS.
  A :class:`app.cad.CadError` / :class:`app.cad.EnvelopeError` becomes the row's plain
  ``error``; anything else a generic message (the traceback goes to the server log).
* **Files** live in ``{APP_DATA_DIR}/files/exports/{id}/`` (the CAD library's layout plus
  ``manifest.json``) and are removed with the row, its version or its project. Downloads are
  looked up in the stored manifest's ``files`` list; no path is ever taken from the URL.
* **Mould sets (Phase 7)** are rows of the same table with ``kind = "moulds"`` (migration
  0007): the same job, child process, guards, reuse and storage, but the child runs
  :func:`app.cad.moulds.generate_moulds` (``moulds_manifest.json``, schema ``vtol-moulds/1``)
  with its own time limit (:attr:`Settings.mould_timeout_s`); the memory guard is shared
  (a mould set peaks at about 900 MB, under the 1500 MB limit of the 2 GB machine).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import utcnow
from app.models import Analysis, DesignVersion, Export, Project

log = logging.getLogger("app.exports")

#: Bumped when the export job or the CAD library output changes, so ``inputs_hash`` reuse never
#: hands back files made by older code.
EXPORT_JOB_VERSION = "exports-1"
MOULD_JOB_VERSION = "moulds-1"
MOULD_SCHEMA = "vtol-moulds/1"
MANIFEST_NAME = "manifest.json"
MOULD_MANIFEST_NAME = "moulds_manifest.json"
MOULD_PART_KEYS = ("nose", "fuselage", "wing_root_fairing")
MESH_TOLERANCE_MM = 0.05
BACKEND_DIR = Path(__file__).resolve().parent.parent
CHILD_MODULE = "app.export_child"
POLL_INTERVAL_S = 0.25
#: Piece previews above this many triangles are decimated (vertex clustering).
PREVIEW_MAX_TRIANGLES = 60_000

INTERRUPTED_MESSAGE = (
    "Making the files was interrupted because the server stopped unexpectedly. Generate them again."
)
UNEXPECTED_MESSAGE = (
    "Something went wrong while making the files. Nothing was changed. Try again; if it keeps "
    "happening, undo the last change to the design."
)
REQUEUED_STAGE = "Waiting (the server restarted)"
WAITING_STAGE = "Waiting for the analysis worker"
REUSED_STAGE = "Reused identical earlier files"

CONTENT_TYPES = {
    "stl": "model/stl",
    "3mf": "model/3mf",
    "step": "model/step",
    "stp": "model/step",
    "pdf": "application/pdf",
    "dxf": "image/vnd.dxf",
    "csv": "text/csv; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "json": "application/json",
}


class ExportCancelled(Exception):
    """The export row was deleted while its job ran."""


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def export_dir(settings: Settings, export_id: int) -> Path:
    return settings.exports_dir / str(int(export_id))


def remove_export_files(settings: Settings, export_id: int) -> None:
    shutil.rmtree(export_dir(settings, export_id), ignore_errors=True)


def sweep_orphan_dirs(session_factory: sessionmaker[Session], settings: Settings) -> int:
    """Remove export directories that have no row (a crash between deleting a row and its
    files, or files written by a job whose row was deleted meanwhile)."""
    root = settings.exports_dir
    if not root.is_dir():
        return 0
    with session_factory() as db:
        ids = {str(i) for i in db.scalars(select(Export.id)).all()}
    removed = 0
    for entry in root.iterdir():
        if entry.name not in ids:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                with contextlib.suppress(OSError):
                    entry.unlink()
            removed += 1
    return removed


def manifest_name(manifest: dict[str, Any] | None) -> str:
    """The manifest's own file name: ``moulds_manifest.json`` for a mould set."""
    if manifest and manifest.get("schema") == MOULD_SCHEMA:
        return MOULD_MANIFEST_NAME
    return MANIFEST_NAME


def manifest_file_paths(manifest: dict[str, Any] | None) -> list[str]:
    """The relative paths an export may serve: the manifest's ``files`` plus the manifest."""
    if not manifest:
        return []
    paths = [str(f["path"]) for f in manifest.get("files") or [] if isinstance(f, dict)]
    return [*paths, manifest_name(manifest)]


def resolve_export_file(settings: Settings, row: Export, rel: str) -> Path | None:
    """The file for ``rel`` when it is listed in the stored manifest and really lies inside
    the export's directory; otherwise None. ``rel`` is only used as a lookup key."""
    if row.status != "done" or not row.manifest or not row.files_dir:
        return None
    listed = manifest_file_paths(row.manifest)
    if rel not in listed:
        return None
    base = (settings.files_dir / row.files_dir).resolve()
    candidate = (base / listed[listed.index(rel)]).resolve()
    if not candidate.is_relative_to(base) or not candidate.is_file():
        return None
    return candidate


def files_complete(settings: Settings, row: Export) -> bool:
    """Every file of a finished export is still on disk with its recorded size."""
    if row.status != "done" or not row.manifest or not row.files_dir:
        return False
    base = settings.files_dir / row.files_dir
    for f in row.manifest.get("files") or []:
        path = base / str(f.get("path"))
        try:
            if path.stat().st_size != f.get("size_bytes"):
                return False
        except OSError:
            return False
    return (base / manifest_name(row.manifest)).is_file()


def link_files(src: Path, dst: Path, rel_paths: list[str]) -> None:
    """Hard-link (or copy, across file systems) the listed files of one export into another."""
    for rel in rel_paths:
        s, d = src / rel, dst / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(s, d)
        except OSError:
            shutil.copy2(s, d)


def content_type_for(path: str) -> str:
    return CONTENT_TYPES.get(Path(path).suffix.lstrip(".").lower(), "application/octet-stream")


def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return s[:60] or "project"


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def _canonical(value: Any) -> str:
    from app.jobs import canonical_json

    return canonical_json(value)


def matching_analysis(
    db: Session,
    project: Project,
    version: DesignVersion | None,
    parameters: dict[str, Any],
    mission: dict[str, Any],
) -> tuple[int | None, dict[str, Any] | None]:
    """The latest finished, valid full analysis of the same source made from the same
    parameters and mission: (id, the blocks the CAD reads) or (None, None)."""
    q = (
        select(Analysis)
        .where(
            Analysis.project_id == project.id,
            Analysis.kind == "full",
            Analysis.status == "done",
            Analysis.result.is_not(None),
            (
                Analysis.version_id.is_(None)
                if version is None
                else Analysis.version_id == version.id
            ),
        )
        .order_by(Analysis.id.desc())
        .limit(20)
    )
    want = (_canonical(parameters), _canonical(mission))
    for row in db.scalars(q):
        inputs = row.inputs or {}
        if (
            _canonical(inputs.get("parameters")),
            _canonical(inputs.get("mission")),
        ) != want:
            continue
        result = row.result or {}
        if not result.get("valid", True):
            continue
        return row.id, {k: result[k] for k in ("balance", "structure") if k in result}
    return None, None


def cad_selection(parts_list: dict[str, Any]) -> list[dict[str, Any]]:
    """Phase 4 parts list (``parts_service.compute``) -> the CAD library's ``parts_selection``
    items (see ``app/cad/bom.py``). Roles keep their Phase 4 names; the CAD library maps them
    onto its own (``cruise_motor`` -> pusher motor mount, ``spar_tube`` -> wing spar channel).
    ``mass_g`` is the installed mass per unit (line mass / quantity), so the BOM line mass
    equals the parts list's; ``price_eur`` is the parts list's unit price and the only listing
    passed is its best one, so the BOM names the same supplier."""
    items: list[dict[str, Any]] = []
    for r in parts_list.get("roles") or []:
        part = r.get("part")
        if not r.get("filled") or not part:
            continue
        qty = int(r.get("quantity") or 1)
        line_mass = r.get("line_mass_g")
        mass = float(line_mass) / qty if line_mass is not None and qty else float(part["mass_g"])
        notes = ["Phase 4 selection"]
        if r.get("locked"):
            notes.append("your choice (locked)")
        if not part.get("verified"):
            notes.append("catalogue data not verified")
        if r.get("price_source") == "estimate":
            notes.append("price is the catalogue estimate")
        best = r.get("best_listing")
        items.append(
            {
                "role": r["role"],
                "category": part["category"],
                "manufacturer": part["manufacturer"],
                "model": part["model"],
                "label": f"{r.get('label') or r['role']}: {part.get('name') or part['model']}",
                "quantity": qty,
                "mass_g": round(mass, 3),
                "price_eur": r.get("unit_price_eur"),
                "listings": [best] if best else [],
                "spec": part.get("spec") or {},
                "part_id": part["id"],
                "notes": "; ".join(notes),
            }
        )
    return items


def inputs_hash(inputs: dict[str, Any]) -> str:
    project = dict(inputs.get("project") or {})
    project.pop("date", None)
    hashed = {
        "parameters": inputs["parameters"],
        "mission": inputs["mission"],
        "settings": inputs["settings"],
        "analysis": inputs.get("analysis"),
        "parts_selection": inputs.get("parts_selection"),
        "project": project,
        "mesh_tolerance_mm": inputs.get("mesh_tolerance_mm"),
        "engine_version": inputs.get("engine_version"),
        "job_version": inputs.get("job_version"),
        "cad_schema": inputs.get("cad_schema"),
    }
    if inputs.get("kind") == "moulds":  # absent for file exports: their hashes stay valid
        hashed["kind"] = "moulds"
        hashed["mould_parts"] = inputs.get("mould_parts")
        hashed["mould_options"] = inputs.get("mould_options")
    return hashlib.sha256(_canonical(hashed).encode("ascii")).hexdigest()


# ---------------------------------------------------------------------------
# API shapes
# ---------------------------------------------------------------------------


def summary(manifest: dict[str, Any] | None) -> dict[str, Any] | None:
    if not manifest:
        return None
    checks = manifest.get("checks") or {}
    files = manifest.get("files") or []
    groups: dict[str, int] = {}
    for f in files:
        groups[f.get("group", "other")] = groups.get(f.get("group", "other"), 0) + 1
    return {
        "parts": len(manifest.get("parts") or []),
        "pieces": checks.get("pieces"),
        "all_pieces_fit": checks.get("all_pieces_fit"),
        "watertight_all": checks.get("watertight_all"),
        "plates": (manifest.get("plates") or {}).get("count"),
        "files": len(files),
        "groups": groups,
        "envelope_mm": (manifest.get("printer") or {}).get("usable_envelope_mm"),
        "bed_mm": (manifest.get("printer") or {}).get("bed_mm"),
        "bom_totals": (manifest.get("bom") or {}).get("totals"),
        "warnings": manifest.get("warnings") or [],
    }


def list_item(row: Export, version_number: int | None, queue_position: int | None) -> dict:
    inputs = row.inputs or {}
    done = row.status == "done" and row.kind != "moulds"  # mould_list_item fills its own
    return {
        "id": row.id,
        "project_id": row.project_id,
        "version_id": row.version_id,
        "version_number": version_number,
        "source": row.source,
        "status": row.status,
        "progress": row.progress,
        "stage": row.stage,
        "error": row.error,
        "inputs_hash": row.inputs_hash,
        "reused_from_id": row.reused_from_id,
        "queue_position": queue_position if row.status == "queued" else None,
        "parts": "selected" if inputs.get("parts_selection") else "generic",
        "analysis_id": inputs.get("analysis_id"),
        "duration_s": row.duration_s,
        "peak_rss_mb": row.peak_rss_mb,
        "total_size_bytes": row.total_size_bytes,
        "file_count": len((row.manifest or {}).get("files") or []) if done else None,
        "summary": summary(row.manifest) if done else None,
        "zip_url": f"/api/exports/{row.id}/zip" if done else None,
        "created_at": row.created_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
    }


# ---------------------------------------------------------------------------
# The job (runs on the analysis worker thread)
# ---------------------------------------------------------------------------


def _rss_mb(pid: int) -> float | None:
    """Resident memory of a process from /proc (Linux); None elsewhere or when it is gone."""
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError):
        return None
    return None


def _duration(seconds: float) -> str:
    if seconds >= 60:
        minutes = seconds / 60
        return f"{minutes:g} minute" + ("" if minutes == 1 else "s")
    return f"{seconds:g} seconds"


def _kill(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(10)


def _reader(stream: Any, out: queue.Queue[dict[str, Any] | None]) -> None:
    try:
        for raw in stream:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                log.info("export child: %s", line[:500])
                continue
            if isinstance(msg, dict):
                out.put(msg)
    finally:
        out.put(None)


def _child_command() -> list[str]:
    return [sys.executable, "-m", CHILD_MODULE]


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(BACKEND_DIR) + (os.pathsep + path if path else "")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def run_export_job(
    session_factory: sessionmaker[Session],
    settings: Settings,
    export_id: int,
    stop: threading.Event,
    cancel: threading.Event,
) -> None:
    """Run one queued export to completion in a child process. Never raises except
    :class:`app.jobs.JobCancelled` handling (the row goes back to ``queued``)."""
    from app.jobs import _Progress

    with session_factory() as db:
        row = db.get(Export, export_id)
        if row is None or row.status != "queued":
            return
        inputs = dict(row.inputs)
        kind = row.kind or "files"
        row.status = "running"
        row.started_at = utcnow()
        row.progress = 0.01
        row.stage = "Starting the CAD process"
        row.error = None
        db.commit()
    out = export_dir(settings, export_id)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    prog = _Progress(session_factory, export_id, stop, model=Export)
    report = prog.window(0.02, 0.97)
    t0 = time.monotonic()
    moulds = kind == "moulds"
    what = "the moulds" if moulds else "the files"
    timeout_s = settings.mould_timeout_s if moulds else settings.export_timeout_s
    job: dict[str, Any]
    if moulds:
        job = {
            "task": "moulds",
            "parameters": inputs["parameters"],
            "mission": inputs["mission"],
            "settings": inputs["settings"],
            "parts": inputs.get("mould_parts") or list(MOULD_PART_KEYS),
            "options": inputs.get("mould_options"),
            "project": inputs.get("project"),
            "out_dir": str(out),
            "generator": settings.fake_mould_generator,
        }
    else:
        job = {
            "parameters": inputs["parameters"],
            "mission": inputs["mission"],
            "settings": inputs["settings"],
            "analysis": inputs.get("analysis"),
            "parts_selection": inputs.get("parts_selection"),
            "project": inputs.get("project"),
            "mesh_tolerance_mm": inputs.get("mesh_tolerance_mm", MESH_TOLERANCE_MM),
            "out_dir": str(out),
            "generator": settings.fake_export_generator,
        }
    peak = 0.0
    outcome: dict[str, Any] | None = None
    failure: str | None = None
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            _child_command(),
            cwd=str(BACKEND_DIR),
            env=_child_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
        )
        assert proc.stdin is not None and proc.stdout is not None
        messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        reader = threading.Thread(
            target=_reader, args=(proc.stdout, messages), name="export-reader", daemon=True
        )
        reader.start()
        try:
            proc.stdin.write(json.dumps(job).encode("utf-8"))
            proc.stdin.close()
        except BrokenPipeError:
            pass
        ended = False
        while not ended:
            try:
                msg = messages.get(timeout=POLL_INTERVAL_S)
            except queue.Empty:
                msg = {}
            if msg is None:
                ended = True
            elif msg.get("type") == "progress":
                report(float(msg.get("progress", 0.0)), str(msg.get("stage", "")))
            elif msg.get("type") in ("done", "error"):
                outcome = msg
            rss = _rss_mb(proc.pid)
            if rss is not None:
                peak = max(peak, rss)
            if cancel.is_set():
                raise ExportCancelled()
            if stop.is_set():
                from app.jobs import JobCancelled

                raise JobCancelled()
            if rss is not None and rss > settings.export_memory_limit_mb:
                failure = (
                    f"Making {what} needed more than {settings.export_memory_limit_mb:.0f} MB "
                    "of memory and was stopped. "
                    + (
                        "Make the moulds one part at a time."
                        if moulds
                        else "Try a smaller design or split it into versions."
                    )
                )
                break
            if time.monotonic() - t0 > timeout_s:
                failure = (
                    f"Making {what} took longer than {_duration(timeout_s)} "
                    "and was stopped. Try again; if it keeps happening, "
                    + ("make the moulds one part at a time." if moulds else "simplify the design.")
                )
                break
        if failure is not None:
            _kill(proc)
        else:
            try:
                proc.wait(30)
            except subprocess.TimeoutExpired:
                _kill(proc)
        reader.join(5)
    except ExportCancelled:
        if proc is not None:
            _kill(proc)
        shutil.rmtree(out, ignore_errors=True)
        log.info("Export %s cancelled (deleted while running)", export_id)
        # Normally the row is gone; if the delete was rolled back, say what happened.
        _safe_write(
            prog,
            status="error",
            error=f"Making {what} was stopped. Generate them again.",
            stage="Stopped",
            finished_at=utcnow(),
        )
        return
    except BaseException as exc:
        from app.jobs import JobCancelled

        if proc is not None:
            _kill(proc)
        shutil.rmtree(out, ignore_errors=True)
        if isinstance(exc, JobCancelled):
            log.info("Export %s interrupted by shutdown; re-queued", export_id)
            _safe_write(prog, status="queued", progress=0.0, stage=REQUEUED_STAGE, started_at=None)
            return
        if not isinstance(exc, Exception):
            raise
        log.exception("Export %s failed to run", export_id)
        failure = UNEXPECTED_MESSAGE
    duration = round(time.monotonic() - t0, 2)
    if outcome is not None and outcome.get("peak_rss_mb"):
        peak = max(peak, float(outcome["peak_rss_mb"]))
    peak_value = round(peak, 1) if peak else None
    if failure is None:
        if outcome is None:
            code = proc.returncode if proc is not None else None
            log.error(
                "Export %s: the CAD process ended without an answer (exit %s)", export_id, code
            )
            failure = (
                "The CAD process stopped unexpectedly"
                + (f" (exit code {code})" if code is not None else "")
                + ". Try again; if it keeps happening, the server may be short of memory."
            )
        elif outcome.get("type") == "error":
            if outcome.get("code") in ("cad", "envelope"):
                failure = str(outcome.get("message") or UNEXPECTED_MESSAGE)
            else:
                log.error("Export %s: %s", export_id, outcome.get("message"))
                failure = UNEXPECTED_MESSAGE
    if failure is None:
        try:
            name = MOULD_MANIFEST_NAME if moulds else MANIFEST_NAME
            manifest = json.loads((out / name).read_text(encoding="utf-8"))
            if moulds and manifest.get("schema") != MOULD_SCHEMA:
                raise ValueError(f"unexpected mould manifest schema {manifest.get('schema')!r}")
            total = (
                sum((out / str(f["path"])).stat().st_size for f in manifest.get("files") or [])
                + (out / name).stat().st_size
            )
        except (OSError, ValueError, KeyError, TypeError):
            log.exception("Export %s: the manifest or a listed file is missing", export_id)
            failure = UNEXPECTED_MESSAGE
    if failure is not None:
        shutil.rmtree(out, ignore_errors=True)
        _safe_write(
            prog,
            status="error",
            error=failure[:2000],
            stage="Failed",
            finished_at=utcnow(),
            duration_s=duration,
            peak_rss_mb=peak_value,
        )
        _remove_if_deleted(session_factory, settings, export_id)
        return
    _safe_write(
        prog,
        status="done",
        progress=1.0,
        stage="Done",
        manifest=manifest,
        files_dir=f"exports/{export_id}",
        total_size_bytes=int(total),
        finished_at=utcnow(),
        duration_s=duration,
        peak_rss_mb=peak_value,
    )
    _remove_if_deleted(session_factory, settings, export_id)


def _safe_write(prog: Any, **values: Any) -> None:
    try:
        prog.write(**values)
    except Exception:
        log.exception("Could not store the outcome of export %s", prog.analysis_id)


def _remove_if_deleted(
    session_factory: sessionmaker[Session], settings: Settings, export_id: int
) -> None:
    """The row (or its version or project) was deleted while the job ran: drop the files."""
    with session_factory() as db:
        if db.get(Export, export_id) is None:
            remove_export_files(settings, export_id)


def recover_exports(
    session_factory: sessionmaker[Session], settings: Settings
) -> tuple[int, list[int]]:
    """At startup: exports left ``running`` become ``error`` (their partial files are
    removed), queued ones are returned to be queued again, and directories without a row are
    removed."""
    with session_factory() as db:
        running = list(db.scalars(select(Export.id).where(Export.status == "running")).all())
        if running:
            db.execute(
                update(Export)
                .where(Export.id.in_(running))
                .values(status="error", error=INTERRUPTED_MESSAGE, stage="Interrupted")
            )
        queued = list(
            db.scalars(select(Export.id).where(Export.status == "queued").order_by(Export.id))
        )
        db.commit()
    for export_id in [*running, *queued]:
        remove_export_files(settings, export_id)
    sweep_orphan_dirs(session_factory, settings)
    return len(running), queued


def delete_exports(
    db: Session,
    settings: Settings,
    worker: Any,
    *,
    project_id: int | None = None,
    version_id: int | None = None,
) -> list[int]:
    """Delete the export rows of a project or a version (cancelling a running one) and return
    their ids; the caller commits and then calls :func:`remove_export_files` for each."""
    q = select(Export.id)
    if project_id is not None:
        q = q.where(Export.project_id == project_id)
    if version_id is not None:
        q = q.where(Export.version_id == version_id)
    ids = list(db.scalars(q).all())
    if ids:
        if worker is not None:
            for i in ids:
                worker.cancel_export(i)
        db.execute(delete(Export).where(Export.id.in_(ids)))
    return ids


# ---------------------------------------------------------------------------
# Piece preview
# ---------------------------------------------------------------------------


_mesh_cache: OrderedDict[tuple[str, int, int], dict[str, Any]] = OrderedDict()
_mesh_lock = threading.Lock()
#: One preview mesh is built at a time: a large STL takes hundreds of MB and many seconds to
#: index and decimate, and requests run on a thread pool (the browser aborting a preview does
#: not stop its build), so concurrent builds next to a CAD child could exhaust the 2 GB.
_mesh_build_lock = threading.Lock()


def find_piece(manifest: dict[str, Any], piece_id: str) -> tuple[dict, dict] | None:
    """(part, piece) whose STL file stem is ``piece_id``."""
    for part in manifest.get("parts") or []:
        for pc in part.get("pieces") or []:
            if Path(str(pc.get("stl", ""))).stem == piece_id:
                return part, pc
    return None


def _read_stl(path: Path) -> np.ndarray:
    data = path.read_bytes()
    if len(data) < 84:
        raise ValueError("not a binary STL")
    count = int.from_bytes(data[80:84], "little")
    rec = np.frombuffer(
        data,
        dtype=np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("attr", "<u2")]),
        count=count,
        offset=84,
    )
    return rec["v"].astype(np.float64)


def _index(tris: np.ndarray, cell: float) -> tuple[np.ndarray, np.ndarray]:
    """Merge vertices within ``cell`` mm (vertex clustering) and drop collapsed triangles."""
    verts = tris.reshape(-1, 3)
    lo = verts.min(axis=0)
    keys = np.floor((verts - lo) / cell).astype(np.int64)
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    inverse = inverse.reshape(-1)
    sums = np.zeros((len(counts), 3))
    np.add.at(sums, inverse, verts)
    positions = sums / counts[:, None]
    faces = inverse.reshape(-1, 3)
    keep = (
        (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
    )
    return positions, faces[keep]


def piece_mesh(path: Path, max_triangles: int = PREVIEW_MAX_TRIANGLES) -> dict[str, Any]:
    """Indexed mesh of a piece STL (print orientation, mm), decimated above ``max_triangles``,
    as base64 little-endian float32 positions and uint32 indices. Cached per file."""
    st = path.stat()
    key = (str(path), st.st_mtime_ns, max_triangles)
    cached = _cached_mesh(key)
    if cached is not None:
        return cached
    with _mesh_build_lock:
        cached = _cached_mesh(key)  # built meanwhile by a request that held the lock
        if cached is not None:
            return cached
        return _build_piece_mesh(path, key, max_triangles)


def _cached_mesh(key: tuple[str, int, int]) -> dict[str, Any] | None:
    with _mesh_lock:
        if key in _mesh_cache:
            _mesh_cache.move_to_end(key)
            return _mesh_cache[key]
    return None


def _build_piece_mesh(path: Path, key: tuple[str, int, int], max_triangles: int) -> dict[str, Any]:
    tris = _read_stl(path)
    original = len(tris)
    extent = float(np.ptp(tris.reshape(-1, 3), axis=0).max()) if original else 1.0
    positions, faces = _index(tris, 1e-4)
    decimated = False
    cell = extent / 400
    while len(faces) > max_triangles and cell < extent:
        positions, faces = _index(tris, cell)
        decimated = True
        cell *= 1.5
    lo = positions.min(axis=0) if len(positions) else np.zeros(3)
    hi = positions.max(axis=0) if len(positions) else np.zeros(3)
    out = {
        "encoding": "base64 little-endian: positions float32 [x, y, z, ...], indices uint32",
        "triangles_original": original,
        "triangles": len(faces),
        "vertices": len(positions),
        "decimated": decimated,
        "bounds_mm": {"min": np.round(lo, 3).tolist(), "max": np.round(hi, 3).tolist()},
        "positions": base64.b64encode(positions.astype("<f4").tobytes()).decode("ascii"),
        "indices": base64.b64encode(faces.astype("<u4").tobytes()).decode("ascii"),
    }
    with _mesh_lock:
        _mesh_cache[key] = out
        while len(_mesh_cache) > 32:
            _mesh_cache.popitem(last=False)
    return out


# ---------------------------------------------------------------------------
# Mould sets (Phase 7)
# ---------------------------------------------------------------------------


def mould_summary(manifest: dict[str, Any] | None) -> dict[str, Any] | None:
    """The list view of a finished mould set: per part halves, tiles, fit, demoulding and
    flagged faces, plus the totals."""
    if not manifest:
        return None
    parts = manifest.get("summary") or []
    kinds: dict[str, int] = {}
    for f in manifest.get("files") or []:
        kinds[str(f.get("kind", "other"))] = kinds.get(str(f.get("kind", "other")), 0) + 1
    tiles = [
        t
        for p in manifest.get("parts") or []
        for h in p.get("halves") or []
        for t in h.get("tiles") or []
    ]
    return {
        "parts": parts,
        "tiles": len(tiles),
        "all_tiles_fit": all(bool(t.get("fits")) for t in tiles) if tiles else None,
        "demouldable": all(bool(p.get("demouldable")) for p in parts) if parts else None,
        "flagged_faces": sum(int(p.get("flagged_faces") or 0) for p in parts),
        "estimated_mass_g": round(sum(float(t.get("estimated_mass_g") or 0.0) for t in tiles), 1),
        "files": len(manifest.get("files") or []),
        "kinds": kinds,
        "envelope_mm": manifest.get("envelope_mm"),
        "printer": manifest.get("printer"),
    }


def mould_list_item(row: Export, version_number: int | None, queue_position: int | None) -> dict:
    out = list_item(row, version_number, queue_position)
    done = row.status == "done"
    if done:
        out["file_count"] = len((row.manifest or {}).get("files") or [])
    inputs = row.inputs or {}
    options = inputs.get("mould_options") or {}
    out.update(
        summary=mould_summary(row.manifest) if done else None,
        zip_url=f"/api/moulds/{row.id}/zip" if done else None,
        mould_parts=list(inputs.get("mould_parts") or []),
        options={k: options[k] for k in ("min_draft_deg", "vent_channels") if k in options},
    )
    return out


def find_tile(manifest: dict[str, Any], tile_id: str) -> tuple[dict, dict, dict] | None:
    """(part, half, tile) whose STL file stem is ``tile_id``."""
    for part in manifest.get("parts") or []:
        for half in part.get("halves") or []:
            for tile in half.get("tiles") or []:
                stl = str((tile.get("files") or {}).get("stl", ""))
                if stl and Path(stl).stem == tile_id:
                    return part, half, tile
    return None
