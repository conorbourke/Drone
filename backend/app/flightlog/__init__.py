"""Flight data (Phase 6): ArduPilot DataFlash logs against the design's predictions.

Public API:

* :func:`process_log` parses a ``.bin`` / ``.log`` file (streaming, bounded memory), detects
  the flight phases and returns a JSON-safe summary with per-phase statistics, decimated series
  (5 Hz) for charts, events and checks.
* :func:`compare_log` compares a processed log with a Phase 3 analysis result, phase by phase.
* :func:`derive_calibration` combines comparisons from one or more logs (and built weights)
  into calibration factors with uncertainties.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from typing import Any

from app.flightlog.parse import CHANNELS, PLANE_MODES, ParsedLog, parse_dataflash
from app.flightlog.phases import airspeed_signal, detect_phases, lift_signal, servo_map
from app.flightlog.stats import VIBE_FAIL, VIBE_SOURCE, VIBE_WARN, climb_rate, energy_check
from app.flightlog.stats import phase_stats as _phase_stats

__all__ = ["compare_log", "derive_calibration", "process_log"]


def compare_log(
    processed: dict[str, Any],
    analysis_result: dict[str, Any],
    *,
    logged_mass_kg: float | None = None,
) -> dict[str, Any]:
    """Predicted against measured per phase (see :func:`app.flightlog.compare.compare_log`)."""
    from app.flightlog.compare import compare_log as _compare

    return _compare(processed, analysis_result, logged_mass_kg=logged_mass_kg)


def derive_calibration(
    comparisons: list[dict[str, Any]],
    *,
    built_weights: list[dict[str, Any]] | None = None,
    log_ids: list[Any] | None = None,
) -> dict[str, Any]:
    """Calibration factors from comparisons (see :mod:`app.flightlog.calibrate`)."""
    from app.flightlog.calibrate import derive_calibration as _derive

    return _derive(comparisons, built_weights=built_weights, log_ids=log_ids)


SCHEMA = "flightlog/1"
ProgressFn = Callable[[float, str], None]

#: Channels exported as chart series (mean per 0.2 s), plus the extra statistics named here.
SERIES_CHANNELS = [
    "power_w",
    "current_a",
    "voltage_v",
    "energy_wh",
    "airspeed_mps",
    "groundspeed_mps",
    "alt_m",
    "roll_deg",
    "pitch_deg",
    "vibe_x",
    "vibe_y",
    "vibe_z",
    "throttle_fw",
    "lift_output",
    "batt_temp_c",
    "tilt_deg",
]


def _r(v: float, nd: int = 3) -> float | None:
    return round(v, nd) if v == v and math.isfinite(v) else None


def _vehicle(log: ParsedLog) -> dict[str, Any]:
    p = log.params
    fw = log.firmware or ""
    kind = "unknown"
    for name, k in (
        ("ArduPlane", "plane"),
        ("ArduCopter", "copter"),
        ("Rover", "rover"),
        ("ArduSub", "sub"),
    ):
        if name in fw:
            kind = k
    if kind == "unknown" and any(k.startswith("Q_") for k in p):
        kind = "plane"
    vtol = None
    if kind == "plane":
        if p.get("Q_ENABLE"):
            vtol = "quadplane"
            if p.get("Q_TILT_ENABLE") or p.get("Q_TILT_MASK"):
                vtol = "tiltrotor quadplane"
            if p.get("Q_TAILSIT_ENABLE"):
                vtol = "tailsitter"
        else:
            vtol = "fixed wing (no QuadPlane)"
    return {
        "type": kind,
        "configuration": vtol,
        "q_frame_class": p.get("Q_FRAME_CLASS"),
        "q_frame_type": p.get("Q_FRAME_TYPE"),
        "outputs": servo_map(p),
    }


IMPORTANT_TYPES = {
    "BAT": "battery voltage and current: no power, energy or battery comparisons",
    "MODE": "flight modes: phase boundaries from motor and airspeed signals only",
    "RCOU": "motor outputs: lift-motor state from QTUN or the flight mode instead",
    "ATT": "attitude statistics",
    "VIBE": "vibration levels",
    "GPS": "ground speed and start time",
    "CTUN": "the autopilot's airspeed estimate and throttle",
    "ARSP": "an airspeed sensor: the autopilot's estimate (or GPS ground speed) is used instead",
    "QTUN": "VTOL throttle (the motor outputs still show when the lift motors run)",
    "MSG": "transition messages: transition ends from the motor outputs instead",
    "PARM": "parameters: default thresholds and output channels are assumed",
}

KEY_PARAMS = [
    "Q_ENABLE",
    "Q_FRAME_CLASS",
    "Q_FRAME_TYPE",
    "Q_TILT_ENABLE",
    "Q_TILT_MASK",
    "Q_M_PWM_MIN",
    "Q_M_PWM_MAX",
    "Q_M_SPIN_MIN",
    "Q_ASSIST_SPEED",
    "Q_TRANSITION_MS",
    "AIRSPEED_MIN",
    "AIRSPEED_CRUISE",
    "AIRSPEED_MAX",
    "ARSPD_FBW_MIN",
    "ARSPD_TYPE",
    "ARSPD_USE",
    "BATT_MONITOR",
    "BATT_CAPACITY",
    "LOG_BITMASK",
    "LOG_DISARMED",
    "SERVO_BLH_TRATE",
    "SCHED_LOOP_RATE",
]


def _series(log: ParsedLog, lift: list[float], aspd: list[float]) -> dict[str, Any]:
    n = log.n_buckets
    out: dict[str, Any] = {}
    for name in SERIES_CHANNELS:
        if name == "lift_output":
            vals, unit, label = lift, "", "Lift-motor output (0-1)"
        elif name == "airspeed_mps":
            vals, unit, label = aspd, "m/s", "Airspeed"
        else:
            c = log.ch(name)
            if c is None:
                continue
            vals = c.last if name == "energy_wh" else c.mean
            unit, label = CHANNELS[name]
        if all(v != v for v in vals):
            continue
        out[name] = {"unit": unit, "label": label, "values": [_r(v, 3) for v in vals[:n]]}
    pw = log.ch("power_w")
    if pw is not None:
        out["power_w_max"] = {
            "unit": "W",
            "label": "Battery power, highest sample in each 0.2 s",
            "values": [_r(v, 2) for v in pw.hi],
        }
    v = log.ch("voltage_v")
    if v is not None:
        out["voltage_v_min"] = {
            "unit": "V",
            "label": "Battery voltage, lowest sample in each 0.2 s",
            "values": [_r(x, 3) for x in v.lo],
        }
    return {
        "rate_hz": 1 / log.bucket_s,
        "t_s": [round((i + 0.5) * log.bucket_s, 2) for i in range(n)],
        "channels": out,
    }


def process_log(
    path: str | os.PathLike[str], *, progress: ProgressFn | None = None
) -> dict[str, Any]:
    """Parse one ArduPilot log and return its JSON-safe summary.

    Shape: ``{schema, file, firmware, vehicle, start_time_utc, duration_s, messages, missing,
    params, phase_detection, phases, flight, energy_check, vibration, series, events, notes,
    timing}`` (see docs/phases/PHASE6.md and the module docstrings for every field)."""
    t0 = time.time()
    path = os.fspath(path)
    log = parse_dataflash(path, progress)
    t_parse = time.time() - t0
    det = detect_phases(log)
    lift, _ = lift_signal(log)
    aspd, aspd_src = airspeed_signal(log)
    climb = climb_rate(log)
    v_hover = det["thresholds"]["hover_max_airspeed_mps"]
    phases_out = []
    for seg in det["phases"]:
        st = _phase_stats(log, seg, climb, aspd, lift, v_hover, aspd_src)
        st["decided_by"] = seg["decided_by"]
        st["modes"] = seg["modes"]
        for k in ("airspeed_reached_mps", "airspeed_reached_s", "done_s", "note"):
            if k in seg:
                st[k] = seg[k]
        phases_out.append(st)
    # whole-flight statistics (take-off to landing of every flight)
    flight_stats = None
    if det["flights"]:
        bs = log.bucket_s
        whole = {
            "key": "flight",
            "label": "Whole flight",
            "flight": 0,
            "start_s": det["flights"][0]["takeoff_s"],
            "end_s": det["flights"][-1]["landing_s"],
            "i0": round(det["flights"][0]["takeoff_s"] / bs),
            "i1": round(det["flights"][-1]["landing_s"] / bs),
        }
        flight_stats = _phase_stats(log, whole, climb, aspd, lift, v_hover, aspd_src)
    whole_log = (
        _phase_stats(
            log,
            {
                "key": "log",
                "label": "Whole log",
                "start_s": 0.0,
                "end_s": round(log.duration_s, 2),
                "i0": 0,
                "i1": log.n_buckets,
            },
            climb,
            aspd,
            lift,
            v_hover,
            aspd_src,
        )
        if log.n_buckets
        else None
    )

    present = set(log.counts)
    missing = [
        {"type": t, "effect": eff}
        for t, eff in IMPORTANT_TYPES.items()
        if t not in present and not (t == "BAT" and "CURR" in present)
    ]
    notes = list(log.notes) + det["notes"]
    if "BAT" not in present and "CURR" not in present:
        notes.append("No battery messages: power and energy cannot be measured from this log.")
    vib_levels = [p.get("vibration", {}).get("level") for p in phases_out]
    vib_worst = (
        "fail"
        if "fail" in vib_levels
        else "warn"
        if "warn" in vib_levels
        else "ok"
        if "ok" in vib_levels
        else "unknown"
    )
    result = {
        "schema": SCHEMA,
        "file": {"name": os.path.basename(path), "size_bytes": log.size_bytes},
        "firmware": log.firmware,
        "vehicle": _vehicle(log),
        "start_time_utc": log.gps_time_utc,
        "duration_s": round(log.duration_s, 2),
        "boot_time_start_s": round(log.t0_us / 1e6, 3) if log.t0_us is not None else None,
        "messages": log.counts,
        "missing": missing,
        "params": {k: log.params[k] for k in KEY_PARAMS if k in log.params},
        "param_count": len(log.params),
        "phase_detection": {
            k: det[k] for k in ("flights", "thresholds", "signals", "vtol", "method")
        },
        "phases": phases_out,
        "flight": flight_stats,
        "whole_log": whole_log,
        "energy_check": energy_check(log),
        "battery": {
            "source": log.battery.get("source"),
            "first_voltage_v": _r(log.battery["first"].get("Volt", math.nan), 3),
            "first_rest_voltage_v": _r(log.battery["first"].get("VoltR", math.nan), 3),
            "integrated_i2_dt_a2s": _r(log.battery.get("i2_dt_a2s", math.nan), 1),
            "capacity_mah_param": log.params.get("BATT_CAPACITY"),
        },
        "vibration": {
            "worst_level": vib_worst,
            "thresholds_mps2": {"warn": VIBE_WARN, "fail": VIBE_FAIL},
            "source": VIBE_SOURCE,
        },
        "series": _series(log, lift, aspd),
        "events": {
            "modes": [
                {"t_s": round(e["t_s"], 2), "mode": e["name"], "num": e["num"]} for e in log.modes
            ],
            "messages": [{"t_s": round(m["t_s"], 2), "text": m["text"]} for m in log.messages],
            "arming": [{"t_s": round(e["t_s"], 2), "armed": e["armed"]} for e in log.arm_events],
        },
        "notes": notes,
        "timing": {
            "parse_s": round(t_parse, 3),
            "total_s": round(time.time() - t0, 3),
        },
    }
    if progress:
        progress(1.0, "Done")
    return result


# Plane mode names for callers that only have numbers.
MODE_NAMES = PLANE_MODES
