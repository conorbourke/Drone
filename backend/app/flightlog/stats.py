"""Per-phase statistics from the decimated series and the full-rate integrals.

Power percentiles and maxima come from the 0.2 s bucket means and maxima (so a peak is the
highest 0.2 s average or single sample, not a smoothed value); energy and charge per phase come
from the full-rate integrals (cumulative values at the bucket edges), so they do not depend on
the decimation. "Steady" subsets exclude climbs, descents, turns and speed changes; they are the
values the comparison and calibration use.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from app.flightlog.parse import ParsedLog
from app.flightlog.phases import LIFT_ON, servo_map

#: ArduPilot vibration guidance ("Measuring Vibration", ardupilot.org common docs, used by
#: Copter and Plane): below 30 m/s² normally acceptable, above 30 may cause problems, above
#: 60 nearly always causes problems with position or altitude hold; clipping should stay zero.
VIBE_WARN = 30.0
VIBE_FAIL = 60.0
VIBE_SOURCE = (
    "ArduPilot documentation, 'Measuring Vibration' "
    "(https://ardupilot.org/copter/docs/common-measuring-vibration.html, also linked from the "
    "Plane docs): vibration levels below 30 m/s² are normally acceptable, levels above 30 m/s² "
    "may have problems and above 60 m/s² nearly always have problems with position or altitude "
    "hold; the accelerometer clipping counts should stay at zero."
)
STEADY_CLIMB_HOVER = 0.5  # m/s
STEADY_CLIMB_CRUISE = 1.0  # m/s
STEADY_ROLL_DEG = 15.0
STEADY_SPEED_BAND = 2.0  # m/s around the phase median
CORRELATION_S = 1.0  # assumed correlation time of power fluctuations (gusts, control activity)
HOVER_KEYS = {"takeoff_hover", "landing_hover", "hover"}


def _vals(arr: Any, i0: int, i1: int) -> list[float]:
    return [v for v in arr[i0:i1] if v == v]


def _pct(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * q
    f = math.floor(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def _mean(v: list[float]) -> float | None:
    return sum(v) / len(v) if v else None


def _rms(v: list[float]) -> float | None:
    return math.sqrt(sum(x * x for x in v) / len(v)) if v else None


def _r(x: float | None, nd: int = 3) -> float | None:
    if x is None or not math.isfinite(x):
        return None
    return round(x, nd)


def climb_rate(log: ParsedLog) -> list[float]:
    """Vertical speed per bucket (m/s) from the altitude series, centred over about 1 s."""
    c = log.ch("alt_m")
    n = log.n_buckets
    if c is None:
        return [math.nan] * n
    alt = c.mean
    k = max(1, int(0.5 / log.bucket_s))
    out = [math.nan] * n
    for i in range(n):
        a, b = max(0, i - k), min(n - 1, i + k)
        if b > a and alt[a] == alt[a] and alt[b] == alt[b]:
            out[i] = (alt[b] - alt[a]) / ((b - a) * log.bucket_s)
    return out


def _cum_at(arr: Any, i: int) -> float | None:
    """Cumulative value at the end of bucket ``i`` (the last known value at or before it)."""
    j = min(i, len(arr) - 1)
    while j >= 0:
        v = arr[j]
        if v == v:
            return v
        j -= 1
    return 0.0 if i >= 0 else None


def _between(arr: Any, i0: int, i1: int) -> float | None:
    if not len(arr):
        return None
    end = _cum_at(arr, i1 - 1)
    start = _cum_at(arr, i0 - 1) if i0 > 0 else 0.0
    if end is None or start is None:
        return None
    return end - start


def vibration_level(v: float | None) -> str:
    if v is None:
        return "unknown"
    return "fail" if v > VIBE_FAIL else ("warn" if v > VIBE_WARN else "ok")


def _vibe_block(log: ParsedLog, i0: int, i1: int) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    worst = None
    for ax in ("x", "y", "z"):
        c = log.ch("vibe_" + ax)
        if c is None:
            continue
        vals = sorted(_vals(c.mean, i0, i1))
        hi = _vals(c.hi, i0, i1)
        if not vals:
            continue
        mx = max(hi) if hi else None
        out[ax] = {
            "mean": _r(_mean(vals), 2),
            "p95": _r(_pct(vals, 0.95), 2),
            "max": _r(mx, 2),
        }
        p95 = _pct(vals, 0.95)
        if p95 is not None:
            worst = p95 if worst is None else max(worst, p95)
    if not out:
        return None
    clip_c = log.ch("clip")
    clips = None
    if clip_c is not None:
        clips = _between(clip_c.hi, i0, i1)
        clips = max(0, round(clips)) if clips is not None else None
    level = vibration_level(worst)
    if clips and level == "ok":
        level = "warn"
    out["clipping"] = clips
    out["worst_p95"] = _r(worst, 2)
    out["level"] = level
    msgs = {
        "ok": "Vibration is within ArduPilot's guidance (below 30 m/s²).",
        "warn": "Vibration is above 30 m/s² or the accelerometers clipped: check propeller "
        "balance, motor bearings and the autopilot's mounting.",
        "fail": "Vibration is above 60 m/s²: position and altitude hold will suffer; fix the "
        "vibration source before trusting this flight's numbers.",
        "unknown": "",
    }
    out["message"] = msgs[level]
    return out


def _motor_block(
    log: ParsedLog, i0: int, i1: int, mask: list[bool] | None
) -> dict[str, Any] | None:
    sm = servo_map(log.params)
    motors = {mn: ch for mn, ch in sm["motors"].items() if ch in log.rcou}
    if not motors:
        return None
    per = {}
    for mn, ch in motors.items():
        arr = log.rcou[ch].mean
        vals = [arr[i] for i in range(i0, i1) if arr[i] == arr[i] and (mask is None or mask[i])]
        if vals:
            per[mn] = _mean(vals)
    if not per:
        return None
    out: dict[str, Any] = {
        "outputs_pwm": {f"motor{mn}": _r(v, 1) for mn, v in per.items()},
        "channels": {f"motor{mn}": f"C{ch}" for mn, ch in motors.items()},
    }
    lo = log.params.get("Q_M_PWM_MIN") or 1000
    hi = log.params.get("Q_M_PWM_MAX") or 2000
    norm = {mn: max(0.0, (v - lo) / max(1.0, hi - lo)) for mn, v in per.items()}
    out["outputs_fraction"] = {f"motor{mn}": _r(v, 3) for mn, v in norm.items()}
    active = {mn: v for mn, v in norm.items() if v > LIFT_ON}
    if len(active) >= 2:
        mean = sum(active.values()) / len(active)
        spread = (max(active.values()) - min(active.values())) / mean if mean > 0 else None
        out["spread_fraction"] = _r(spread, 3)
    frame_class = int(log.params.get("Q_FRAME_CLASS", 1) or 1)
    frame_type = int(log.params.get("Q_FRAME_TYPE", 1) or 1)
    if frame_class == 1 and frame_type in (1, 3) and all(k in active for k in (1, 2, 3, 4)):
        # ArduPilot quad X / H motor order: 1 front-right, 2 rear-left, 3 front-left, 4 rear-right
        front = (active[1] + active[3]) / 2
        rear = (active[2] + active[4]) / 2
        left = (active[2] + active[3]) / 2
        right = (active[1] + active[4]) / 2
        fr = (front - rear) / ((front + rear) / 2)
        lr = (left - right) / ((left + right) / 2)
        out["front_minus_rear_fraction"] = _r(fr, 3)
        out["left_minus_right_fraction"] = _r(lr, 3)
        hints = []
        if abs(fr) > 0.05:
            hints.append(
                f"The {'front' if fr > 0 else 'rear'} motors work {abs(fr) * 100:.0f} % harder "
                f"than the {'rear' if fr > 0 else 'front'} ones: the balance point is "
                f"{'ahead of' if fr > 0 else 'behind'} the centre of the lift motors (or the "
                "motors or propellers differ)."
            )
        if abs(lr) > 0.05:
            hints.append(
                f"The {'left' if lr > 0 else 'right'} motors work {abs(lr) * 100:.0f} % harder: "
                f"the aircraft is heavier on the {'left' if lr > 0 else 'right'} or a motor or "
                "propeller is weaker (yaw trim also shifts this)."
            )
        out["hints"] = hints or ["The lift motors share the load evenly (within 5 %)."]
    return out


def _esc_block(log: ParsedLog, i0: int, i1: int) -> dict[str, Any] | None:
    if not log.esc:
        return None
    out = {}
    for inst, d in sorted(log.esc.items()):
        rpm = _vals(d["rpm"].mean, i0, i1)
        cur = _vals(d["curr"].mean, i0, i1)
        tmp = _vals(d["temp"].hi, i0, i1)
        if not (rpm or cur or tmp):
            continue
        out[f"esc{inst}"] = {
            "rpm_mean": _r(_mean(rpm), 0),
            "current_mean_a": _r(_mean(cur), 2),
            "temp_max_c": _r(max(tmp) if tmp else None, 1),
        }
    return out or None


def _steady_mask(
    log: ParsedLog,
    seg: dict[str, Any],
    climb: list[float],
    aspd: list[float],
    lift: list[float],
    v_hover: float,
) -> list[bool]:
    i0, i1 = seg["i0"], seg["i1"]
    n = log.n_buckets
    mask = [False] * n
    roll_c = log.ch("roll_deg")
    if seg["key"] in HOVER_KEYS:
        for i in range(i0, i1):
            c = climb[i]
            li = lift[i]
            v = aspd[i]
            mask[i] = (
                (c != c or abs(c) < STEADY_CLIMB_HOVER)
                and (li != li or li > LIFT_ON)
                and (v != v or v < v_hover)
            )
    elif seg["key"] == "cruise":
        speeds = sorted(v for v in aspd[i0:i1] if v == v)
        med = _pct(speeds, 0.5)
        for i in range(i0, i1):
            c = climb[i]
            v = aspd[i]
            r = roll_c.mean[i] if roll_c is not None else math.nan
            mask[i] = (
                (c != c or abs(c) < STEADY_CLIMB_CRUISE)
                and (r != r or abs(r) < STEADY_ROLL_DEG)
                and (med is None or v != v or abs(v - med) < STEADY_SPEED_BAND)
            )
    else:
        for i in range(i0, i1):
            mask[i] = True
    return mask


def phase_stats(
    log: ParsedLog,
    seg: dict[str, Any],
    climb: list[float],
    aspd: list[float],
    lift: list[float],
    v_hover: float,
    aspd_source: str,
) -> dict[str, Any]:
    i0, i1 = seg["i0"], seg["i1"]
    dur = (i1 - i0) * log.bucket_s
    out: dict[str, Any] = {
        "key": seg["key"],
        "label": seg["label"],
        "flight": seg.get("flight", 1),
        "start_s": seg["start_s"],
        "end_s": seg["end_s"],
        "duration_s": _r(dur, 2),
    }
    pw = log.ch("power_w")
    cur = log.ch("current_a")
    volt = log.ch("voltage_v")
    e_c = log.channels["energy_wh"]
    q_c = log.channels["charge_mah"]
    mask = _steady_mask(log, seg, climb, aspd, lift, v_hover)
    if pw is not None:
        vals = _vals(pw.mean, i0, i1)
        srt = sorted(vals)
        energy = _between(e_c.last, i0, i1) if e_c.samples else None
        steady = [pw.mean[i] for i in range(i0, i1) if mask[i] and pw.mean[i] == pw.mean[i]]
        st_mean = _mean(steady)
        st_sd = statistics.pstdev(steady) if len(steady) > 1 else None
        n_eff = max(1.0, len(steady) * log.bucket_s / CORRELATION_S)
        out["power_w"] = {
            "mean": _r(
                energy * 3600 / dur
                if energy is not None and dur > 0 and len(vals) >= 0.95 * (i1 - i0)
                else _mean(vals),
                2,
            ),
            "p10": _r(_pct(srt, 0.10), 2),
            "median": _r(_pct(srt, 0.50), 2),
            "p90": _r(_pct(srt, 0.90), 2),
            "max": _r(max(_vals(pw.hi, i0, i1)) if _vals(pw.hi, i0, i1) else None, 2),
            "steady_mean": _r(st_mean, 2),
            "steady_sd": _r(st_sd, 2),
            "steady_sem": _r(st_sd / math.sqrt(n_eff) if st_sd is not None else None, 3),
            "steady_s": _r(len(steady) * log.bucket_s, 1),
        }
        out["energy_wh"] = _r(energy, 4)
    if cur is not None:
        cv = _vals(cur.mean, i0, i1)
        out["current_a"] = {
            "mean": _r(_mean(cv), 3),
            "max": _r(max(_vals(cur.hi, i0, i1)) if _vals(cur.hi, i0, i1) else None, 3),
            "steady_mean": _r(
                _mean(
                    [cur.mean[i] for i in range(i0, i1) if mask[i] and cur.mean[i] == cur.mean[i]]
                ),
                3,
            ),
        }
        out["charge_mah"] = _r(_between(q_c.last, i0, i1) if q_c.samples else None, 2)
    if volt is not None:
        vv = _vals(volt.mean, i0, i1)
        vr = log.ch("voltage_rest_v")
        sag = None
        if vr is not None:
            diffs = [
                vr.mean[i] - volt.mean[i]
                for i in range(i0, i1)
                if vr.mean[i] == vr.mean[i] and volt.mean[i] == volt.mean[i]
            ]
            sag = _mean(diffs)
        out["voltage_v"] = {
            "mean": _r(_mean(vv), 3),
            "min": _r(min(_vals(volt.lo, i0, i1)) if _vals(volt.lo, i0, i1) else None, 3),
            "start": _r(vv[0] if vv else None, 3),
            "end": _r(vv[-1] if vv else None, 3),
            "sag_mean": _r(sag, 3),
            "sag_note": "Resting voltage (BAT.VoltR, the autopilot's estimate without load) "
            "minus the loaded voltage."
            if sag is not None
            else "No resting-voltage estimate logged (BAT.VoltR).",
        }
    sp = [aspd[i] for i in range(i0, i1) if aspd[i] == aspd[i]]
    if sp:
        srt = sorted(sp)
        st_sp = [aspd[i] for i in range(i0, i1) if mask[i] and aspd[i] == aspd[i]]
        out["airspeed_mps"] = {
            "mean": _r(_mean(sp), 2),
            "min": _r(srt[0], 2),
            "median": _r(_pct(srt, 0.5), 2),
            "max": _r(srt[-1], 2),
            "steady_median": _r(_pct(sorted(st_sp), 0.5), 2),
            "source": aspd_source,
        }
    gs = log.ch("groundspeed_mps")
    if gs is not None:
        g = _vals(gs.mean, i0, i1)
        out["groundspeed_mps"] = {"mean": _r(_mean(g), 2), "max": _r(max(g) if g else None, 2)}
    alt = log.ch("alt_m")
    if alt is not None:
        a = _vals(alt.mean, i0, i1)
        if a:
            out["altitude_m"] = {
                "start": _r(a[0], 2),
                "end": _r(a[-1], 2),
                "min": _r(min(a), 2),
                "max": _r(max(a), 2),
                "mean": _r(_mean(a), 2),
            }
        cl = [climb[i] for i in range(i0, i1) if climb[i] == climb[i]]
        if cl:
            out["climb_rate_mps"] = {
                "mean": _r(_mean(cl), 2),
                "max": _r(max(cl), 2),
                "min": _r(min(cl), 2),
            }
    att = {}
    for axis in ("roll", "pitch"):
        c = log.ch(axis + "_deg")
        if c is None:
            continue
        v = _vals(c.mean, i0, i1)
        if v:
            att[axis] = {"mean_deg": _r(_mean(v), 2), "rms_deg": _r(_rms(v), 2)}
    if att:
        out["attitude"] = att
    vib = _vibe_block(log, i0, i1)
    if vib:
        out["vibration"] = vib
    bt = log.ch("batt_temp_c")
    if bt is not None:
        v = _vals(bt.mean, i0, i1)
        if v:
            out["battery_temp_c"] = {"mean": _r(_mean(v), 1), "max": _r(max(v), 1)}
    mot = _motor_block(log, i0, i1, mask if seg["key"] in HOVER_KEYS else None)
    if mot:
        out["motors"] = mot
    esc = _esc_block(log, i0, i1)
    if esc:
        out["esc"] = esc
    out["steady_definition"] = (
        f"Hover: climb rate within ±{STEADY_CLIMB_HOVER} m/s, lift motors running, airspeed "
        f"below {v_hover:.1f} m/s."
        if seg["key"] in HOVER_KEYS
        else (
            f"Cruise: climb rate within ±{STEADY_CLIMB_CRUISE} m/s, bank below "
            f"{STEADY_ROLL_DEG:g}°, airspeed within ±{STEADY_SPEED_BAND:g} m/s of the phase median."
            if seg["key"] == "cruise"
            else "Whole phase."
        )
    )
    return out


def energy_check(log: ParsedLog) -> dict[str, Any]:
    """Integrated volts x amps and amps against the autopilot's own BAT.EnrgTot / CurrTot."""
    b = log.battery
    out: dict[str, Any] = {
        "integrated_wh": _r(b.get("integrated_wh"), 4),
        "integrated_mah": _r(b.get("integrated_mah"), 2),
        "method": "Trapezoidal integration of every battery sample (not the 5 Hz series).",
    }
    first, last = b.get("first", {}), b.get("last", {})
    ok = True
    have = False
    for key, mine, unit in (
        ("EnrgTot", "integrated_wh", "wh"),
        ("CurrTot", "integrated_mah", "mah"),
    ):
        if key in first and key in last:
            have = True
            logged = last[key] - first[key]
            out[f"logged_{unit}"] = _r(logged, 4)
            ref = b.get(mine) or 0.0
            diff = (ref - logged) / logged * 100 if abs(logged) > 1e-9 else None
            out[f"diff_{unit}_pct"] = _r(diff, 2)
            if diff is not None and abs(diff) > 3.0:
                ok = False
    if not have:
        out["agrees"] = None
        out["note"] = "The log has no BAT.EnrgTot / CurrTot totals to check against."
    else:
        out["agrees"] = ok
        out["note"] = (
            "The integration agrees with the autopilot's own totals within 3 %."
            if ok
            else "The integration differs from the autopilot's totals by more than 3 %: the log "
            "may have gaps (dropped messages) or the battery monitor sampled faster than it "
            "logged; the autopilot's totals are the better figure for whole-flight energy."
        )
    return out
