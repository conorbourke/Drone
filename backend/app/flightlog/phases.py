"""Flight phase detection: take-off hover, transition, cruise, back-transition, landing hover.

Signals, in order of preference (each segment records which one decided its start and end):

1. **Lift motors running** from the VTOL motor outputs (``RCOU`` on the channels whose
   ``SERVOn_FUNCTION`` is a motor, normalised between ``Q_M_PWM_MIN`` and ``Q_M_PWM_MAX``; on a
   tiltrotor, motors in ``Q_TILT_MASK`` count as lifting only while ``TILT`` is below 75°).
   Fallbacks: ``QTUN`` rows (ArduPlane writes them at 25 Hz while the VTOL motors are active),
   then the flight mode (Q modes = VTOL).
2. **Airspeed** from the airspeed sensor (``ARSP``), else the autopilot's estimate (``CTUN.As``),
   else GPS ground speed (wind then biases it; stated).
3. **Events**: ``MODE`` changes (Q modes against fixed-wing modes) and the transition messages
   ``MSG`` "Transition done" / "Transition FW done" refine the start and end of transitions.

Each 0.2 s bucket inside a flight is classed as hover (lift motors on, airspeed below the hover
limit), cruise (lift motors off, airspeed at least 80 % of ``AIRSPEED_MIN``) or in between;
runs shorter than 1 s are absorbed by their neighbour and lift-motor blips shorter than 3 s
inside wing flight (Q assist) stay part of the cruise. The runs are then labelled in order.
"""

from __future__ import annotations

import math
import re
from typing import Any

from app.flightlog.parse import (
    MOTOR_FUNCTIONS,
    THROTTLE_FUNCTIONS,
    VTOL_MODES,
    ParsedLog,
)

LIFT_ON = 0.05  # normalised motor output above which a lift motor counts as running
TILT_FORWARD_DEG = 75.0
MIN_RUN_S = 1.0
ASSIST_GAP_S = 3.0
AIR_ALT_M = 1.0
GROUND_ALT_M = 0.3

LABELS = {
    "takeoff_hover": "Take-off hover",
    "transition": "Transition to wing flight",
    "cruise": "Cruise (wing flight)",
    "back_transition": "Transition back to hover",
    "landing_hover": "Landing hover",
    "hover": "Hover",
    "vtol_assist": "Wing flight with lift motors assisting",
    "fw_climb": "Fixed-wing take-off and climb",
    "fw_descent": "Fixed-wing descent and landing",
    "vtol_forward": "Forward flight on the lift motors (transition not completed)",
}


def _param(params: dict[str, float], *names: str, default: float | None = None) -> float | None:
    for n in names:
        if n in params:
            return params[n]
    return default


def servo_map(params: dict[str, float]) -> dict[str, Any]:
    """VTOL motor, forward throttle and tilt output channels from the logged parameters."""
    motors: dict[int, int] = {}
    throttle: list[int] = []
    source = "SERVOn_FUNCTION"
    for ch in range(1, 33):
        fn = _param(params, f"SERVO{ch}_FUNCTION", f"RC{ch}_FUNCTION")
        if fn is None:
            continue
        fn = int(fn)
        if fn in MOTOR_FUNCTIONS:
            motors[MOTOR_FUNCTIONS[fn]] = ch
        elif fn in THROTTLE_FUNCTIONS:
            throttle.append(ch)
    if not motors and _param(params, "Q_ENABLE", default=0):
        # ArduPlane's QuadPlane default: motors 1-4 on outputs 5-8.
        motors = {1: 5, 2: 6, 3: 7, 4: 8}
        source = "QuadPlane default outputs 5-8 (no SERVOn_FUNCTION parameters logged)"
    notes = []
    if int(_param(params, "Q_FRAME_CLASS", default=1) or 1) == 7 and 7 in motors:
        # Tri frames use the "motor 7" output for the tail yaw servo (AP_MotorsTri), not a motor.
        notes.append(f"Motor 7 output (C{motors.pop(7)}) is the tri frame's yaw servo.")
    if not throttle and _param(params, "SERVO3_FUNCTION", "RC3_FUNCTION") is None:
        throttle = [3]
    tilt_mask = int(_param(params, "Q_TILT_MASK", default=0) or 0)
    return {
        "motors": dict(sorted(motors.items())),
        "throttle": throttle,
        "tilt_mask": tilt_mask,
        "tilting_motors": [mn for mn in motors if tilt_mask & (1 << (mn - 1))],
        "source": source,
        "notes": notes,
    }


def _pwm_range(params: dict[str, float], ch: int) -> tuple[float, float]:
    lo = _param(params, "Q_M_PWM_MIN", default=0) or 0
    hi = _param(params, "Q_M_PWM_MAX", default=0) or 0
    if lo <= 0 or hi <= lo:
        lo = _param(params, f"SERVO{ch}_MIN", f"RC{ch}_MIN", default=1000) or 1000
        hi = _param(params, f"SERVO{ch}_MAX", f"RC{ch}_MAX", default=2000) or 2000
    return lo, max(hi, lo + 1)


def _nan(v: float) -> bool:
    return v != v


def lift_signal(log: ParsedLog) -> tuple[list[float], str]:
    """Per-bucket lift-motor output 0..1 (NaN where unknown) and how it was obtained."""
    n = log.n_buckets
    sm = servo_map(log.params)
    motors = {mn: ch for mn, ch in sm["motors"].items() if ch in log.rcou}
    tilt = log.ch("tilt_deg")
    if motors:
        out = [math.nan] * n
        ranges = {ch: _pwm_range(log.params, ch) for ch in motors.values()}
        for i in range(n):
            vals = []
            for mn, ch in motors.items():
                pwm = log.rcou[ch].mean[i]
                if _nan(pwm):
                    continue
                lo, hi = ranges[ch]
                x = min(1.0, max(0.0, (pwm - lo) / (hi - lo)))
                if mn in sm["tilting_motors"] and tilt is not None:
                    td = tilt.mean[i]
                    if not _nan(td) and td >= TILT_FORWARD_DEG:
                        x = 0.0  # tilted forward: thrust is propulsion, not lift
                vals.append(x)
            if vals:
                out[i] = sum(vals) / len(vals)
        chs = ", ".join(f"C{ch}" for ch in motors.values())
        src = f"VTOL motor outputs (RCOU {chs}, {sm['source']})"
        if sm["tilting_motors"]:
            src += (
                f"; tilting motors {sm['tilting_motors']} count as lifting only while TILT is "
                f"below {TILT_FORWARD_DEG:g}°"
            )
        return out, src
    qtun = log.ch("qtun")
    if qtun is not None:
        out = [1.0 if not _nan(v) else 0.0 for v in qtun.mean]
        return out, "QTUN rows present (written while the VTOL motors are active)"
    if log.modes:
        out = [0.0] * n
        for i in range(n):
            out[i] = 1.0 if mode_at(log, log.t(i))[0] in VTOL_MODES else 0.0
        return out, "flight mode only (Q modes counted as VTOL; no motor outputs or QTUN logged)"
    return [math.nan] * n, "none (no motor outputs, QTUN or modes logged)"


def airspeed_signal(log: ParsedLog) -> tuple[list[float], str]:
    for name, src in (
        ("airspeed_sensor_mps", "airspeed sensor (ARSP)"),
        ("airspeed_est_mps", "autopilot airspeed estimate (CTUN.As)"),
        ("groundspeed_mps", "GPS ground speed (no airspeed logged; wind biases it)"),
    ):
        c = log.ch(name)
        if c is not None:
            vals = list(c.mean)
            # fill short gaps forward so a slower channel still covers every bucket
            last = math.nan
            for i, v in enumerate(vals):
                if _nan(v):
                    vals[i] = last
                else:
                    last = v
            return vals, src
    return [math.nan] * log.n_buckets, "none"


def mode_at(log: ParsedLog, t: float) -> tuple[int, str]:
    cur = (-1, "UNKNOWN")
    for e in log.modes:
        if e["t_s"] <= t:
            cur = (e["num"], e["name"])
        else:
            break
    return cur


def armed_signal(log: ParsedLog) -> tuple[list[bool], str]:
    n = log.n_buckets
    if not log.arm_events:
        return [True] * n, "no arming events logged: the whole log is treated as armed"
    out = [False] * n
    ev = log.arm_events
    state = not ev[0]["armed"]  # before the first event the state is the opposite
    k = 0
    for i in range(n):
        t = log.t(i)
        while k < len(ev) and ev[k]["t_s"] <= t:
            state = ev[k]["armed"]
            k += 1
        out[i] = state
    return out, "arming events (" + "/".join(sorted({e["source"] for e in ev})) + ")"


def _runs(states: list[str]) -> list[list[Any]]:
    runs: list[list[Any]] = []
    for i, s in enumerate(states):
        if runs and runs[-1][0] == s:
            runs[-1][2] = i + 1
        else:
            runs.append([s, i, i + 1])
    return runs


def _smooth(states: list[str], min_len: int) -> list[str]:
    """Absorb runs shorter than ``min_len`` buckets into the previous (or next) run."""
    for _ in range(5):
        runs = _runs(states)
        changed = False
        for k, (s, a, b) in enumerate(runs):
            if b - a >= min_len or len(runs) == 1:
                continue
            repl = runs[k - 1][0] if k > 0 else runs[k + 1][0]
            if repl != s:
                for i in range(a, b):
                    states[i] = repl
                changed = True
        if not changed:
            break
    return states


def _find_msg(log: ParsedLog, pattern: str, t_lo: float, t_hi: float) -> dict[str, Any] | None:
    rx = re.compile(pattern, re.IGNORECASE)
    for m in log.messages:
        if t_lo <= m["t_s"] <= t_hi and rx.search(m["text"]):
            return m
    return None


def _find_mode_change(
    log: ParsedLog, t_lo: float, t_hi: float, vtol: bool, last: bool = False
) -> dict[str, Any] | None:
    hits = [e for e in log.modes if t_lo <= e["t_s"] <= t_hi and ((e["num"] in VTOL_MODES) == vtol)]
    if not hits:
        return None
    return hits[-1] if last else hits[0]


def detect_phases(log: ParsedLog) -> dict[str, Any]:
    """Split the log into labelled flight phases with the signal that decided each boundary."""
    n = log.n_buckets
    bs = log.bucket_s
    p = log.params
    notes: list[str] = []
    v_min = _param(p, "AIRSPEED_MIN", "ARSPD_FBW_MIN", default=None)
    if v_min is None or v_min <= 0:
        v_min = 10.0
        notes.append("AIRSPEED_MIN not logged: 10 m/s assumed for the wing-flight threshold.")
    v_cruise_min = 0.8 * v_min
    v_hover_max = max(3.0, 0.35 * v_min)
    lift, lift_src = lift_signal(log)
    aspd, aspd_src = airspeed_signal(log)
    armed, armed_src = armed_signal(log)
    alt_c = log.ch("alt_m")
    alt = list(alt_c.mean) if alt_c is not None else [math.nan] * n
    has_vtol = bool(_param(p, "Q_ENABLE", default=0)) or any(
        not _nan(x) and x > LIFT_ON for x in lift
    )
    thresholds = {
        "lift_on_fraction": LIFT_ON,
        "cruise_min_airspeed_mps": v_cruise_min,
        "hover_max_airspeed_mps": v_hover_max,
        "in_air_altitude_m": AIR_ALT_M,
        "min_run_s": MIN_RUN_S,
        "assist_gap_s": ASSIST_GAP_S,
    }
    signals = {
        "lift": lift_src,
        "airspeed": aspd_src,
        "armed": armed_src,
        "altitude": log.sources.get("alt_m", "none"),
    }
    if alt_c is None:
        notes.append(
            "No altitude logged: in-air detection falls back to lift-motor output and airspeed."
        )

    # ---- flights: armed and off the ground ----
    air = [False] * n
    for i in range(n):
        if not armed[i]:
            continue
        a = alt[i]
        if not _nan(a):
            air[i] = a > AIR_ALT_M
        else:
            li, v = lift[i], aspd[i]
            air[i] = (not _nan(li) and li > 0.3) or (not _nan(v) and v > v_cruise_min)
    flights: list[tuple[int, int]] = []
    for s, a, b in _runs(["A" if x else "G" for x in air]):
        if s != "A":
            continue
        if flights and (a - flights[-1][1]) * bs < 5.0:
            flights[-1] = (flights[-1][0], b)
        else:
            flights.append((a, b))
    flights = [(a, b) for a, b in flights if (b - a) * bs >= 3.0]
    # extend each flight to the moment the aircraft left / reached the ground
    ext: list[tuple[int, int]] = []
    for a, b in flights:
        lim = int(15 / bs)
        k = 0
        while a > 0 and k < lim and armed[a - 1] and not _nan(alt[a - 1]):
            if alt[a - 1] <= GROUND_ALT_M:
                break
            a -= 1
            k += 1
        k = 0
        while b < n and k < lim and armed[b] and not _nan(alt[b]):
            if alt[b] <= GROUND_ALT_M:
                break
            b += 1
            k += 1
        ext.append((a, b))
    flights = ext
    if not flights:
        notes.append(
            "No flight found: the aircraft was not armed or stayed below "
            f"{AIR_ALT_M:g} m (signals: {armed_src}; altitude {signals['altitude']})."
        )

    segments: list[dict[str, Any]] = []
    for fi, (a, b) in enumerate(flights):
        segments.extend(
            _flight_segments(
                log, fi + 1, a, b, lift, aspd, v_cruise_min, v_hover_max, has_vtol, signals
            )
        )
    return {
        "phases": segments,
        "flights": [
            {
                "flight": k + 1,
                "takeoff_s": round(a * bs, 2),
                "landing_s": round(b * bs, 2),
                "duration_s": round((b - a) * bs, 2),
            }
            for k, (a, b) in enumerate(flights)
        ],
        "thresholds": thresholds,
        "signals": signals,
        "vtol": has_vtol,
        "method": __doc__.split("\n\n", 1)[1].strip() if __doc__ else "",
        "notes": notes,
    }


def _flight_segments(
    log: ParsedLog,
    flight: int,
    a: int,
    b: int,
    lift: list[float],
    aspd: list[float],
    v_cruise: float,
    v_hover: float,
    has_vtol: bool,
    signals: dict[str, str],
) -> list[dict[str, Any]]:
    bs = log.bucket_s
    states: list[str] = []
    for i in range(a, b):
        li = lift[i]
        v = aspd[i]
        lifting = (not _nan(li) and li > LIFT_ON) if has_vtol else False
        if not has_vtol:
            states.append("C" if (not _nan(v) and v >= v_cruise) else "T")
        elif lifting:
            states.append("H" if (_nan(v) or v < v_hover) else "T")
        else:
            states.append("C" if (_nan(v) or v >= v_cruise) else "T")
    states = _smooth(states, max(1, int(MIN_RUN_S / bs)))
    # short lift-motor blips inside wing flight (Q assist) stay cruise
    runs = _runs(states)
    for k in range(1, len(runs) - 1):
        s, i0, i1 = runs[k]
        between_cruise = runs[k - 1][0] == "C" and runs[k + 1][0] == "C"
        if s != "C" and between_cruise and (i1 - i0) * bs < ASSIST_GAP_S:
            for i in range(i0, i1):
                states[i] = "C"
    runs = [(s, i0 + a, i1 + a) for s, i0, i1 in _runs(states)]

    def t(i: int) -> float:
        return i * bs

    segs: list[dict[str, Any]] = []

    def add(key: str, i0: int, i1: int, start: str, end: str, **extra: Any) -> None:
        if i1 <= i0:
            return
        segs.append(
            {
                "key": key,
                "label": LABELS[key],
                "flight": flight,
                "start_s": round(t(i0), 2),
                "end_s": round(t(i1), 2),
                "duration_s": round(t(i1) - t(i0), 2),
                "i0": i0,
                "i1": i1,
                "decided_by": {"start": start, "end": end},
                "modes": sorted(
                    {mode_at(log, t(i0))[1]}
                    | {e["name"] for e in log.modes if t(i0) <= e["t_s"] < t(i1)}
                ),
                **extra,
            }
        )

    cruise_runs = [r for r in runs if r[0] == "C"]
    lift_txt = f"lift motors ({signals['lift']})"
    spd_txt = f"airspeed ({signals['airspeed']})"
    takeoff_txt = "left the ground (altitude above 0.3 m, armed)"
    landing_txt = "back on the ground (altitude below 0.3 m or disarmed)"
    if not cruise_runs:
        fwd = [r for r in runs if r[0] == "T" and (r[2] - r[1]) * bs >= 10.0]
        if has_vtol and fwd:
            f0, f1 = fwd[0][1], fwd[-1][2]
            why0 = f"{spd_txt} rose above {v_hover:.1f} m/s with the lift motors running"
            mc = _find_mode_change(log, t(f0) - 30, t(f0) + 1, vtol=False, last=True)
            if mc is not None and mc["t_s"] > t(a):
                f0 = round(mc["t_s"] / bs)
                why0 = f"MODE change to {mc['name']} at {mc['t_s']:.1f} s"
            why1 = f"{spd_txt} fell below {v_hover:.1f} m/s"
            mc = _find_mode_change(log, t(f1) - 30, t(f1) + 5, vtol=True)
            if mc is not None and t(f0) < mc["t_s"] < t(b):
                f1 = round(mc["t_s"] / bs)
                why1 = f"MODE change to {mc['name']} at {mc['t_s']:.1f} s"
            note = (
                f"The lift motors never stopped and the airspeed stayed below "
                f"{v_cruise:.1f} m/s: the transition did not complete (check AIRSPEED_MIN, "
                "Q_TILT_MAX and the forward thrust)."
            )
            add("takeoff_hover", a, f0, takeoff_txt, why0)
            add("vtol_forward", f0, f1, why0, why1, note=note)
            add("landing_hover", f1, b, why1, landing_txt)
            return segs
        key = "hover" if has_vtol else "fw_climb"
        add(key, a, b, takeoff_txt, landing_txt, note="No wing flight found in this flight.")
        return segs
    if not has_vtol:
        c0, c1 = cruise_runs[0][1], cruise_runs[-1][2]
        add("fw_climb", a, c0, takeoff_txt, f"{spd_txt} reached {v_cruise:.1f} m/s")
        add("cruise", c0, c1, f"{spd_txt} at or above {v_cruise:.1f} m/s", "airspeed fell")
        add("fw_descent", c1, b, f"{spd_txt} fell below {v_cruise:.1f} m/s", landing_txt)
        return segs

    # ---- forward transition (before the first cruise run) ----
    first_c = cruise_runs[0][1]
    pre = [r for r in runs if r[2] <= first_c]
    hover_runs = [r for r in pre if r[0] == "H"]
    hover_end = hover_runs[-1][2] if hover_runs else a
    tr_start, tr_start_why = (
        hover_end,
        (f"{spd_txt} rose above {v_hover:.1f} m/s with the lift motors still running"),
    )
    mc = _find_mode_change(log, t(hover_end) - 30, t(first_c), vtol=False, last=True)
    if mc is not None and mc["t_s"] >= t(a):
        tr_start = max(a, min(first_c, round(mc["t_s"] / bs)))
        tr_start_why = f"MODE change to {mc['name']} at {mc['t_s']:.1f} s"
    msg = _find_msg(log, r"transition started", t(hover_end) - 30, t(first_c))
    if msg is not None and mc is None:
        tr_start = max(a, round(msg["t_s"] / bs))
        tr_start_why = f"MSG '{msg['text']}' at {msg['t_s']:.1f} s"
    tr_end, tr_end_why = first_c, f"{lift_txt} stopped and airspeed above {v_cruise:.1f} m/s"
    done = _find_msg(log, r"^transition (fw )?done", t(tr_start), t(first_c) + 15)
    reached = _find_msg(log, r"transition airspeed reached", t(tr_start), t(first_c) + 15)
    if done is not None:
        tr_end = max(tr_start + 1, round(done["t_s"] / bs))
        tr_end_why = f"MSG '{done['text']}' at {done['t_s']:.1f} s"
    extra_tr: dict[str, Any] = {}
    if reached is not None:
        extra_tr["airspeed_reached_s"] = round(reached["t_s"], 2)
        mm = re.search(r"([\d.]+)\s*$", reached["text"])
        if mm:
            extra_tr["airspeed_reached_mps"] = float(mm.group(1))
    if done is not None:
        extra_tr["done_s"] = round(done["t_s"], 2)
    add(
        "takeoff_hover",
        a,
        tr_start,
        takeoff_txt,
        tr_start_why,
    )
    add("transition", tr_start, tr_end, tr_start_why, tr_end_why, **extra_tr)

    # ---- cruise runs and what lies between them ----
    last_c_end = cruise_runs[-1][2]
    cur = tr_end
    for k, (_s, c0, c1) in enumerate(cruise_runs):
        c0 = max(c0, cur)
        if k + 1 < len(cruise_runs):
            nxt0 = cruise_runs[k + 1][1]
            between = [r for r in runs if r[1] >= c1 and r[2] <= nxt0]
            hov = [r for r in between if r[0] == "H"]
            if not hov:
                add("cruise", c0, c1, "previous phase ended", f"{lift_txt} started")
                add(
                    "vtol_assist",
                    c1,
                    nxt0,
                    f"{lift_txt} started in wing flight",
                    f"{lift_txt} stopped",
                )
            else:
                h0, h1 = hov[0][1], hov[-1][2]
                add("cruise", c0, c1, "previous phase ended", f"{lift_txt} started")
                add(
                    "back_transition",
                    c1,
                    h0,
                    f"{lift_txt} started",
                    f"{spd_txt} below {v_hover:.1f} m/s",
                )
                add("hover", h0, h1, f"{spd_txt} below {v_hover:.1f} m/s", f"{spd_txt} rose")
                add("transition", h1, nxt0, f"{spd_txt} rose", f"{lift_txt} stopped")
            cur = nxt0
            continue
        # last cruise run: back-transition and landing hover
        bt_start, bt_why = c1, f"{lift_txt} started again"
        mc = _find_mode_change(log, t(c1) - 30, t(c1) + 5, vtol=True)
        if mc is not None and t(c0) < mc["t_s"]:
            bt_start = round(mc["t_s"] / bs)
            bt_why = f"MODE change to {mc['name']} at {mc['t_s']:.1f} s"
        add(
            "cruise",
            c0,
            bt_start,
            tr_end_why if k == 0 else "previous phase ended",
            bt_why,
        )
        post_h = [r for r in runs if r[1] >= last_c_end and r[0] == "H"]
        bt_end = post_h[0][1] if post_h else b
        bt_end = max(bt_end, bt_start + 1)
        bt_end_why = f"{spd_txt} fell below {v_hover:.1f} m/s"
        vdone = _find_msg(log, r"transition vtol done", t(bt_start), t(b))
        if vdone is not None:
            bt_end = max(bt_start + 1, round(vdone["t_s"] / bs))
            bt_end_why = f"MSG '{vdone['text']}' at {vdone['t_s']:.1f} s"
        add("back_transition", bt_start, min(bt_end, b), bt_why, bt_end_why)
        add("landing_hover", min(bt_end, b), b, bt_end_why, landing_txt)
    return segs
