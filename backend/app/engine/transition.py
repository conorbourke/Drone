"""Transition from hover to wing flight: thrust margin, power and speed range.

Quasi-steady speed sweep from 0 to 1.3 x cruise speed (docs/phases/PHASE3.md section 2):

* Wing lift: the aircraft accelerates at a fixed wing attitude whose lift coefficient is the one
  at 1.2 x stall speed, CL_t = CL_max / 1.44 (from AVL CL_alpha and the stall analysis), so the
  wing never flies closer than 1.2 V_s while the rotors still help; lift L_w = min(W, q S CL_t).
* Drag: q S (CD0 + CL^2 / (pi e A)) with the cruise CD0 and AVL's span efficiency (rotor H-force
  and propeller-wash effects on the wing are not modelled; stated).
* Forward force needed: drag + m a with a = 1.0 m/s^2 up to cruise speed (about 16 s from 0 to
  16 m/s, close to ArduPilot QuadPlane transitions; engine assumption), drag only above cruise.
* Tilt layouts: the tilting pair carries its static share of the remaining weight (moment balance
  about the CG, as in hover) and all the forward force; the tilt angle follows from the two,
  atan(F_x / F_z,pair), limited to tilt.max_angle_deg (the "tilt schedule"). When the limit is
  reached the fixed pair carries the rest of the weight (the tail trims the moment at speed).
  Available thrust: full throttle with the tilting rotors' axial inflow V sin(tilt); the fixed pair
  is treated as static (edgewise-flow gains ignored, conservative).
* Quad + pusher: the lift rotors carry W - L_w by their static shares; the pusher must give the
  forward force at airspeed V.
* Battery: the bus voltage is the loaded voltage at the reserve point (the landing transition is
  the weakest moment of the pack).

Two margins, two checks (they answer different questions):

* **Transition thrust margin** (``min_thrust_margin``, check ``transition_margin``): available /
  required thrust of the busiest rotor group over the *transition range*, from hover to
  ``transition_end_speed_mps`` = TRANSITION_END_BUFFER (1.1) x the speed at which the wing alone
  carries the weight at the transition attitude (``speed_wing_100pct_mps``, 1.2 V_s). By then the
  tilt has reached ``tilt.max_angle_deg`` too (the tilt reaches its limit at or before the wing
  takes the full weight). The 10 % speed buffer (21 % on lift) means a speed undershoot or a gust
  at the end of the transition does not hand weight back to rotors that are already tilted
  forward (engine assumption). Per point, ``thrust_margin`` is this margin and is ``None`` above
  the transition range, so the chart, its minimum marker and the check use the same numbers.
* **Top-speed thrust margin** (``top_speed``, check ``top_speed_margin``): on every wing-borne
  point of the full 0 to 1.3 x cruise sweep, the forward thrust the cruise propellers can give at
  full throttle (tilting pair at the tilt limit, or the pusher) divided by the forward force
  needed (drag, plus m a while still accelerating to cruise speed). Per point this is
  ``forward_thrust_margin``. Warn below TOP_SPEED_MARGIN_MIN (1.15 = the +/-15 % uncertainty of
  the generic propeller thrust coefficient, so the low end of the band still flies 1.3 x cruise).
  What limits the top speed is usually propeller unloading: the thrust of a low-pitch hover
  propeller falls towards zero as the advance ratio J = V / (n D) approaches its zero-thrust
  value (about 1.1 P/D + 0.05), and at full throttle the motor cannot spin it fast enough to
  keep J low. ``top_speed_mps`` is where this margin reaches 1.0 (searched up to 2 x the sweep
  end when the sweep does not reach it).
"""

from __future__ import annotations

import itertools
import math
from typing import Any

from app.engine import battery as bat
from app.engine.propulsion import (
    PROP_UNCERTAINTY,
    Motor,
    Propeller,
    max_thrust,
    operating_point,
)
from app.engine.quantity import G0, RHO_SL

ACCELERATION = 1.0
POINTS = 27
#: The sweep runs to this multiple of cruise speed (docs/phases/PHASE3.md section 2).
SWEEP_END_FACTOR = 1.3
#: Transition range ends at this multiple of the speed where the wing carries the full weight.
TRANSITION_END_BUFFER = 1.1
#: Warn when forward thrust available / needed falls below this on the wing-borne sweep: the
#: +/-15 % thrust-coefficient uncertainty of the generic propeller model (propulsion.py).
TOP_SPEED_MARGIN_MIN = 1.0 + PROP_UNCERTAINTY


def _bus(pack: dict[str, Any], power: float) -> float:
    return bat.loaded_voltage(pack, power, pack["v_reserve_ocv"])["voltage_v"]


def _speeds(v_end: float, extra: list[float]) -> list[float]:
    """Uniform grid 0..v_end plus the given speeds inside it, sorted, without near-duplicates."""
    grid = [v_end * k / (POINTS - 1) for k in range(POINTS)]
    grid += [v for v in extra if 0 < v < v_end]
    out: list[float] = []
    for v in sorted(grid):
        if not out or v - out[-1] > 1e-6 * max(1.0, v_end):
            out.append(v)
    return out


def transition_sweep(
    layout: str,
    mass_kg: float,
    wing_area: float,
    cl_max: float,
    cd0: float,
    span_efficiency: float,
    aspect_ratio: float,
    cruise_speed: float,
    front_share: float,
    max_tilt_deg: float,
    lift_prop: Propeller,
    lift_motor: Motor,
    pack: dict[str, Any],
    margin_min: float,
    pusher_prop: Propeller | None = None,
    pusher_motor: Motor | None = None,
    avionics_w: float = 0.0,
    download: float = 0.03,
) -> dict[str, Any]:
    w = mass_kg * G0
    cl_t = cl_max / 1.44
    tilt_max = math.radians(max_tilt_deg)
    s_front = min(1.0, max(0.0, front_share))
    tilt_share = s_front if layout == "front_tilt" else 1 - s_front
    v_end = SWEEP_END_FACTOR * cruise_speed
    v80 = math.sqrt(0.8 * w / (0.5 * RHO_SL * wing_area * cl_t))
    v100 = math.sqrt(w / (0.5 * RHO_SL * wing_area * cl_t))
    v_tr_end = TRANSITION_END_BUFFER * v100
    state = {"power": 0.0}

    def point(v: float) -> dict[str, Any]:
        q = 0.5 * RHO_SL * v * v
        lift = min(w, q * wing_area * cl_t)
        cl = lift / (q * wing_area) if q > 0 else 0.0
        drag = q * wing_area * (cd0 + cl * cl / (math.pi * span_efficiency * aspect_ratio))
        fx = drag + (mass_kg * ACCELERATION if v <= cruise_speed + 1e-9 else 0.0)
        fz = max(0.0, w - lift) * (1 + download)
        groups: list[dict[str, Any]] = []
        if layout == "quad_pusher":
            for share, name in ((s_front, "front lift pair"), (1 - s_front, "rear lift pair")):
                groups.append(
                    {
                        "name": name,
                        "count": 2,
                        "thrust": fz * share / 2,
                        "inflow": 0.0,
                        "prop": lift_prop,
                        "motor": lift_motor,
                        "forward": 0.0,
                    }
                )
            assert pusher_prop is not None and pusher_motor is not None
            groups.append(
                {
                    "name": "pusher",
                    "count": 1,
                    "thrust": fx,
                    "inflow": v,
                    "prop": pusher_prop,
                    "motor": pusher_motor,
                    "forward": 1.0,
                }
            )
            tilt = 0.0
        else:
            vz_t = fz * tilt_share
            tilt = math.atan2(fx, vz_t) if (fx > 0 or vz_t > 0) else tilt_max
            if tilt > tilt_max:
                tilt = tilt_max
                vz_t = fx / math.tan(tilt_max) if tilt_max < math.radians(89.99) else 0.0
                vz_t = min(vz_t, fz)
            t_tilt = math.hypot(fx, vz_t) / 2
            t_fixed = max(0.0, fz - vz_t) / 2
            groups.append(
                {
                    "name": "tilting pair",
                    "count": 2,
                    "thrust": t_tilt,
                    "inflow": v * math.sin(tilt),
                    "prop": lift_prop,
                    "motor": lift_motor,
                    "forward": math.sin(tilt),
                }
            )
            groups.append(
                {
                    "name": "fixed pair",
                    "count": 2,
                    "thrust": t_fixed,
                    "inflow": 0.0,
                    "prop": lift_prop,
                    "motor": lift_motor,
                    "forward": 0.0,
                }
            )
        p_guess = state["power"]
        vbus = _bus(pack, p_guess) if p_guess > 0 else pack["v_reserve_ocv"]
        total = avionics_w
        for _ in range(3):
            total = avionics_w
            for gr in groups:
                if gr["thrust"] > 1e-6:
                    op = operating_point(gr["prop"], gr["motor"], gr["thrust"], gr["inflow"], vbus)
                    gr["op"] = op
                    total += gr["count"] * op["battery_power_w"]
                else:
                    gr["op"] = None
            vbus = _bus(pack, total)
        state["power"] = total
        lv = bat.loaded_voltage(pack, total, pack["v_reserve_ocv"])
        margins = []
        fwd_avail = 0.0
        for gr in groups:
            mt = max_thrust(gr["prop"], gr["motor"], gr["inflow"], vbus)
            gr["available"] = mt["thrust_n"]
            gr["available_j"] = mt["j"]
            fwd_avail += gr["count"] * mt["thrust_n"] * gr["forward"]
            if gr["thrust"] > 0.01 * w / 4:
                margins.append(gr["available"] / gr["thrust"])
        wing_borne = lift >= w * (1 - 1e-9)
        fwd = next(gr for gr in groups if gr["forward"] > 0 or gr["name"] == "tilting pair")
        return {
            "speed_mps": v,
            "tilt_deg": math.degrees(tilt),
            "wing_lift_fraction": lift / w,
            "drag_n": drag,
            "forward_force_n": fx,
            "rotor_vertical_n": fz,
            # Transition margin; set to None above the transition range below.
            "thrust_margin": min(margins) if margins else None,
            "phase": "transition",
            "wing_borne": wing_borne,
            "forward_thrust_available_n": fwd_avail if wing_borne else None,
            "forward_thrust_margin": (fwd_avail / fx if wing_borne and fx > 0 else None),
            "forward_advance_ratio": fwd["available_j"] if wing_borne else None,
            "power_w": total,
            "battery_current_a": lv["current_a"],
            "bus_voltage_v": lv["voltage_v"],
            "groups": [
                {
                    "name": gr["name"],
                    "thrust_per_rotor_n": gr["thrust"],
                    "available_per_rotor_n": gr["available"],
                    "throttle": gr["op"]["throttle"] if gr["op"] else 0.0,
                }
                for gr in groups
            ],
        }

    points = [point(v) for v in _speeds(v_end, [v100, v_tr_end, cruise_speed])]
    tr_complete = v_tr_end <= v_end + 1e-9
    for p in points:
        if p["speed_mps"] > v_tr_end + 1e-9:
            p["phase"] = "wing_borne"
            p["thrust_margin"] = None
    tilt_done = next(
        (
            p["speed_mps"]
            for p in points
            if layout != "quad_pusher" and p["tilt_deg"] >= max_tilt_deg - 1e-6
        ),
        None,
    )

    # ----- Transition margin (hover to the end of the transition range) -----
    valid = [p for p in points if p["thrust_margin"] is not None]
    worst = min(valid, key=lambda p: p["thrust_margin"]) if valid else None
    m = worst["thrust_margin"] if worst else float("inf")
    level = "fail" if m < 1.0 else ("warn" if m < margin_min else "ok")
    if not tr_complete and level == "ok":
        level = "warn"

    # ----- Top-speed / cruise thrust margin (wing-borne part of the full sweep) -----
    fwd_pts = [p for p in points if p["forward_thrust_margin"] is not None]
    top_speed = None
    top_beyond_sweep = False
    for a, b in itertools.pairwise(fwd_pts):
        ma, mb = a["forward_thrust_margin"], b["forward_thrust_margin"]
        if ma >= 1 > mb:
            top_speed = a["speed_mps"] + (ma - 1) / (ma - mb) * (b["speed_mps"] - a["speed_mps"])
            break
    if top_speed is None and fwd_pts and fwd_pts[-1]["forward_thrust_margin"] >= 1:
        # Not reached inside the sweep: march on (points not added to the result).
        prev = fwd_pts[-1]
        step = 0.05 * v_end
        v = prev["speed_mps"]
        while v < 2 * v_end - 1e-9:
            v = min(2 * v_end, v + step)
            nxt = point(v)
            ma, mb = prev["forward_thrust_margin"], nxt["forward_thrust_margin"]
            if mb is not None and mb < 1 <= ma:
                top_speed = prev["speed_mps"] + (ma - 1) / (ma - mb) * (v - prev["speed_mps"])
                top_beyond_sweep = True
                break
            prev = nxt
    fwd_worst = min(fwd_pts, key=lambda p: p["forward_thrust_margin"]) if fwd_pts else None
    end_pt = points[-1]
    if layout == "quad_pusher":
        fwd_prop, fwd_name = pusher_prop, "pusher"
    else:
        fwd_prop, fwd_name = lift_prop, "tilted lift propellers"
    j0 = fwd_prop.j_zero_thrust if fwd_prop is not None else None
    if fwd_worst is None:
        top_level = "warn"
        fm = None
    else:
        fm = fwd_worst["forward_thrust_margin"]
        top_level = "warn" if fm < TOP_SPEED_MARGIN_MIN else "ok"
    top = {
        "margin_min": fm,
        "margin_min_speed_mps": fwd_worst["speed_mps"] if fwd_worst else None,
        "margin_at_sweep_end": end_pt["forward_thrust_margin"],
        "sweep_end_speed_mps": v_end,
        "required_margin": TOP_SPEED_MARGIN_MIN,
        "top_speed_mps": top_speed,
        "top_speed_beyond_sweep": top_beyond_sweep,
        "propeller": fwd_name,
        "advance_ratio_at_sweep_end": end_pt["forward_advance_ratio"],
        "zero_thrust_advance_ratio": j0,
        "level": top_level,
    }

    peak = max(points, key=lambda p: p["power_w"])
    # Duration and energy of one transition (0 -> cruise at constant acceleration).
    energy_j = 0.0
    seg = [p for p in points if p["speed_mps"] <= cruise_speed + 1e-9]
    for a, b in itertools.pairwise(seg):
        dt = (b["speed_mps"] - a["speed_mps"]) / ACCELERATION
        energy_j += 0.5 * (a["power_w"] + b["power_w"]) * dt
    duration = cruise_speed / ACCELERATION
    return {
        "points": points,
        "min_thrust_margin": m,
        "min_margin_speed_mps": worst["speed_mps"] if worst else None,
        "min_margin_group": (
            min(
                worst["groups"],
                key=lambda gr: (
                    gr["available_per_rotor_n"] / gr["thrust_per_rotor_n"]
                    if gr["thrust_per_rotor_n"] > 0.01 * w / 4
                    else float("inf")
                ),
            )["name"]
            if worst
            else None
        ),
        "transition_end_speed_mps": min(v_tr_end, v_end),
        "transition_end_buffer": TRANSITION_END_BUFFER,
        "transition_complete_within_sweep": tr_complete,
        "tilt_complete_speed_mps": tilt_done,
        "top_speed": top,
        "top_speed_mps": top_speed,
        "top_speed_limited_within_sweep": top_speed is not None and not top_beyond_sweep,
        "peak_power_w": peak["power_w"],
        "peak_power_speed_mps": peak["speed_mps"],
        "peak_current_a": max(p["battery_current_a"] for p in points),
        "speed_wing_80pct_mps": v80,
        "speed_wing_100pct_mps": v100,
        "transition_cl": cl_t,
        "duration_s": duration,
        "energy_wh": energy_j / 3600,
        "mean_power_w": energy_j / duration if duration > 0 else 0.0,
        "acceleration_mps2": ACCELERATION,
        "level": level,
        "margin_min_setting": margin_min,
    }
