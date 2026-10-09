"""Full-scale (final, carbon, up to 24 kg) checks on top of the Phase 3 analysis.

Public entry point::

    run_fullscale_checks(parameters, mission, settings, *, parts=None, analysis=None,
                         catalogue=None, cache_dir=None, mode="fast", settings_meta=None) -> dict

It runs (or reuses) the Phase 3 analysis (``app.engine.analysis.run_analysis``), keeps every
Phase 3 check, and adds (docs/phases/PHASE7.md section 2):

* **MTOW thresholds** 23 / 24 / 25 kg from settings (warning, design limit, legal limit), with a
  banner for every screen that shows mass; also against the layup-based mass below.
* **Motor-out hover** for the quad. Simple model, stated: quasi-static hover about the CG of
  the heaviest (and the lightest) payload case; each rotor gives thrust along body z up to its
  full-throttle static thrust (the propulsion model's ``max_static_thrust_per_motor_n``) and a
  reaction torque k_Q x T with k_Q = hover torque / hover thrust from the propeller model; the
  rotors spin as ArduPilot Quad X (front-right and rear-left CCW, front-left and rear-right CW);
  for tilt layouts the working tilting motors can give yaw by differential tilt of up to +-10 deg
  (ArduPilot vectored yaw, ``Q_TILT_YAW_ANGLE``; small-angle: the vertical component keeps
  cos 10 deg = 0.98 of the thrust). With one motor failed, linear programmes (scipy HiGHS) find
  whether the remaining thrusts can satisfy vertical force, roll, pitch and yaw equilibrium with
  0 <= T <= T_max, the smallest utilisation, and the largest roll and yaw moments that remain
  (authority) as fractions of the intact aircraft's. Mueller & D'Andrea (ICRA 2014, "Stability
  and control of a quadrocopter despite the complete loss of one, two, or three propellers")
  show a plain quad can at best hold altitude while spinning in yaw; this check asks for
  attitude and heading to be held, so a plain quad fails and a coaxial X8 is sized instead.
* **Structure at the final mass**: the wing-spar root moment and the boom moments of the
  analysis (manoeuvre load factor x safety factor from settings) against every catalogued carbon
  tube (``Selector._tube_rows``, the Phase 4 rule: margin >= 0.25 against min(500 MPa, half the
  published strength), boom tip deflection <= 1 % of the arm) and the smallest standard tube
  that would do (``structure.STANDARD_TUBES_MM``, ``structure.min_boom_diameter``).
* **Landing gear**: sink rate 1.5 m/s (a hard VTOL touchdown, three times ArduPilot's default
  final descent ``LAND_SPEED`` of 0.5 m/s), gear stroke 75 mm (carbon bow skids, estimate),
  spring efficiency 0.5 (linear spring; Raymer, Aircraft Design, ch. 11): n = 1 + V^2 / (2 g
  eta S). The boom landing case of Phase 3 (3 g) is re-checked at this n.
* **Battery current** at 24 kg hover (and in the worst feasible motor-out case) against the pack
  rating and the settings fraction; the Li-ion alternative of the same energy from catalogue
  cells.
* **Composite layup** per part (plies by fabric weight and orientation, core where used) giving a
  structural mass estimate for the final scale, compared with the mass model's areal densities.
* The A3 note next to every range and endurance figure.
"""

from __future__ import annotations

import copy
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np

from app.engine.analysis import json_safe, resolve_settings, run_analysis
from app.engine.geometry import build_geometry
from app.engine.quantity import G0, sort_statuses, status
from app.engine.structure import (
    MARGIN_WARN,
    STANDARD_TUBES_MM,
    STANDARD_WALLS_MM,
    TUBE_ALLOWABLE_PA,
    boom_check,
    min_boom_diameter,
    tube_section,
)

SCHEMA = "fullscale-checks/1"
A3_NOTE = (
    "Visual line of sight; flight beyond needs IAA authorisation; at least 150 m from "
    "residential, commercial, industrial or recreational areas (EU Open category A3)."
)
SEED_CATALOGUE = Path(__file__).resolve().parents[2] / "seed" / "parts.json"

# ----- Motor-out model -----
TILT_YAW_ANGLE_DEG = 10.0
ROLL_AUTHORITY_MIN = 0.25  # fraction of the intact aircraft's roll authority (engine rule)
YAW_AUTHORITY_MIN = 0.10  # fraction of the intact aircraft's yaw authority (engine rule)
COAX_LOWER_EFFICIENCY = 0.85  # thrust of a coaxial rotor at the same power vs isolated
#: Quad X motor order and spin (ArduPilot): +1 counter-clockwise seen from above.
QUAD_X = (
    ("front-right (motor 1)", "front", +1.0, +1),
    ("rear-left (motor 2)", "rear", -1.0, +1),
    ("front-left (motor 3)", "front", -1.0, -1),
    ("rear-right (motor 4)", "rear", +1.0, -1),
)

# ----- Landing gear -----
SINK_RATE_MPS = 1.5
GEAR_STROKE_M = 0.075
GEAR_EFFICIENCY = 0.5
PHASE3_LANDING_N = 3.0

# ----- Composite layup -----
FIBRE_DENSITY = 1780.0  # kg/m^3, standard-modulus carbon fibre (T300/T700 class datasheets)
FIBRE_VOLUME_FRACTION = 0.48
FIBRE_MASS_FRACTION = 0.55  # vacuum-bagged wet lay-up
CORE_BOND_G_M2 = 50.0  # resin taken up by each bonded core face (estimate)
LAMINATE_DENSITY = 1550.0  # kg/m^3 cured UD carbon/epoxy at ~55 % fibre volume
FAIRING_RADIUS_FRACTION = 0.06  # wing-root fillet radius as a fraction of the root chord
FAIRING_RADIUS_MIN_MM = 10.0
FAIRING_RADIUS_MAX_MM = 40.0

FABRICS: dict[str, dict[str, Any]] = {
    "cf93": {"label": "carbon 93 g/m² plain weave (1K)", "g_m2": 93.0},
    "cf160": {"label": "carbon 160 g/m² plain weave (3K)", "g_m2": 160.0},
    "cf200": {"label": "carbon 200 g/m² 2x2 twill (3K)", "g_m2": 200.0},
    "ud300": {"label": "carbon 300 g/m² unidirectional tape", "g_m2": 300.0},
}
CORES: dict[str, dict[str, Any]] = {
    "rohacell31": {"label": "Rohacell 31 IG-F foam", "kg_m3": 32.0},
    "rohacell51": {"label": "Rohacell 51 IG-F foam", "kg_m3": 52.0},
}
LAYUP_SOURCES = [
    "Fabric areal weights: standard aerospace/hobby carbon fabrics (93, 160, 200 g/m² woven, "
    "300 g/m² UD) as sold by composite suppliers (e.g. R&G Faserverbundwerkstoffe, Easy "
    "Composites).",
    "Cured ply mass = fabric / 0.55 fibre mass fraction (vacuum-bagged wet lay-up, about 48 % "
    "fibre volume; Hexcel 'Prepreg Technology' and supplier vacuum-bagging guides give 45-55 %).",
    "Ply thickness = fabric / (1780 kg/m³ x 0.48) (T300/T700-class fibre density).",
    "Core: Evonik ROHACELL IG-F datasheet (31 IG-F 32 kg/m³, 51 IG-F 52 kg/m³); 50 g/m² of "
    "resin per bonded core face (estimate for fine-cell PMI foam).",
    "Plies: +-45 for skin shear and torsion, 0/90 for hoop and handling, 0 UD in spar caps "
    "(classical sailplane/UAV practice; Niu, 'Composite Airframe Structures', 1992).",
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _v(q: Any) -> float | None:
    if isinstance(q, dict):
        q = q.get("value")
    if isinstance(q, (int, float)) and math.isfinite(q):
        return float(q)
    return None


def load_seed_catalogue(path: Path | None = None) -> list[dict[str, Any]]:
    """The seed parts catalogue (``backend/seed/parts.json``) in the selection's shape."""
    from app.parts_catalog import validate_spec

    p = path or SEED_CATALOGUE
    if not p.exists():
        return []
    out = []
    for i, entry in enumerate(json.loads(p.read_text(encoding="utf-8")), start=1):
        part = copy.deepcopy(entry)
        part["id"] = part.get("id", i)
        part["spec"] = validate_spec(part["category"], part["spec"])
        out.append(part)
    return out


def wing_root_fairing_radius_mm(root_chord_mm: float) -> float:
    """Fillet radius of the wing-root fairing (the moulds may reduce it to fit the fuselage)."""
    return float(
        min(
            FAIRING_RADIUS_MAX_MM,
            max(FAIRING_RADIUS_MIN_MM, FAIRING_RADIUS_FRACTION * root_chord_mm),
        )
    )


# ---------------------------------------------------------------------------
# MTOW thresholds
# ---------------------------------------------------------------------------


def mtow_thresholds(mass_kg: float, settings: dict[str, Any], label: str) -> dict[str, Any]:
    lim = settings["limits"]
    rows = [
        ("warn_mtow_kg", "Warning mass", "warn", "limits.warn_mtow_kg"),
        ("design_mtow_kg", "Design limit", "fail", "limits.design_mtow_kg"),
        ("legal_mtow_kg", "Legal limit (EU Open category)", "fail", "limits.legal_mtow_kg"),
    ]
    out = []
    level = "ok"
    for key, name, lvl, path in rows:
        kg = float(lim[key])
        over = mass_kg >= kg if key != "design_mtow_kg" else mass_kg > kg
        out.append(
            {
                "key": key,
                "label": name,
                "kg": kg,
                "exceeded": bool(over),
                "margin_kg": round(kg - mass_kg, 3),
                "setting": path,
            }
        )
        if over and (lvl == "fail" or level == "ok"):
            level = lvl
    if level == "ok":
        banner = (
            f"{label}: {mass_kg:.2f} kg, {lim['warn_mtow_kg'] - mass_kg:.2f} kg under the "
            f"{lim['warn_mtow_kg']:g} kg warning mass."
        )
    elif level == "warn":
        banner = (
            f"{label}: {mass_kg:.2f} kg is at or above the {lim['warn_mtow_kg']:g} kg warning mass "
            f"({lim['design_mtow_kg'] - mass_kg:.2f} kg to the {lim['design_mtow_kg']:g} kg design "
            f"limit, {lim['legal_mtow_kg'] - mass_kg:.2f} kg to the {lim['legal_mtow_kg']:g} kg "
            "legal limit)."
        )
    else:
        which = "legal" if mass_kg >= lim["legal_mtow_kg"] else "design"
        banner = (
            f"{label}: {mass_kg:.2f} kg is over the {lim[which + '_mtow_kg']:g} kg {which} limit."
            + (
                " Homebuilt drones of 25 kg and more are outside the EU Open category."
                if which == "legal"
                else ""
            )
        )
    return {
        "mass_kg": round(mass_kg, 3),
        "label": label,
        "thresholds": out,
        "level": level,
        "banner": banner,
        "show_on_every_mass_screen": True,
    }


# ---------------------------------------------------------------------------
# Motor-out hover
# ---------------------------------------------------------------------------


def _rotors(
    kind: str, x_f: float, x_r: float, y_o: float, tmax: float, tilt_group: str | None
) -> list[dict[str, Any]]:
    out = []
    for name, group, side, spin in QUAD_X:
        x = x_f if group == "front" else x_r
        base = {"x": x, "y": side * y_o, "group": group, "tilt": group == tilt_group}
        if kind == "quad":
            out.append({**base, "name": name, "spin": spin, "tmax": tmax})
        else:  # coaxial X8: upper rotor keeps the quad spin, lower rotor spins the other way
            pos = name.split(" (")[0]
            out.append({**base, "name": f"{pos} upper", "spin": spin, "tmax": tmax})
            out.append(
                {
                    **base,
                    "name": f"{pos} lower",
                    "spin": -spin,
                    "tmax": tmax * COAX_LOWER_EFFICIENCY,
                }
            )
    return out


def _lp(
    rotors: list[dict[str, Any]],
    failed: int | None,
    weight: float,
    x_cg: float,
    kq: float,
    *,
    goal: str,
    yaw: bool = True,
    unit_tmax: bool = False,
) -> dict[str, Any]:
    """One hover linear programme. ``goal``: ``util`` (smallest max T/Tmax), ``roll+``,
    ``roll-``, ``yaw+``, ``yaw-`` (largest moment). Distances in metres, forces in N."""
    from scipy.optimize import linprog

    work = [r for i, r in enumerate(rotors) if i != failed]
    n = len(work)
    tilt = [i for i, r in enumerate(work) if r["tilt"]]
    sin_y = math.sin(math.radians(TILT_YAW_ANGLE_DEG))
    cos_y = math.cos(math.radians(TILT_YAW_ANGLE_DEG))
    nt = len(tilt)
    util = goal == "util"
    nv = n + nt + (1 if util else 0)
    vert = np.zeros(nv)
    roll = np.zeros(nv)
    pitch = np.zeros(nv)
    yaw_row = np.zeros(nv)
    for i, r in enumerate(work):
        vert[i] = cos_y if r["tilt"] and nt else 1.0
        roll[i] = r["y"]
        pitch[i] = r["x"] - x_cg
        yaw_row[i] = -r["spin"] * kq
    for j in range(nt):
        yaw_row[n + j] = 1.0
    a_eq = [vert, pitch]
    b_eq = [weight, 0.0]
    if not goal.startswith("roll"):
        a_eq.append(roll)
        b_eq.append(0.0)
    if yaw and not goal.startswith("yaw"):
        a_eq.append(yaw_row)
        b_eq.append(0.0)
    a_ub: list[np.ndarray] = []
    b_ub: list[float] = []
    for j, i in enumerate(tilt):
        lever = abs(work[i]["y"]) * sin_y
        row = np.zeros(nv)
        row[n + j], row[i] = 1.0, -lever
        a_ub.append(row)
        row = np.zeros(nv)
        row[n + j], row[i] = -1.0, -lever
        a_ub.append(row)
        b_ub += [0.0, 0.0]
    bounds: list[tuple[float | None, float | None]] = []
    for r in work:
        tm = 1.0 if unit_tmax else r["tmax"]
        bounds.append((0.0, None if util else tm))
    bounds += [(None, None)] * nt
    c = np.zeros(nv)
    if util:
        bounds.append((0.0, None))
        for i, r in enumerate(work):
            tm = 1.0 if unit_tmax else r["tmax"]
            row = np.zeros(nv)
            row[i], row[-1] = 1.0, -tm
            a_ub.append(row)
            b_ub.append(0.0)
        c[-1] = 1.0
    elif goal == "roll+":
        c = -roll
    elif goal == "roll-":
        c = roll.copy()
    elif goal == "yaw+":
        c = -yaw_row
    elif goal == "yaw-":
        c = yaw_row.copy()
    res = linprog(
        c,
        A_ub=np.array(a_ub) if a_ub else None,
        b_ub=np.array(b_ub) if b_ub else None,
        A_eq=np.array(a_eq),
        b_eq=np.array(b_eq),
        bounds=bounds,
        method="highs",
    )
    if not res.success:
        return {"feasible": False}
    x = res.x
    thrusts = {work[i]["name"]: float(x[i]) for i in range(n)}
    out: dict[str, Any] = {"feasible": True, "thrusts_n": thrusts}
    if util:
        out["utilisation"] = float(x[-1])
    elif goal.startswith("roll"):
        out["moment_nm"] = float(roll @ x)
    else:
        out["moment_nm"] = float(yaw_row @ x)
    out["residual_yaw_nm"] = float(sum(yaw_row[i] * x[i] for i in range(n)))
    return out


def _authority(
    rotors: list[dict[str, Any]], failed: int | None, w: float, x_cg: float, kq: float, axis: str
) -> float | None:
    """Smaller of the largest positive and negative moments about ``axis`` that the rotors can
    give while holding the other equilibria; negative when zero moment is out of reach."""
    hi = _lp(rotors, failed, w, x_cg, kq, goal=f"{axis}+")
    lo = _lp(rotors, failed, w, x_cg, kq, goal=f"{axis}-")
    if not (hi["feasible"] and lo["feasible"]):
        return None
    return min(hi["moment_nm"], -lo["moment_nm"])


def motor_out_check(
    params: dict[str, Any], geometry: dict[str, Any], analysis: dict[str, Any]
) -> dict[str, Any]:
    layout = params["layout"]
    tilt_group = {"front_tilt": "front", "rear_tilt": "rear"}.get(layout)
    prop = analysis["propulsion"]
    tmax = float(prop["max_static_thrust_per_motor_n"])
    hov = prop["hover"]
    kqs = [
        op["torque_nm"] / op["thrust_n"]
        for op in hov.values()
        if op.get("thrust_n") and op.get("torque_nm")
    ]
    kq = float(np.mean(kqs)) if kqs else 0.04 * params["propulsion"]["prop_diameter_mm"] / 635
    x_f = geometry["front_rotor_x_mm"] / 1000
    x_r = geometry["rear_rotor_x_mm"] / 1000
    y_o = params["booms"]["lateral_offset_mm"] / 1000
    bal = analysis["balance"]
    mass = analysis["mass"]
    cases_cg = [
        ("heaviest camera", _v(bal["cg_max_payload_x"]), _v(mass["takeoff_max_payload"])),
        ("lightest camera", _v(bal["cg_min_payload_x"]), _v(mass["takeoff_min_payload"])),
    ]
    quad = _rotors("quad", x_f, x_r, y_o, tmax, tilt_group)
    hover_i = {
        "front": float(hov.get("front", {}).get("current_a") or 0.0),
        "rear": float(hov.get("rear", {}).get("current_a") or 0.0),
    }
    hover_t = {
        "front": float(hov.get("front", {}).get("thrust_n") or 1.0),
        "rear": float(hov.get("rear", {}).get("thrust_n") or 1.0),
    }
    cases = []
    for cg_label, cg_x_mm, m_kg in cases_cg:
        if cg_x_mm is None or m_kg is None:
            continue
        w = m_kg * G0
        x_cg = cg_x_mm / 1000
        roll0 = _authority(quad, None, w, x_cg, kq, "roll") or 0.0
        yaw0 = _authority(quad, None, w, x_cg, kq, "yaw") or 0.0
        for j, rot in enumerate(quad):
            full = _lp(quad, j, w, x_cg, kq, goal="util")
            spin = _lp(quad, j, w, x_cg, kq, goal="util", yaw=False)
            roll = _authority(quad, j, w, x_cg, kq, "roll")
            yaw = _authority(quad, j, w, x_cg, kq, "yaw")
            ok_eq = full["feasible"] and full["utilisation"] <= 1.0 + 1e-9
            roll_frac = (roll / roll0) if (roll is not None and roll0 > 0) else None
            yaw_frac = (yaw / yaw0) if (yaw is not None and yaw0 > 0) else None
            if not ok_eq:
                level = "fail"
            elif (roll_frac or 0) < ROLL_AUTHORITY_MIN or (yaw_frac or 0) < YAW_AUTHORITY_MIN:
                level = "warn"
            else:
                level = "ok"
            thr = full.get("thrusts_n") if full["feasible"] else spin.get("thrusts_n")
            current = None
            if ok_eq and thr:
                current = 0.0
                for r in quad:
                    if r["name"] in thr:
                        g = r["group"]
                        per = hover_i[g] / 2
                        current += per * (max(thr[r["name"]], 0.0) / (hover_t[g])) ** 1.5
            if ok_eq:
                why = (
                    f"holds attitude and heading: the busiest working motor runs at "
                    f"{full['utilisation'] * 100:.0f} % of its full thrust"
                )
            elif full["feasible"]:
                why = (
                    f"would need {full['utilisation'] * 100:.0f} % of full thrust from the busiest "
                    "working motor"
                )
            elif spin["feasible"] and spin["utilisation"] <= 1.0:
                why = (
                    "can hold attitude and altitude only while spinning in yaw: the reaction "
                    f"torques leave {abs(spin['residual_yaw_nm']):.1f} N·m that no remaining motor "
                    "can cancel"
                )
            else:
                why = (
                    "cannot hold attitude at all: the roll and pitch balance would need a motor "
                    "to pull downwards or beyond its full thrust"
                )
            cases.append(
                {
                    "failed_motor": rot["name"],
                    "payload_case": cg_label,
                    "cg_x_mm": cg_x_mm,
                    "mass_kg": m_kg,
                    "holds_attitude_and_heading": bool(ok_eq),
                    "holds_attitude_spinning": bool(
                        spin["feasible"] and spin["utilisation"] <= 1.0 + 1e-9
                    ),
                    "max_utilisation": full.get("utilisation", spin.get("utilisation")),
                    "thrusts_n": thr,
                    "roll_authority_fraction": roll_frac,
                    "yaw_authority_fraction": yaw_frac,
                    "residual_yaw_nm": spin.get("residual_yaw_nm"),
                    "battery_current_a": current,
                    "level": level,
                    "message": f"With the {rot['name']} out ({cg_label}) the aircraft {why}.",
                }
            )
    order = {"fail": 0, "warn": 1, "ok": 2}
    worst = min(cases, key=lambda c: (order[c["level"]], -(c["max_utilisation"] or 9)))
    level = worst["level"]
    fails = [c for c in cases if c["level"] == "fail"]
    # Recommendation: a coaxial X8 on the same four booms, sized for any single failure.
    w_max = max(c["mass_kg"] for c in cases) * G0
    req = 0.0
    x8 = _rotors("x8", x_f, x_r, y_o, 1.0, None)
    for _cg_label, cg_x_mm, m_kg in cases_cg:
        if cg_x_mm is None or m_kg is None:
            continue
        for j in range(len(x8)):
            r = _lp(x8, j, m_kg * G0, cg_x_mm / 1000, kq, goal="util", unit_tmax=True)
            if r["feasible"]:
                need = max(
                    t / (COAX_LOWER_EFFICIENCY if "lower" in name else 1.0)
                    for name, t in r["thrusts_n"].items()
                )
                req = max(req, need)
    x8_check = None
    if req > 0:
        x8_check = {
            "required_static_thrust_per_motor_n": round(req, 1),
            "current_motor_static_thrust_n": round(tmax, 1),
            "same_motors_enough": bool(tmax >= req),
            "installed_thrust_to_weight": round(4 * tmax * (1 + COAX_LOWER_EFFICIENCY) / w_max, 2),
            "coax_lower_rotor_efficiency": COAX_LOWER_EFFICIENCY,
        }
    if level == "fail":
        rec_msg = (
            "Recommendation: make it an octocopter. A coaxial X8 keeps the four boom stations: "
            "two contra-rotating motors at each, so a failed motor's partner carries that "
            "corner and the other pairs cancel the yaw torque by running their upper and lower "
            "rotors differently. "
            + (
                f"Each of the 8 motors then needs at least {req:.0f} N of static thrust "
                f"(lower rotors at {COAX_LOWER_EFFICIENCY:.0%} of an isolated rotor)"
                + (
                    f"; the present {tmax:.0f} N motors would do."
                    if tmax >= req
                    else f"; the present motors give {tmax:.0f} N, so a larger motor class is "
                    "needed."
                )
                if req > 0
                else ""
            )
            + " A flat octocopter needs eight arms and does not fit this boom layout."
        )
    else:
        rec_msg = ""
    first = fails[0] if fails else worst
    msg = (
        f"Motor-out hover ({len(cases)} cases: each of the four motors at both payloads). "
        + first["message"]
        + (
            f" {len(fails)} of {len(cases)} cases fail."
            if fails
            else " Every case holds attitude and heading."
        )
        + (" " + rec_msg if rec_msg else "")
    )
    return {
        "layout": {
            "front_tilt": "quad, front pair tilting",
            "rear_tilt": "quad, rear pair tilting",
            "quad_pusher": "quad + pusher (fixed lift motors)",
        }.get(layout, layout),
        "model": [
            "Quasi-static hover about the CG; thrust along body z up to the full-throttle static "
            f"thrust {tmax:.0f} N per motor (propulsion model); reaction torque k_Q x T with "
            f"k_Q = {kq * 1000:.1f} mm (hover torque / thrust).",
            "ArduPilot Quad X spin directions (front-right and rear-left counter-clockwise).",
            (
                "The tilting motors give yaw by differential tilt of up to "
                f"+-{TILT_YAW_ANGLE_DEG:g}° (ArduPilot vectored yaw)."
                if tilt_group
                else "Fixed lift motors: yaw only from motor speed differences."
            ),
            "Linear programmes (scipy HiGHS) for equilibrium with 0 <= T <= T_max and for the "
            "remaining roll and yaw moments, reported as fractions of the intact aircraft's "
            f"(at least {ROLL_AUTHORITY_MIN:.0%} roll and {YAW_AUTHORITY_MIN:.0%} yaw wanted, "
            "engine rule).",
            "Descending needs about the hover thrust, so holding hover is the test for a "
            "controlled descent.",
        ],
        "k_q_m": kq,
        "max_static_thrust_per_motor_n": tmax,
        "tilt_yaw_angle_deg": TILT_YAW_ANGLE_DEG if tilt_group else 0.0,
        "cases": cases,
        "worst_case": worst,
        "level": level,
        "message": msg,
        "recommendation": {
            "needed": level == "fail",
            "layout": "coaxial X8 (octocopter on the four boom stations)"
            if level == "fail"
            else None,
            "message": rec_msg,
            "x8": x8_check,
        },
        "source": "Mueller & D'Andrea, ICRA 2014 (loss of one propeller: a plain quadrotor can "
        "only hover while spinning); coaxial losses: Leishman, Principles of Helicopter "
        "Aerodynamics, 2006, ch. 2 (lower rotor works in the upper rotor's wake).",
    }


# ---------------------------------------------------------------------------
# Structure with catalogued tubes
# ---------------------------------------------------------------------------


def _needed_tube(moment_nm: float, max_od: float | None) -> dict[str, Any] | None:
    """Lightest standard tube (outer x wall) with margin >= MARGIN_WARN at ``moment_nm``."""
    from app.engine.mass import carbon_tube_mass_per_m

    best = None
    for od in STANDARD_TUBES_MM:
        if max_od is not None and od > max_od:
            continue
        for wall in STANDARD_WALLS_MM:
            if 2 * wall >= od * 0.6:
                continue
            sec = tube_section(od, wall)
            stress = moment_nm * sec["c_m"] / sec["I_m4"]
            ms = TUBE_ALLOWABLE_PA / stress - 1 if stress > 0 else math.inf
            if ms < MARGIN_WARN:
                continue
            mass = carbon_tube_mass_per_m(od, wall)
            if best is None or mass < best["mass_g_per_m"]:
                best = {
                    "outer_mm": float(od),
                    "wall_mm": wall,
                    "margin": ms,
                    "stress_mpa": stress / 1e6,
                    "mass_g_per_m": mass,
                }
    return best


def _tube_rows(
    params: dict[str, Any],
    mission: dict[str, Any],
    settings: dict[str, Any],
    catalogue: list[dict[str, Any]],
    moment: float,
    max_od: float | None,
    length_mm: float,
    tip: tuple[float, float] | None,
) -> list[dict[str, Any]]:
    from app.engine.selection import Selector, part_name

    sel = Selector(params, mission, settings, {"mass_kg": 1.0, "generic_masses": {}}, catalogue)
    rows = sel._tube_rows(moment, max_od, length_mm, tip)
    return [
        {
            "part": part_name(r["part"]),
            "outer_mm": r["part"]["spec"]["outer_diameter_mm"],
            "inner_mm": r["part"]["spec"]["inner_diameter_mm"],
            "mass_per_m_g": r["part"]["spec"]["mass_per_m_g"],
            "stress_mpa": r["stress_mpa"],
            "allowable_mpa": r["allow_mpa"],
            "margin": r["margin"],
            "deflection_mm": r.get("deflection_mm"),
            "ok": not r["problems"],
            "problems": r["problems"],
        }
        for r in sorted(rows, key=lambda r: -r["margin"])
    ]


def _needed_boom(
    params: dict[str, Any], geometry: dict[str, Any], t_max: float, weight: float, sf: float
) -> dict[str, Any] | None:
    """Smallest standard boom (standard wall) meeting the Phase 3 strength margin and the
    Phase 4 stiffness rule (tip deflection at full thrust <= 1 % of the arm)."""
    from app.engine.selection import BOOM_DEFLECTION_MAX

    for od in STANDARD_TUBES_MM:
        booms = {k: v for k, v in params["booms"].items() if k != "wall_mm"}
        q = {**params, "booms": {**booms, "diameter_mm": float(od)}}
        b = boom_check(q, geometry, t_max, weight, sf)
        if (
            b["margin"] >= MARGIN_WARN
            and b["tip_deflection_full_thrust_mm"] <= BOOM_DEFLECTION_MAX * b["arm_mm"]
        ):
            return {
                "outer_mm": float(od),
                "wall_mm": b["wall_mm"],
                "margin": b["margin"],
                "deflection_mm": b["tip_deflection_full_thrust_mm"],
                "smallest_for_strength_only_mm": min_boom_diameter(
                    params, geometry, t_max, weight, sf
                ),
            }
    return None


def structure_checks(
    params: dict[str, Any],
    mission: dict[str, Any],
    settings: dict[str, Any],
    geometry: dict[str, Any],
    analysis: dict[str, Any],
    catalogue: list[dict[str, Any]],
) -> dict[str, Any]:
    st = analysis["structure"]
    sp = st["wing_spar"]
    m_spar = float(sp["root_moment_ultimate_nm"])
    w = geometry["wing"]
    max_od = 0.85 * w["thickness_ratio"] * w["root_chord_mm"]
    spar_len = w["span_mm"]
    spar_rows = _tube_rows(params, mission, settings, catalogue, m_spar, max_od, spar_len, None)
    spar_ok = [r for r in spar_rows if r["ok"]]
    spar_need = _needed_tube(m_spar, max_od)
    if spar_ok:
        best = min(spar_ok, key=lambda r: r["mass_per_m_g"])
        spar_level = "ok"
        spar_msg = (
            f"Wing spar: root moment {m_spar:.0f} N·m at {sp['load_factor']:g} g x "
            f"{sp['safety_factor']:g}. The catalogued {best['part']} is enough "
            f"(margin {best['margin']:+.2f})."
        )
    else:
        strongest = spar_rows[0] if spar_rows else None
        spar_level = "warn" if sp.get("level") == "ok" else "fail"
        spar_msg = (
            f"Wing spar: root moment {m_spar:.0f} N·m at {sp['load_factor']:g} g x "
            f"{sp['safety_factor']:g}. No catalogued carbon tube is enough"
            + (
                f" (the strongest, {strongest['part']}, reaches {strongest['stress_mpa']:.0f} MPa,"
                f" margin {strongest['margin']:+.2f})"
                if strongest
                else " (the catalogue has no carbon tubes)"
            )
            + ". "
            + (
                f"A {spar_need['outer_mm']:g} x {spar_need['wall_mm']:g} mm wall tube would do "
                f"(margin {spar_need['margin']:+.2f}, {spar_need['mass_g_per_m']:.0f} g/m); "
                if spar_need
                else f"No standard tube up to {max_od:.0f} mm fits the root depth; "
            )
            + f"the moulded spar caps of the composite wing carry it in the mass model "
            f"({sp['spar']}, margin {sp['margin']:+.2f})."
        )
    bm = st["boom"]
    m_boom = max(bm["moment_thrust_ultimate_nm"], bm["moment_landing_ultimate_nm"])
    t_max = float(analysis["propulsion"]["max_static_thrust_per_motor_n"])
    arm_m = bm["arm_mm"] / 1000
    boom_len = 2 * params["booms"]["length_mm"]
    boom_rows = _tube_rows(
        params, mission, settings, catalogue, m_boom, None, boom_len, (t_max, arm_m)
    )
    boom_ok = [r for r in boom_rows if r["ok"]]
    weight = _v(analysis["mass"]["takeoff_max_payload"]) * G0
    sf = settings["checks"]["structural_safety_factor"]
    boom_need = _needed_boom(params, geometry, t_max, weight, sf)
    if boom_ok:
        best = min(boom_ok, key=lambda r: r["mass_per_m_g"])
        boom_level = "ok"
        boom_msg = (
            f"Booms: {m_boom:.0f} N·m ultimate ({bm['critical_case']}). The catalogued "
            f"{best['part']} is enough (margin {best['margin']:+.2f}"
            + (
                f", {best['deflection_mm']:.1f} mm tip deflection at full thrust)."
                if best.get("deflection_mm") is not None
                else ")."
            )
        )
    else:
        strongest = boom_rows[0] if boom_rows else None
        boom_level = "warn" if bm.get("level") == "ok" else "fail"
        boom_msg = (
            f"Booms: {m_boom:.0f} N·m ultimate ({bm['critical_case']}). No catalogued carbon "
            "tube is enough"
            + (
                f" (the strongest, {strongest['part']}: " + "; ".join(strongest["problems"]) + ")"
                if strongest
                else ""
            )
            + (
                f". Needed: a {boom_need['outer_mm']:g} mm tube with the standard "
                f"{boom_need['wall_mm']:.2g} mm wall (margin {boom_need['margin']:+.2f}, "
                f"{boom_need['deflection_mm']:.1f} mm tip deflection at full thrust); order a "
                "tube of that size and add it to the catalogue."
                if boom_need
                else ". No standard tube up to 50 mm meets both rules: shorten the boom arms."
            )
        )
    return {
        "spar": {
            "root_moment_ultimate_nm": m_spar,
            "max_outer_mm_root_depth": max_od,
            "catalogue": spar_rows,
            "needed_standard_tube": spar_need,
            "composite_caps": {k: sp[k] for k in ("spar", "stress_mpa", "margin", "level")},
            "level": spar_level,
            "message": spar_msg,
        },
        "booms": {
            "moment_ultimate_nm": m_boom,
            "critical_case": bm["critical_case"],
            "arm_mm": bm["arm_mm"],
            "full_thrust_n": t_max,
            "catalogue": boom_rows,
            "needed_outer_mm": boom_need,
            "level": boom_level,
            "message": boom_msg,
        },
        "source": "Phase 3 moments (structure.py) with the Phase 4 tube rule (selection.py).",
    }


# ---------------------------------------------------------------------------
# Landing gear
# ---------------------------------------------------------------------------


def landing_gear_check(
    params: dict[str, Any], analysis: dict[str, Any], settings: dict[str, Any]
) -> dict[str, Any]:
    m = _v(analysis["mass"]["takeoff_max_payload"]) or 0.0
    w = m * G0
    n = 1 + SINK_RATE_MPS**2 / (2 * G0 * GEAR_EFFICIENCY * GEAR_STROKE_M)
    gear = params["landing_gear"]["type"]
    points = 4 if gear in ("skids", "legs") else 0
    total = n * w
    per = total / points if points else 0.0
    bm = analysis["structure"]["boom"]
    sec = tube_section(bm["outer_mm"], bm["wall_mm"])
    m_land = bm["moment_landing_ultimate_nm"] * n / PHASE3_LANDING_N
    stress = m_land * sec["c_m"] / sec["I_m4"] if m_land > 0 else 0.0
    margin = bm["allowable_mpa"] * 1e6 / stress - 1 if stress > 0 else math.inf
    level = "fail" if margin < 0 else ("warn" if margin < MARGIN_WARN or n > 4.5 else "ok")
    if not points:
        level = "warn"
    msg = (
        f"Landing at {SINK_RATE_MPS:g} m/s sink on {GEAR_STROKE_M * 1000:.0f} mm of gear stroke "
        f"gives n = {n:.1f} g: {total:.0f} N in total"
        + (f", {per:.0f} N at each of the {points} gear attachments" if points else "")
        + f". The boom landing case (Phase 3 used {PHASE3_LANDING_N:g} g) becomes "
        f"{m_land:.0f} N·m at ultimate load, {stress / 1e6:.0f} MPa in the "
        f"{bm['outer_mm']:g} mm boom (margin {margin:+.2f})."
        + ("" if points else " The design has no landing gear: add skids or legs.")
    )
    return {
        "sink_rate_mps": SINK_RATE_MPS,
        "stroke_mm": GEAR_STROKE_M * 1000,
        "efficiency": GEAR_EFFICIENCY,
        "load_factor": n,
        "total_load_n": total,
        "attachments": points,
        "load_per_attachment_n": per,
        "ultimate_load_per_attachment_n": per * settings["checks"]["structural_safety_factor"],
        "boom_moment_ultimate_nm": m_land,
        "boom_stress_mpa": stress / 1e6,
        "boom_margin": margin,
        "level": level,
        "message": msg,
        "source": "n = 1 + V²/(2 g eta S) (energy balance; Raymer, Aircraft Design, ch. 11); "
        "1.5 m/s is three times ArduPilot's LAND_SPEED default (0.5 m/s); stroke and efficiency "
        "are estimates for carbon bow skids.",
    }


# ---------------------------------------------------------------------------
# Battery current
# ---------------------------------------------------------------------------


def battery_check(
    params: dict[str, Any],
    analysis: dict[str, Any],
    settings: dict[str, Any],
    motor_out: dict[str, Any],
    catalogue: list[dict[str, Any]],
) -> dict[str, Any]:
    b = analysis["battery"]
    frac = settings["checks"]["battery_current_max_fraction_of_rating"]
    i_hover = _v(b["hover_current"]) or 0.0
    i_peak = _v(b["peak_current"]) or 0.0
    i_cont = float(b["continuous_rating_a"])
    limit = frac * i_cont
    mo = [c["battery_current_a"] for c in motor_out["cases"] if c.get("battery_current_a")]
    i_mo = max(mo) if mo else None
    worst = max(i_peak, i_mo or 0.0)
    level = "ok" if worst <= limit else ("warn" if worst <= i_cont else "fail")
    pack = b["pack"]
    msg = (
        f"At {(_v(analysis['mass']['takeoff_max_payload']) or 0):.1f} kg the hover draws "
        f"{i_hover:.0f} A and the peak is {i_peak:.0f} A"
        + (f" ({i_mo:.0f} A hovering with one motor out)" if i_mo else "")
        + f" from the {pack['label']} pack rated {i_cont:.0f} A continuous "
        f"({pack['c_continuous']:g} C); the limit is {frac:.0%} of the rating, {limit:.0f} A."
    )
    # Li-ion alternative of the same energy from catalogue cells.
    energy = float(pack["energy_wh"])
    s_cells = int(pack["cells_series"])
    alts = []
    for c in catalogue:
        if c.get("category") != "cell":
            continue
        sp = c["spec"]
        cell_wh = sp["nominal_voltage_v"] * sp["capacity_mah"] / 1000
        par = max(1, math.ceil(energy / (s_cells * cell_wh) - 1e-9))
        i_rate = par * sp["max_continuous_discharge_a"]
        mass_kg = s_cells * par * c["mass_g"] * 1.08 / 1000
        alts.append(
            {
                "cell": f"{c['manufacturer']} {c['model']}",
                "config": f"{s_cells}S{par}P",
                "energy_wh": round(s_cells * par * cell_wh, 1),
                "mass_kg": round(mass_kg, 2),
                "continuous_rating_a": i_rate,
                "peak_fraction_of_rating": worst / i_rate if i_rate else None,
                "ok": worst <= frac * i_rate,
            }
        )
    return {
        "hover_current_a": i_hover,
        "peak_current_a": i_peak,
        "motor_out_current_a": i_mo,
        "pack": pack["label"],
        "continuous_rating_a": i_cont,
        "limit_a": limit,
        "max_fraction_of_rating": frac,
        "level": level,
        "message": msg,
        "li_ion_alternatives": sorted(alts, key=lambda a: a["mass_kg"]),
        "li_ion_note": "Same energy in catalogue 21700 cells, 8 % mass for strips, wrap and "
        "leads (the selection's custom-pack rule); the final scale prefers Li-ion for its "
        "specific energy when the cells' current rating covers the hover peak.",
    }


# ---------------------------------------------------------------------------
# Composite layup and structural mass
# ---------------------------------------------------------------------------


def _ply_t_mm(g_m2: float) -> float:
    return g_m2 / (FIBRE_DENSITY * FIBRE_VOLUME_FRACTION)


def _layup(
    plies: list[tuple[str, str, int, str]],
    core: tuple[str, float, float] | None,
    area_m2: float,
    extra_fraction: float,
    extra_note: str,
) -> dict[str, Any]:
    """``plies``: (fabric, orientation, count, position); ``core``: (material, mm, coverage)."""
    rows = []
    areal = 0.0
    thick = 0.0
    for fab, orient, count, pos in plies:
        f = FABRICS[fab]
        cured = f["g_m2"] / FIBRE_MASS_FRACTION * count
        areal += cured
        thick += _ply_t_mm(f["g_m2"]) * count
        rows.append(
            {
                "fabric": f["label"],
                "fabric_g_m2": f["g_m2"],
                "orientation": orient,
                "count": count,
                "position": pos,
                "cured_g_m2": round(cured, 1),
                "thickness_mm": round(_ply_t_mm(f["g_m2"]) * count, 3),
            }
        )
    core_out = None
    if core is not None:
        mat, t_mm, cover = core
        cd = CORES[mat]
        g = (t_mm / 1000 * cd["kg_m3"] * 1000 + 2 * CORE_BOND_G_M2) * cover
        areal += g
        thick += t_mm * cover
        core_out = {
            "material": cd["label"],
            "thickness_mm": t_mm,
            "coverage_fraction": cover,
            "g_m2_averaged": round(g, 1),
        }
    mass = areal * area_m2 * (1 + extra_fraction)
    return {
        "plies": rows,
        "core": core_out,
        "areal_density_g_m2": round(areal, 1),
        "laminate_thickness_mm": round(thick, 2),
        "area_m2": round(area_m2, 3),
        "local_reinforcement_fraction": extra_fraction,
        "local_reinforcement": extra_note,
        "mass_g": round(mass, 0),
    }


def _fus_area_between(g: dict[str, Any], x0: float, x1: float) -> float:
    """Wetted area (m²) of the fuselage between two stations (geometry module's stations)."""
    st = g["fuselage"]["stations"]
    xs = np.array([s["x_mm"] for s in st])
    ps = np.array([s["perimeter_mm"] for s in st])
    grid = np.linspace(max(x0, xs[0]), min(x1, xs[-1]), 200)
    per = np.interp(grid, xs, ps)
    return float(np.trapezoid(per, grid)) / 1e6


def layup_suggestions(
    params: dict[str, Any], geometry: dict[str, Any], analysis: dict[str, Any]
) -> dict[str, Any]:
    g = geometry
    w = g["wing"]
    parts: list[dict[str, Any]] = []
    # Wing skins.
    wing = _layup(
        [
            ("cf93", "+-45", 1, "outer"),
            ("cf93", "+-45", 1, "inner"),
        ],
        ("rohacell31", 2.0, 0.85),
        w["wetted_area_m2"],
        0.15,
        "Solid laminate (no core) along the spar caps, leading and trailing edges; ribs at the "
        "root, the boom clamps and the aileron hinges, root rib and hard points: +15 % (estimate).",
    )
    parts.append({"key": "wing_skins", "label": "Wing skins (both panels)", **wing})
    # Spar caps and shear web.
    sp = analysis["structure"]["wing_spar"]
    det = sp.get("spar_detail") or {}
    semi_m = w["span_mm"] / 2000
    depth_m = float(det.get("depth_m") or 0.8 * w["thickness_ratio"] * w["root_chord_mm"] / 1000)
    cap_area = float(det.get("cap_area_m2") or 0.0)
    cap_w_mm = max(20.0, 0.06 * w["root_chord_mm"])
    ud_t = _ply_t_mm(FABRICS["ud300"]["g_m2"])
    n_root = max(1, math.ceil(cap_area * 1e6 / (cap_w_mm * ud_t)))
    cap_mass = 2 * 2 * cap_area * LAMINATE_DENSITY * semi_m * 0.6 * 1000  # tapered caps
    web = _layup(
        [("cf160", "+-45", 1, "each face")] * 2,
        ("rohacell51", 3.0, 1.0),
        2 * semi_m * depth_m * 0.8,
        0.1,
        "Web doublers at the root and the boom clamps: +10 %.",
    )
    parts.append(
        {
            "key": "wing_spar",
            "label": "Wing spar (UD caps and shear web)",
            "plies": [
                {
                    "fabric": FABRICS["ud300"]["label"],
                    "fabric_g_m2": 300.0,
                    "orientation": "0 (spanwise)",
                    "count": n_root,
                    "position": f"each cap, {cap_w_mm:.0f} mm wide at the root, dropping plies "
                    "outboard",
                    "cured_g_m2": round(300 / FIBRE_MASS_FRACTION * n_root, 1),
                    "thickness_mm": round(ud_t * n_root, 3),
                },
                *web["plies"],
            ],
            "core": web["core"],
            "cap_area_mm2": cap_area * 1e6,
            "depth_mm": depth_m * 1000,
            "areal_density_g_m2": web["areal_density_g_m2"],
            "area_m2": web["area_m2"],
            "local_reinforcement_fraction": 0.1,
            "local_reinforcement": web["local_reinforcement"],
            "mass_g": round(cap_mass + web["mass_g"], 0),
        }
    )
    # Fuselage shell (without the nose bay) and the nose bay.
    nb_len = params["nose_bay"]["length_mm"]
    fus_area = _fus_area_between(g, nb_len, g["fuselage"]["length_mm"])
    nose_area = _fus_area_between(g, 0.0, nb_len)
    fus = _layup(
        [
            ("cf200", "0/90", 1, "outer"),
            ("cf160", "+-45", 1, "outer"),
            ("cf160", "+-45", 1, "inner"),
        ],
        ("rohacell31", 2.0, 0.6),
        fus_area,
        0.25,
        "Frames, the wing-root and boom-load doublers (2 x 200 g/m² locally), battery tray and "
        "hatch flanges: +25 % (estimate).",
    )
    parts.append({"key": "fuselage", "label": "Fuselage shell", **fus})
    nose = _layup(
        [("cf160", "0/90", 1, "outer"), ("cf160", "+-45", 1, "inner")],
        None,
        nose_area,
        0.2,
        "Camera-window edge doubler, mating flange and the four boss pads: +20 % (estimate).",
    )
    parts.append({"key": "nose_bay", "label": "Nose bay shell", **nose})
    # Tail surfaces.
    tail = _layup(
        [("cf93", "+-45", 1, "outer"), ("cf93", "+-45", 1, "inner")],
        ("rohacell31", 2.0, 0.85),
        2 * (1 + 0.25 * 0.09) * g["tail"]["planform_area_m2"],
        0.2,
        "Spar tube sockets, hinge lines and root ribs: +20 % (estimate).",
    )
    parts.append({"key": "tail", "label": "Tail surfaces", **tail})
    # Wing-root fairings.
    r_f = wing_root_fairing_radius_mm(w["root_chord_mm"])
    fair_area = 2 * 2.05 * w["root_chord_mm"] * (math.pi / 2) * r_f / 1e6
    fair = _layup(
        [("cf93", "+-45", 1, "outer"), ("cf160", "0/90", 1, "inner")],
        None,
        fair_area,
        0.1,
        "Bonding flanges: +10 %.",
    )
    parts.append(
        {"key": "wing_root_fairing", "label": "Wing-root fairings (left and right)", **fair}
    )
    total = sum(p["mass_g"] for p in parts)
    comps = {c["key"]: c for c in analysis["mass"]["components"]}
    model_keys = ("wing_structure", "wing_spar", "fuselage_structure", "tail_structure")
    model = sum(comps[k]["mass_g"] for k in model_keys if k in comps)
    delta = total - model
    m_takeoff = _v(analysis["mass"]["takeoff_max_payload"]) or 0.0
    implied = {
        "wing": round(
            (parts[0]["mass_g"] + parts[1]["mass_g"] + parts[5]["mass_g"]) / 1000 / w["area_m2"],
            3,
        ),
        "fuselage": round(
            (parts[2]["mass_g"] + parts[3]["mass_g"]) / 1000 / g["fuselage"]["wetted_area_m2"], 3
        ),
        "tail": round(parts[4]["mass_g"] / 1000 / g["tail"]["planform_area_m2"], 3),
    }
    return {
        "parts": parts,
        "structural_mass": {
            "layup_total_g": round(total, 0),
            "mass_model_g": round(model, 0),
            "mass_model_items": [k for k in model_keys if k in comps],
            "difference_g": round(delta, 0),
            "takeoff_mass_with_layup_kg": round(m_takeoff + delta / 1000, 3),
            "implied_areal_density_kg_m2": implied,
            "mass_model_areal_density_kg_m2": {"wing": 1.0, "fuselage": 1.0, "tail": 0.7},
            "note": "The layup masses replace the mass model's areal-density estimates for the "
            "wing, spar, fuselage (with nose bay) and tail; booms, gear and mounts are bought "
            "or printed and keep the mass-model values. 'implied_areal_density_kg_m2' is in the "
            "mass model's units (wing and tail per m² of planform, fuselage per m² of wetted "
            "area) so it can replace the final-scale values.",
        },
        "fairing_radius_mm": r_f,
        "laminate_rules": {
            "fibre_mass_fraction": FIBRE_MASS_FRACTION,
            "fibre_volume_fraction": FIBRE_VOLUME_FRACTION,
            "core_bond_g_m2_per_face": CORE_BOND_G_M2,
        },
        "sources": LAYUP_SOURCES,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _battery_trim_hint(mass_kg: float, settings: dict[str, Any], analysis: dict[str, Any]) -> str:
    """How much battery to take out to come back to the design limit, and what it costs."""
    excess = mass_kg - float(settings["limits"]["design_mtow_kg"])
    if excess <= 0:
        return ""
    bat_kg = float(analysis["battery"]["pack"]["mass_kg"])
    end = _v(analysis["performance"]["endurance_cruise"]) or 0.0
    lost = end * excess / bat_kg if bat_kg > 0 else 0.0
    return (
        f" To come back to {settings['limits']['design_mtow_kg']:g} kg take {excess:.2f} kg out "
        f"of the {bat_kg:.1f} kg battery (about {lost:.0f} min less wing-flight endurance) or "
        "re-run scale to weight with this structure."
    )


def run_fullscale_checks(
    parameters: dict[str, Any],
    mission: dict[str, Any],
    settings: dict[str, Any] | None,
    *,
    parts: dict[str, Any] | None = None,
    analysis: dict[str, Any] | None = None,
    catalogue: list[dict[str, Any]] | None = None,
    cache_dir: str | None = None,
    mode: str = "fast",
    settings_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Phase 3 analysis and checks plus the full-scale checks (module docstring)."""
    t0 = time.time()
    s = resolve_settings(settings)
    if analysis is None:
        analysis = run_analysis(
            parameters,
            mission,
            s,
            mode=mode,
            cache_dir=cache_dir,
            parts=parts,
            settings_meta=settings_meta,
        )
    if not analysis.get("valid"):
        return {
            "schema": SCHEMA,
            "valid": False,
            "checks": analysis.get("checks", []),
            "message": "The design cannot be analysed, so the full-scale checks cannot run.",
        }
    p = analysis["inputs"]["parameters"]
    m = analysis["inputs"]["mission"]
    cat = load_seed_catalogue() if catalogue is None else catalogue
    g = build_geometry(p)
    mass_kg = _v(analysis["mass"]["takeoff_max_payload"]) or 0.0
    mtow = mtow_thresholds(mass_kg, s, "Take-off mass (heaviest camera)")
    motor_out = motor_out_check(p, g, analysis)
    struct = structure_checks(p, m, s, g, analysis, cat)
    gear = landing_gear_check(p, analysis, s)
    battery = battery_check(p, analysis, s, motor_out, cat)
    layup = layup_suggestions(p, g, analysis)
    m_layup = layup["structural_mass"]["takeoff_mass_with_layup_kg"]
    mtow_layup = mtow_thresholds(m_layup, s, "Take-off mass with the layup structure")

    checks: list[dict[str, Any]] = []

    def add(key: str, label: str, level: str, message: str, **extra: Any) -> None:
        checks.append(status(key, label, level, message, **extra))

    add(
        "fullscale.mtow",
        "Take-off mass thresholds",
        mtow["level"],
        mtow["banner"],
        value=mass_kg,
        threshold=[t["kg"] for t in mtow["thresholds"]],
        unit="kg",
        threshold_source="Settings: limits.warn_mtow_kg, limits.design_mtow_kg, "
        "limits.legal_mtow_kg (EU Regulation 2019/947 Open category below 25 kg).",
    )
    add(
        "fullscale.mtow_layup",
        "Take-off mass with the layup structure",
        mtow_layup["level"],
        mtow_layup["banner"]
        + f" The layup structure is {layup['structural_mass']['difference_g'] / 1000:+.2f} kg "
        "against the mass model's estimate." + _battery_trim_hint(m_layup, s, analysis),
        value=m_layup,
        threshold=[t["kg"] for t in mtow_layup["thresholds"]],
        unit="kg",
        threshold_source="Settings: limits (as above).",
    )
    add(
        "fullscale.motor_out",
        "Motor-out hover",
        motor_out["level"],
        motor_out["message"],
        value=motor_out["worst_case"].get("max_utilisation"),
        threshold=1.0,
        unit="",
        threshold_source="Engine rule: hold attitude and heading with any one motor failed, "
        f"roll authority >= {ROLL_AUTHORITY_MIN:.0%} and yaw >= {YAW_AUTHORITY_MIN:.0%} of "
        "intact.",
    )
    add(
        "fullscale.spar_tube",
        "Wing spar (carbon tube candidates)",
        struct["spar"]["level"],
        struct["spar"]["message"],
        value=struct["spar"]["root_moment_ultimate_nm"],
        unit="N·m",
        threshold_source="Settings: checks.manoeuvre_load_factor x "
        "checks.structural_safety_factor; tube margin >= 0.25 (engine rule).",
    )
    add(
        "fullscale.boom_tube",
        "Booms (carbon tube candidates)",
        struct["booms"]["level"],
        struct["booms"]["message"],
        value=struct["booms"]["moment_ultimate_nm"],
        unit="N·m",
        threshold_source="Tube margin >= 0.25, tip deflection <= 1 % of the arm (engine rules).",
    )
    add(
        "fullscale.landing_gear",
        "Landing gear load",
        gear["level"],
        gear["message"],
        value=gear["load_factor"],
        unit="g",
        threshold_source=gear["source"],
    )
    add(
        "fullscale.battery_current",
        "Battery current at full-scale hover",
        battery["level"],
        battery["message"],
        value=max(battery["peak_current_a"], battery["motor_out_current_a"] or 0.0),
        threshold=battery["limit_a"],
        unit="A",
        threshold_source="Settings: checks.battery_current_max_fraction_of_rating.",
    )
    perf = analysis["performance"]
    range_endurance = {
        "endurance_cruise": perf["endurance_cruise"],
        "endurance_total": perf["endurance_total"],
        "range": perf["range"],
        "a3_note": A3_NOTE,
    }
    notes = []
    if m.get("scale") != "final":
        notes.append(
            "The mission is not set to the final (carbon) scale; the full-scale checks still "
            "run but the mass model uses the prototype construction."
        )
    if p["battery"]["chemistry"] != "li-ion":
        notes.append(
            "The final scale prefers Li-ion packs for their specific energy; see the Li-ion "
            "alternatives in the battery section."
        )
    if not 12 <= int(p["battery"]["cells_series"]) <= 14:
        notes.append(f"The pack is {p['battery']['cells_series']}S; the final scale uses 12S-14S.")
    all_checks = sort_statuses(list(analysis.get("checks", [])) + checks)
    counts = {lvl: sum(1 for c in all_checks if c["level"] == lvl) for lvl in ("fail", "warn")}
    return json_safe(
        {
            "schema": SCHEMA,
            "valid": True,
            "parameters": p,
            "mission": m,
            "mtow": mtow,
            "mtow_layup": mtow_layup,
            "motor_out": motor_out,
            "structure": struct,
            "landing_gear": gear,
            "battery": battery,
            "layup": layup,
            "range_endurance": range_endurance,
            "a3_note": A3_NOTE,
            "fullscale_checks": checks,
            "phase3_checks": analysis.get("checks", []),
            "checks": all_checks,
            "counts": counts,
            "summary": analysis["summary"],
            "analysis": analysis,
            "notes": notes,
            "duration_s": round(time.time() - t0, 2),
        }
    )
