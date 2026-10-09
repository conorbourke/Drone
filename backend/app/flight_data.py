"""Flight data (Phase 6): stored ArduPilot logs, the worker job that reads them and compares
them with the design, calibration factors and built weights.

Design notes (docs/phases/PHASE6.md):

* Files live under ``{APP_DATA_DIR}/files/logs/{storage_name}`` (random names; the owner's
  file name is only a label). Uploads stream to disk in chunks (never the whole file in
  memory) and are limited to :data:`MAX_LOG_BYTES`.
* The worker job (:func:`run_flight_log_job`, queued on the single analysis worker) runs
  :func:`app.flightlog.process_log`, then :func:`app.flightlog.compare_log` against the
  design's analysis: the newest finished full analysis of the same source (the version that
  flew, or the draft) whose parameters and mission equal the current ones and that was not
  itself calibrated; otherwise a quick analysis is run for the log. Comparisons are always
  made against the uncalibrated model, so applying factors never compounds them.
* Calibration: :func:`proposed_calibration` combines every finished log of the project and
  the structure built weights (:func:`app.flightlog.derive_calibration`); applying stores one
  ``calibrations`` row per factor and analyses queued afterwards pass them to the engine
  (:func:`analysis_calibration`); undo deletes the rows. Only the four factors the engine
  uses can be applied, and only when they are plausible (between 0.5 and 2).
"""

from __future__ import annotations

import logging
import math
import secrets
import shutil
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import utcnow
from app.engine.analysis import CALIBRATION_FACTORS, json_safe
from app.flightlog.calibrate import FACTOR_INFO
from app.jobs import JobCancelled, _Progress, polar_cache_dir
from app.models import (
    Analysis,
    BuiltWeight,
    Calibration,
    DesignVersion,
    FlightLog,
    Project,
)

log = logging.getLogger("app.flight_data")

MAX_LOG_BYTES = 200 * 1024 * 1024
UPLOAD_CHUNK = 1 << 16
#: Accepted extensions: DataFlash binary logs and their text form.
LOG_EXTENSIONS = {".bin": "DataFlash binary log", ".log": "DataFlash text log"}
TLOG_MESSAGE = (
    "Telemetry logs (.tlog) only hold what the radio link carried, at a low rate and without "
    "the battery and tuning messages the comparison needs. Download the DataFlash .bin log "
    "from the SD card or with Mission Planner (DataFlash Logs tab) or QGroundControl (Log "
    "Download) instead."
)
NOT_A_LOG_MESSAGE = (
    "This file is not an ArduPilot DataFlash log (.bin files start with a fixed header, .log "
    "files with FMT lines). Download the .bin from the autopilot's SD card or with Mission "
    "Planner or QGroundControl."
)
UNREADABLE_MESSAGE = (
    "The log could not be read. It may be cut short or damaged; download it again from the "
    "SD card or the ground station and upload the new copy."
)
INTERRUPTED_MESSAGE = "Reading the log was interrupted because the server restarted. Run it again."
WAITING_STAGE = "Waiting for the analysis worker"
REQUEUED_STAGE = "Waiting (the server restarted)"

#: The bundled sample: a real ArduPlane SITL QuadPlane flight (see its README). The whole
#: backend directory is copied into the container image, tests/fixtures included.
SAMPLE_LOG_PATH = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "logs" / "sitl_quadplane.bin"
)
SAMPLE_FILENAME = "sitl_quadplane.bin"
#: SITL's quadplane frame: SIM_FRM_MASS 3.0 kg x 1.5 (tests/fixtures/logs/README.md).
SAMPLE_MASS_KG = 4.5
SAMPLE_NOTE = (
    "Sample flight: a real ArduPilot log from ArduPilot's simulator (ArduPlane SITL, quadplane "
    "frame, 4.5 kg). It is a different, simulated aircraft, so it shows how the comparison "
    "works rather than how good this design is."
)

#: Factors the engine applies, and the band outside which a factor is not offered.
PLAUSIBLE_RANGE = (0.5, 2.0)
#: Chart series: at most this many points per channel by default.
DEFAULT_SERIES_POINTS = 600
MAX_SERIES_POINTS = 2000

STRUCTURE_SUBGROUPS = {
    "wing_structure": "wing",
    "wing_spar": "wing",
    "fuselage_structure": "printed shell",
    "tail_structure": "tail",
    "tail_support": "tail",
    "booms": "booms",
}


# ---------------------------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------------------------


def new_storage_name(extension: str) -> str:
    return f"{secrets.token_hex(16)}{extension}"


def log_file(settings: Settings, storage_name: str) -> Path:
    return settings.flight_logs_dir / storage_name


def remove_log_file(settings: Settings, storage_name: str) -> None:
    log_file(settings, storage_name).unlink(missing_ok=True)


def header_problem(extension: str, head: bytes) -> str | None:
    """None when the first bytes look like a DataFlash log of this kind, else the message."""
    if extension == ".bin" and head.startswith(b"\xa3\x95"):
        return None
    if extension == ".log" and b"FMT," in head[:8000]:
        return None
    return NOT_A_LOG_MESSAGE


# ---------------------------------------------------------------------------------------------
# API payloads
# ---------------------------------------------------------------------------------------------


def version_number(db: Session, version_id: int | None) -> int | None:
    if version_id is None:
        return None
    return db.scalar(select(DesignVersion.number).where(DesignVersion.id == version_id))


def list_item(row: FlightLog, number: int | None, queue_position: int | None) -> dict[str, Any]:
    comparison = row.comparison or {}
    return {
        "id": row.id,
        "project_id": row.project_id,
        "version_id": row.version_id,
        "version_number": number,
        "source": "version" if row.version_id is not None else "draft",
        "filename": row.filename,
        "size_bytes": row.size_bytes,
        "sample": row.sample,
        "takeoff_mass_kg": row.takeoff_mass_kg,
        "status": row.status,
        "progress": row.progress,
        "stage": row.stage,
        "error": row.error,
        "queue_position": queue_position,
        "firmware": row.firmware,
        "vehicle_type": row.vehicle_type,
        "log_start_at": row.log_start_at,
        "flight_duration_s": row.flight_duration_s,
        "summary": row.summary,
        "counts": comparison.get("counts"),
        "created_at": row.created_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
    }


def detail(row: FlightLog, number: int | None, queue_position: int | None) -> dict[str, Any]:
    out = list_item(row, number, queue_position)
    result = dict(row.result or {})
    result.pop("series", None)
    out["result"] = result or None
    out["comparison"] = row.comparison
    out["sample_note"] = SAMPLE_NOTE if row.sample else None
    return out


def _phase_summary(processed: dict[str, Any]) -> dict[str, Any]:
    phases = []
    for p in processed.get("phases") or []:
        power = p.get("power_w") or {}
        speed = p.get("airspeed_mps") or {}
        phases.append(
            {
                "key": p.get("key"),
                "label": p.get("label"),
                "flight": p.get("flight"),
                "start_s": p.get("start_s"),
                "end_s": p.get("end_s"),
                "duration_s": p.get("duration_s"),
                "energy_wh": p.get("energy_wh"),
                "power_mean_w": power.get("mean"),
                "airspeed_median_mps": speed.get("median"),
                "decided_by": p.get("decided_by"),
            }
        )
    flight = processed.get("flight") or {}
    return {
        "phases": phases,
        "log_duration_s": processed.get("duration_s"),
        "flight_duration_s": flight.get("duration_s"),
        "energy_wh": flight.get("energy_wh"),
        "method": (processed.get("phase_detection") or {}).get("method"),
        "vibration": (processed.get("vibration") or {}).get("worst_level"),
        "missing": [m.get("type") for m in processed.get("missing") or []],
    }


def downsample_series(series: dict[str, Any], max_points: int) -> dict[str, Any]:
    """Average the 5 Hz series into at most ``max_points`` buckets per channel (``*_max``
    channels keep the bucket's maximum, ``*_min`` its minimum, so peaks and sag survive)."""
    t = list(series.get("t_s") or [])
    n = len(t)
    step = max(1, math.ceil(n / max(10, max_points)))
    channels = series.get("channels") or {}

    def reduce(values: list[Any], how: str) -> list[float | None]:
        out: list[float | None] = []
        for i in range(0, n, step):
            chunk = [v for v in values[i : i + step] if isinstance(v, int | float)]
            if not chunk:
                out.append(None)
            elif how == "max":
                out.append(max(chunk))
            elif how == "min":
                out.append(min(chunk))
            else:
                out.append(round(sum(chunk) / len(chunk), 4))
        return out

    t_out = [round(sum(t[i : i + step]) / len(t[i : i + step]), 2) for i in range(0, n, step)]
    ch_out = {}
    for name, ch in channels.items():
        how = "max" if name.endswith("_max") else "min" if name.endswith("_min") else "mean"
        ch_out[name] = {
            "unit": ch.get("unit", ""),
            "label": ch.get("label", name),
            "values": reduce(list(ch.get("values") or []), how),
        }
    rate = series.get("rate_hz") or 5.0
    return {
        "t_s": t_out,
        "interval_s": round(step / rate, 3),
        "source_rate_hz": rate,
        "channels": ch_out,
    }


# ---------------------------------------------------------------------------------------------
# The design to compare with
# ---------------------------------------------------------------------------------------------


def _source_docs(
    db: Session, project_id: int, version_id: int | None
) -> tuple[dict[str, Any], dict[str, Any], str]:
    from app.routers.common import current_mission, current_parameters

    if version_id is not None:
        version = db.get(DesignVersion, version_id)
        if version is not None:
            return (
                current_parameters(version.parameters),
                current_mission(version.mission),
                f"version {version.number}",
            )
    project = db.get(Project, project_id)
    assert project is not None
    return (
        current_parameters(project.draft_parameters),
        current_mission(project.draft_mission),
        "the draft",
    )


def stored_reference(
    db: Session,
    project_id: int,
    version_id: int | None,
    parameters: dict[str, Any],
    mission: dict[str, Any],
) -> Analysis | None:
    """The newest finished, uncalibrated full analysis of this source with these inputs."""
    same = (
        Analysis.version_id.is_(None) if version_id is None else Analysis.version_id == version_id
    )
    rows = db.scalars(
        select(Analysis)
        .where(
            Analysis.project_id == project_id,
            Analysis.kind == "full",
            Analysis.status == "done",
            Analysis.result.is_not(None),
            same,
        )
        .order_by(Analysis.id.desc())
        .limit(20)
    ).all()
    for row in rows:
        inputs = row.inputs or {}
        if inputs.get("calibration"):
            continue
        if (
            inputs.get("parameters") == parameters
            and inputs.get("mission") == mission
            and (row.result or {}).get("valid")
        ):
            return row
    return None


def _reference(
    session_factory: sessionmaker[Session],
    settings: Settings,
    owner_id: int,
    project_id: int,
    version_id: int | None,
    prog: _Progress,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(analysis result, reference description) for the comparison."""
    from app.engine.analysis import run_analysis
    from app.routers.settings import effective_settings

    with session_factory() as db:
        parameters, mission, label = _source_docs(db, project_id, version_id)
        row = stored_reference(db, project_id, version_id, parameters, mission)
        if row is not None:
            assert row.result is not None
            return row.result, {
                "analysis_id": row.id,
                "kind": "stored",
                "label": f"Full analysis #{row.id} of {label}",
                "source": label,
            }
        settings_doc, meta = effective_settings(db, owner_id)
    result = run_analysis(
        parameters,
        mission,
        settings_doc,
        mode="fast",
        cache_dir=str(polar_cache_dir(settings)),
        progress=prog.window(0.74, 0.96),
        settings_meta=meta,
        uncertainty=True,
    )
    return result, {
        "analysis_id": None,
        "kind": "quick",
        "label": f"Quick analysis of {label} run for this log (no matching full analysis)",
        "source": label,
    }


# ---------------------------------------------------------------------------------------------
# The worker job
# ---------------------------------------------------------------------------------------------


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def run_flight_log_job(
    session_factory: sessionmaker[Session],
    settings: Settings,
    log_id: int,
    stop: threading.Event,
) -> None:
    """Read one queued log, compare it with the design and store everything. Never raises."""
    from app.flightlog import compare_log, process_log

    with session_factory() as db:
        row = db.get(FlightLog, log_id)
        if row is None or row.status != "queued":
            return
        path = log_file(settings, row.storage_name)
        owner_id, project_id, version_id = row.owner_id, row.project_id, row.version_id
        mass = row.takeoff_mass_kg
        row.status = "running"
        row.started_at = utcnow()
        row.progress = 0.01
        row.stage = "Reading the log"
        row.error = None
        db.commit()
    prog = _Progress(session_factory, log_id, stop, model=FlightLog)
    t0 = time.monotonic()

    def finish(**values: Any) -> None:
        try:
            prog.write(finished_at=utcnow(), duration_s=round(time.monotonic() - t0, 2), **values)
        except Exception:
            log.exception("Could not store the outcome of flight log %s", log_id)

    try:
        processed = json_safe(process_log(path, progress=prog.window(0.02, 0.7)))
    except JobCancelled:
        _requeue(prog)
        return
    except Exception:
        log.exception("Flight log %s could not be read", log_id)
        finish(status="error", error=UNREADABLE_MESSAGE, stage="Failed")
        return

    comparison: dict[str, Any] | None
    analysis_id = None
    try:
        prog.update(0.72, "Finding the design's analysis", force=True)
        reference, ref_meta = _reference(
            session_factory, settings, owner_id, project_id, version_id, prog
        )
        prog.update(0.97, "Comparing with the predictions", force=True)
        if not reference.get("valid"):
            comparison = {
                "available": False,
                "reason": f"{ref_meta['source'].capitalize()} could not be analysed (a check "
                "failed), so there are no predictions to compare with. Fix the design's failed "
                "checks on the Design tab, then press Compare again.",
                "reference": ref_meta,
            }
        else:
            comparison = json_safe(compare_log(processed, reference, logged_mass_kg=mass))
            comparison["available"] = True
            comparison["reference"] = ref_meta
            analysis_id = ref_meta["analysis_id"]
    except JobCancelled:
        _requeue(prog)
        return
    except Exception:
        log.exception("Flight log %s could not be compared", log_id)
        comparison = {
            "available": False,
            "reason": "The comparison with the design failed. The log itself was read; press "
            "Compare again, and if it keeps failing, re-run the design's analysis first.",
            "reference": None,
        }
    flight = processed.get("flight") or {}
    finish(
        status="done",
        progress=1.0,
        stage="Done",
        result=processed,
        summary=json_safe(_phase_summary(processed)),
        comparison=comparison,
        analysis_id=analysis_id,
        firmware=(processed.get("firmware") or None),
        vehicle_type=_vehicle_label(processed.get("vehicle") or {}),
        log_start_at=_parse_time(processed.get("start_time_utc")),
        flight_duration_s=flight.get("duration_s") or processed.get("duration_s"),
    )


def _vehicle_label(vehicle: dict[str, Any]) -> str | None:
    kind = vehicle.get("type")
    conf = vehicle.get("configuration")
    if not kind or kind == "unknown":
        return None
    return f"{kind} ({conf})" if conf else str(kind)


def _requeue(prog: _Progress) -> None:
    log.info("Flight log %s interrupted by shutdown; re-queued", prog.analysis_id)
    try:
        prog.write(status="queued", progress=0.0, stage=REQUEUED_STAGE, started_at=None)
    except Exception:
        log.exception("Could not re-queue flight log %s", prog.analysis_id)


def recover_flight_logs(session_factory: sessionmaker[Session]) -> tuple[int, list[int]]:
    """At startup: logs left ``running`` become ``error`` (the owner can run them again);
    ``queued`` ones are returned, oldest first, to be queued again."""
    with session_factory() as db:
        result = db.execute(
            update(FlightLog)
            .where(FlightLog.status == "running")
            .values(status="error", error=INTERRUPTED_MESSAGE, stage="Interrupted")
        )
        queued = list(
            db.scalars(
                select(FlightLog.id).where(FlightLog.status == "queued").order_by(FlightLog.id)
            ).all()
        )
        db.commit()
    return int(getattr(result, "rowcount", 0) or 0), queued


def delete_project_flight_data(db: Session, project_id: int) -> list[str]:
    """Delete a project's logs, calibration and built weights (before the project itself: their
    version references are RESTRICT). Returns the log files to remove after the commit."""
    names = list(
        db.scalars(select(FlightLog.storage_name).where(FlightLog.project_id == project_id)).all()
    )
    db.execute(delete(Calibration).where(Calibration.project_id == project_id))
    db.execute(delete(FlightLog).where(FlightLog.project_id == project_id))
    db.execute(delete(BuiltWeight).where(BuiltWeight.project_id == project_id))
    return names


def copy_sample(settings: Settings) -> tuple[str, int]:
    """Copy the bundled sample log into the logs directory; (storage name, size)."""
    settings.flight_logs_dir.mkdir(parents=True, exist_ok=True)
    name = new_storage_name(".bin")
    dst = log_file(settings, name)
    shutil.copyfile(SAMPLE_LOG_PATH, dst)
    return name, dst.stat().st_size


# ---------------------------------------------------------------------------------------------
# Built weights
# ---------------------------------------------------------------------------------------------


def subgroup_for(key: str, group: str) -> str:
    if group != "structure":
        return group
    return STRUCTURE_SUBGROUPS.get(key, "other structure")


def predicted_components(
    settings: Settings, db: Session, owner_id: int, project: Project
) -> tuple[list[dict[str, Any]], str | None]:
    """The mass model's components of the draft (uncalibrated quick analysis), or a reason."""
    from app import parts_service
    from app.routers.common import current_mission, current_parameters
    from app.routers.settings import effective_settings

    settings_doc, _meta = effective_settings(db, owner_id)
    result = parts_service.generic_analysis(
        current_parameters(project.draft_parameters),
        current_mission(project.draft_mission),
        settings_doc,
        str(polar_cache_dir(settings)),
    )
    if not result.get("valid"):
        return [], (
            "The draft could not be analysed (a check failed), so there is no predicted mass "
            "breakdown. Fix the design first; weights already entered are kept."
        )
    out = []
    for c in (result.get("mass") or {}).get("components") or []:
        if c.get("group") == "payload":
            continue
        out.append(
            {
                "key": c["key"],
                "label": c["label"],
                "group": c["group"],
                "subgroup": subgroup_for(c["key"], c["group"]),
                "predicted_g": round(float(c["mass_g"]), 1),
                "explain": c.get("explain") or "",
            }
        )
    return out, None


def built_weights_payload(
    settings: Settings, db: Session, owner_id: int, project: Project
) -> dict[str, Any]:
    from app.flightlog.calibrate import structural_factor

    predicted, reason = predicted_components(settings, db, owner_id, project)
    stored = {
        r.key: r
        for r in db.scalars(select(BuiltWeight).where(BuiltWeight.project_id == project.id)).all()
    }
    items = []
    for c in predicted:
        row = stored.pop(c["key"], None)
        items.append(
            {
                **c,
                "measured_g": row.measured_g if row else None,
                "note": row.note if row else "",
                "predicted_at_entry_g": row.predicted_g if row else None,
            }
        )
    for row in stored.values():  # weighed items the current design no longer has
        items.append(
            {
                "key": row.key,
                "label": row.label,
                "group": row.group,
                "subgroup": row.subgroup,
                "predicted_g": row.predicted_g,
                "explain": "No longer in the design's mass breakdown; kept as entered.",
                "measured_g": row.measured_g,
                "note": row.note,
                "predicted_at_entry_g": row.predicted_g,
            }
        )
    weighed = [i for i in items if i["measured_g"] is not None]
    pred_weighed = sum(i["predicted_g"] for i in weighed)
    meas = sum(i["measured_g"] for i in weighed)
    structure = [
        {"group": i["subgroup"], "predicted_g": i["predicted_g"], "measured_g": i["measured_g"]}
        for i in weighed
        if i["group"] == "structure"
    ]
    return json_safe(
        {
            "items": items,
            "totals": {
                "predicted_g": round(sum(i["predicted_g"] for i in items), 1),
                "weighed_items": len(weighed),
                "items": len(items),
                "measured_g": round(meas, 1),
                "predicted_weighed_g": round(pred_weighed, 1),
                "difference_pct": round((meas / pred_weighed - 1) * 100, 1)
                if pred_weighed > 0
                else None,
            },
            "structural": structural_factor(structure),
            "reason": reason,
            "source": "Mass model of the draft (uncalibrated quick analysis), heaviest camera "
            "excluded.",
        }
    )


def save_built_weights(
    settings: Settings,
    db: Session,
    owner_id: int,
    project: Project,
    items: list[dict[str, Any]],
) -> None:
    """Upsert the weighed items; an item with ``measured_g`` null is cleared."""
    predicted, _ = predicted_components(settings, db, owner_id, project)
    known = {c["key"]: c for c in predicted}
    stored = {
        r.key: r
        for r in db.scalars(select(BuiltWeight).where(BuiltWeight.project_id == project.id)).all()
    }
    for item in items:
        key = item["key"]
        row = stored.get(key)
        if item.get("measured_g") is None:
            if row is not None:
                db.delete(row)
            continue
        ref = known.get(key)
        if ref is None and row is None:
            raise KeyError(key)
        if row is None:
            assert ref is not None
            row = BuiltWeight(
                owner_id=owner_id,
                project_id=project.id,
                key=key,
                label=ref["label"],
                group=ref["group"],
                subgroup=ref["subgroup"],
                predicted_g=ref["predicted_g"],
                measured_g=float(item["measured_g"]),
                note=item.get("note") or "",
            )
            db.add(row)
            stored[key] = row
        else:
            if ref is not None:
                row.label, row.group, row.subgroup = ref["label"], ref["group"], ref["subgroup"]
                row.predicted_g = ref["predicted_g"]
            row.measured_g = float(item["measured_g"])
            row.note = item.get("note") or ""
    db.commit()


# ---------------------------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------------------------

FACTOR_EFFECT = {
    "hover_power": "Predicted hover and transition power",
    "cruise_drag": "Predicted cruise drag (and so cruise power, endurance and range)",
    "battery_usable_energy": "Usable battery energy (endurance and range)",
    "structural_mass": "Predicted structure mass (and so take-off mass)",
    "cruise_power": "Shown for information only",
}


def _done_logs(db: Session, project_id: int) -> list[FlightLog]:
    return list(
        db.scalars(
            select(FlightLog)
            .where(FlightLog.project_id == project_id, FlightLog.status == "done")
            .order_by(FlightLog.id)
        ).all()
    )


def proposed_calibration(db: Session, project: Project) -> dict[str, Any]:
    """Factors from every compared log of the project and the structure built weights."""
    from app.flightlog import derive_calibration

    logs = [r for r in _done_logs(db, project.id) if r.comparison and r.comparison.get("available")]
    weights = db.scalars(select(BuiltWeight).where(BuiltWeight.project_id == project.id)).all()
    structure = [
        {"group": w.subgroup, "predicted_g": w.predicted_g, "measured_g": w.measured_g}
        for w in weights
        if w.group == "structure"
    ]
    cal = json_safe(
        derive_calibration(
            [r.comparison for r in logs],
            built_weights=structure or None,
            log_ids=[r.id for r in logs],
        )
    )
    lo, hi = PLAUSIBLE_RANGE
    for name, f in cal["factors"].items():
        f["effect"] = FACTOR_EFFECT.get(name, "")
        value = f.get("value")
        plausible = isinstance(value, int | float) and lo <= value <= hi
        f["plausible"] = plausible if f.get("valid") else None
        f["applicable"] = bool(f.get("valid") and plausible and name in CALIBRATION_FACTORS)
        if name == "cruise_power":
            f["why_not"] = (
                "Not applied directly: the cruise drag factor carries the same measurement, "
                "corrected for the propeller's efficiency."
            )
        elif f.get("valid") and not plausible:
            f["why_not"] = (
                f"{value:.2f} is outside {lo:g}-{hi:g}: more likely a wrong take-off mass, an "
                "uncalibrated current sensor or a log from another aircraft than a model "
                "error, so it is not offered."
            )
        elif not f.get("valid"):
            f["why_not"] = f.get("reason") or "Not available."
    cal["log_ids"] = [r.id for r in logs]
    cal["logs"] = [
        {"id": r.id, "filename": r.filename, "sample": r.sample, "version_id": r.version_id}
        for r in logs
    ]
    return cal


def applied_calibration(db: Session, project_id: int) -> dict[str, Any] | None:
    rows = db.scalars(
        select(Calibration).where(Calibration.project_id == project_id).order_by(Calibration.id)
    ).all()
    if not rows:
        return None
    log_ids = sorted({i for r in rows for i in (r.source_log_ids or [])})
    return {
        "factors": {
            r.name: {
                "name": r.name,
                "label": FACTOR_INFO.get(r.name, (r.name, ""))[0],
                "value": r.value,
                "uncertainty": r.uncertainty,
                "n_logs": r.n_logs,
                "source_logs": r.source_log_ids,
                "effect": FACTOR_EFFECT.get(r.name, ""),
            }
            for r in rows
        },
        "n_logs": len(log_ids),
        "source_log_ids": log_ids,
        "applied_at": max(r.created_at for r in rows),
    }


def analysis_calibration(db: Session, project_id: int) -> dict[str, Any] | None:
    """The ``calibration`` input of :func:`app.engine.analysis.run_analysis`, or None."""
    applied = applied_calibration(db, project_id)
    if not applied:
        return None
    return {
        "factors": {
            name: {"value": f["value"], "uncertainty": f["uncertainty"]}
            for name, f in applied["factors"].items()
        },
        "n_logs": applied["n_logs"],
    }


def apply_calibration(
    db: Session, owner_id: int, project: Project, names: list[str] | None
) -> list[str]:
    """Store the applicable proposed factors (optionally only ``names``) as the project's
    calibration, replacing any applied before. Returns the names applied (empty: nothing)."""
    cal = proposed_calibration(db, project)
    chosen = {
        name: f
        for name, f in cal["factors"].items()
        if f.get("applicable") and (names is None or name in names)
    }
    if not chosen:
        return []
    versions = {lg["version_id"] for lg in cal["logs"]}
    version_id = next(iter(versions)) if len(versions) == 1 else None
    db.execute(delete(Calibration).where(Calibration.project_id == project.id))
    for name, f in chosen.items():
        db.add(
            Calibration(
                owner_id=owner_id,
                project_id=project.id,
                version_id=version_id if name != "structural_mass" else None,
                name=name,
                value=float(f["value"]),
                uncertainty=float(f["uncertainty"]),
                n_logs=int(f.get("n_logs") or 0),
                source_log_ids=[int(i) for i in f.get("source_logs") or []],
                details={
                    k: f.get(k)
                    for k in ("per_log", "per_group", "method", "birge_ratio", "source")
                    if f.get(k) is not None
                },
            )
        )
    db.commit()
    return sorted(chosen)


def undo_calibration(db: Session, project_id: int) -> int:
    result = db.execute(delete(Calibration).where(Calibration.project_id == project_id))
    db.commit()
    return int(getattr(result, "rowcount", 0) or 0)


PREVIEW_KEYS = ("takeoff_mass", "hover_power", "cruise_power", "endurance_cruise", "range")


def calibration_preview(
    settings: Settings,
    db: Session,
    owner_id: int,
    project: Project,
    factors: dict[str, dict[str, float]],
    n_logs: int,
) -> dict[str, Any]:
    """Headline numbers of the draft without and with these factors (quick analyses)."""
    from app import parts_service
    from app.engine.analysis import run_analysis
    from app.routers.common import current_mission, current_parameters
    from app.routers.settings import effective_settings

    settings_doc, _meta = effective_settings(db, owner_id)
    parameters = current_parameters(project.draft_parameters)
    mission = current_mission(project.draft_mission)
    cache_dir = str(polar_cache_dir(settings))
    before = parts_service.generic_analysis(parameters, mission, settings_doc, cache_dir)
    if not before.get("valid") or not factors:
        return {"rows": [], "reason": "The draft could not be analysed." if factors else None}
    after = run_analysis(
        parameters,
        mission,
        settings_doc,
        mode="fast",
        cache_dir=cache_dir,
        uncertainty=False,
        calibration={"factors": factors, "n_logs": n_logs},
    )
    rows = []
    for key in PREVIEW_KEYS:
        a = (before.get("summary") or {}).get(key) or {}
        b = (after.get("summary") or {}).get(key) or {}
        va, vb = a.get("value"), b.get("value")
        if not isinstance(va, int | float) or not isinstance(vb, int | float):
            continue
        rows.append(
            {
                "key": key,
                "label": a.get("label") or key,
                "unit": a.get("unit") or "",
                "explain": a.get("explain") or "",
                "before": va,
                "after": vb,
                "change_pct": round((vb / va - 1) * 100, 1) if va else None,
            }
        )
    return json_safe({"rows": rows, "reason": None, "source": "the draft (quick analysis)"})
