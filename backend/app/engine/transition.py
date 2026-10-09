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

The transition proper is the acceleration from 0 to cruise speed; the minimum margin and the check
use that range. Points from cruise to 1.3 x cruise give the speed reserve and the top speed where
the propellers can no longer overcome the drag.

Outputs: thrust margin (available / required) per speed, its minimum and where it occurs, the
peak electrical power and battery current, the speed range where the wing carries 80 % to 100 %
of the weight, duration and energy of one transition, and pass/warn/fail against
``checks.transition_thrust_margin_min`` (default 1.3): fail below 1.0, warn below the minimum.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

from app.engine import battery as bat
from app.engine.propulsion import Motor, Propeller, max_thrust, operating_point
from app.engine.quantity import G0, RHO_SL

ACCELERATION = 1.0
POINTS = 27


def _bus(pack: dict[str, Any], power: float) -> float:
    return bat.loaded_voltage(pack, power, pack["v_reserve_ocv"])["voltage_v"]


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
    v_end = 1.3 * cruise_speed
    points = []
    power_guess = 0.0
    for k in range(POINTS):
        v = v_end * k / (POINTS - 1)
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
                }
            )
        vbus = _bus(pack, power_guess) if power_guess > 0 else pack["v_reserve_ocv"]
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
        power_guess = total
        lv = bat.loaded_voltage(pack, total, pack["v_reserve_ocv"])
        margins = []
        for gr in groups:
            avail = max_thrust(gr["prop"], gr["motor"], gr["inflow"], vbus)["thrust_n"]
            gr["available"] = avail
            if gr["thrust"] > 0.01 * w / 4:
                margins.append(avail / gr["thrust"])
        points.append(
            {
                "speed_mps": v,
                "tilt_deg": math.degrees(tilt),
                "wing_lift_fraction": lift / w,
                "drag_n": drag,
                "forward_force_n": fx,
                "rotor_vertical_n": fz,
                "thrust_margin": min(margins) if margins else None,
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
        )
    # The transition itself is the acceleration to cruise speed; points above cruise speed show
    # how much speed reserve the propellers have and give the top speed.
    valid = [
        p
        for p in points
        if p["thrust_margin"] is not None and p["speed_mps"] <= cruise_speed + 1e-9
    ]
    top = None
    for a, b in itertools.pairwise(points):
        ma, mb = a["thrust_margin"], b["thrust_margin"]
        if (
            a["speed_mps"] >= cruise_speed - 1e-9
            and ma is not None
            and mb is not None
            and ma >= 1 > mb
        ):
            top = a["speed_mps"] + (ma - 1) / (ma - mb) * (b["speed_mps"] - a["speed_mps"])
            break
    worst = min(valid, key=lambda p: p["thrust_margin"]) if valid else None
    peak = max(points, key=lambda p: p["power_w"])
    v80 = math.sqrt(0.8 * w / (0.5 * RHO_SL * wing_area * cl_t))
    v100 = math.sqrt(w / (0.5 * RHO_SL * wing_area * cl_t))
    # Duration and energy of one transition (0 -> cruise at constant acceleration).
    energy_j = 0.0
    seg = [p for p in points if p["speed_mps"] <= cruise_speed + 1e-9]
    for a, b in itertools.pairwise(seg):
        dt = (b["speed_mps"] - a["speed_mps"]) / ACCELERATION
        energy_j += 0.5 * (a["power_w"] + b["power_w"]) * dt
    duration = cruise_speed / ACCELERATION
    m = worst["thrust_margin"] if worst else float("inf")
    level = "fail" if m < 1.0 else ("warn" if m < margin_min else "ok")
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
        "top_speed_mps": top,
        "top_speed_limited_within_sweep": top is not None,
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
