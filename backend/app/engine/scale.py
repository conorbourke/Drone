"""Scale to a target take-off mass: re-solve the same layout, never multiply by one factor.

Public entry point::

    run_scale(parameters, mission, settings, target_takeoff_mass_kg, *, mode="full",
              cache_dir=None, progress=None, settings_meta=None) -> dict

Method (docs/phases/PHASE3.md section 2 "scale.py"), each rule with its basis:

1. Construction: above 5 kg the mission scale becomes ``final`` (carbon composite); the brief
   puts the printed prototype at 2-3 kg.
2. Wing loading: the lower of (a) the stall limit, W/S = 0.5 rho (V_cruise / (1.1 x the settings
   cruise/stall ratio))^2 x CL_max (10 % extra margin on the ratio for the estimate's
   uncertainty), and (b) the loading that puts the cruise CL at the best lift-to-drag point,
   CL* = sqrt(pi e A CD0), but not above the section's best cl/cd (XFOIL table at the scaled
   Reynolds number). The aspect ratio and taper of the original wing are kept.
3. Lift propellers: disc loading grows with the cube root of the mass ratio (heavier multirotors
   run higher disc loadings: a statistical trend, estimate), diameter rounded to whole inches,
   the original pitch/diameter ratio kept. Booms are placed so the discs clear the fuselage and
   the wing leading and trailing edges; boom diameter from the boom bending check.
4. Fuselage: long enough for the wing root and tail arm, wide and tall enough to hold the
   battery (pack density 2.3 kg/L LiPo, 2.6 kg/L Li-ion, at most 45 % of the constant-section
   volume, estimate); tail arm and tail areas from the original tail volume coefficients.
5. Battery: series count by mass class (6S up to 8 kg, 12S above: common QuadPlane practice
   keeping currents moderate), energy for the target endurance, capped by the mass budget
   (target mass minus everything else), as LiPo packs of up to 22 Ah or Li-ion 4.5 Ah cells.
6. Mass iterated to convergence (fast analyses), battery moved to centre the static margin,
   then the full analysis is run on the result.

Output: before/after table with a plain "what changed and why" line per row, the new
parameters and mission, and both analyses' summaries.
"""

from __future__ import annotations

import copy
import math
import time
from collections.abc import Callable
from typing import Any

from app.engine.analysis import json_safe, resolve_settings, run_analysis, validate_inputs
from app.engine.geometry import build_geometry
from app.engine.mass import PACK_SPECIFIC_ENERGY_WH_PER_KG, solve_mass
from app.engine.polars import PolarStore
from app.engine.quantity import G0, MU_SL, RHO_SL

ProgressFn = Callable[[float, str], None]
FINAL_SCALE_ABOVE_KG = 5.0
PACK_DENSITY_KG_PER_L = {"lipo": 2.3, "li-ion": 2.6}
BATTERY_VOLUME_FRACTION = 0.45
INCH = 25.4
LIPO_MAX_GROUP_MAH = 22000.0
LIION_CELL_MAH = 4500.0


def _r(x: float, step: float = 1.0) -> float:
    return round(round(x / step) * step, 3)


def _section_best_cl(store: PolarStore, airfoil: str, re: float) -> float:
    pol = store.table_polar(airfoil, re)
    if pol is None:
        return 0.7
    best, best_cl = 0.0, 0.7
    for part_w, part in pol.parts:
        if part_w <= 0:
            continue
        for cl, cd in zip(part.cl, part.cd, strict=True):
            if cd > 0 and cl / cd > best and cl < part.cl_max * 0.85:
                best, best_cl = cl / cd, cl
    return best_cl


def _design_for_mass(
    p0: dict[str, Any],
    m0: dict[str, Any],
    base: dict[str, Any],
    mass_kg: float,
    settings: dict[str, Any],
    battery_wh: float,
    store: PolarStore,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Parameters (and reasons) for the same layout at ``mass_kg`` with ``battery_wh``."""
    p = copy.deepcopy(p0)
    why: dict[str, str] = {}
    w0 = p0["wing"]
    v = m0["cruise_speed_mps"]
    q = 0.5 * RHO_SL * v * v
    weight = mass_kg * G0
    ar = w0["span_mm"] ** 2 / ((w0["root_chord_mm"] + w0["tip_chord_mm"]) / 2 * w0["span_mm"])
    taper = w0["tip_chord_mm"] / w0["root_chord_mm"]
    # --- Wing loading ---
    clmax = base["aero"]["cl_max"]["value"]
    ratio = settings["checks"]["cruise_to_stall_speed_ratio_min"] * 1.1
    ws_stall = 0.5 * RHO_SL * (v / ratio) ** 2 * clmax
    cd0 = base["aero"]["cd0"]["value"] * 0.85  # carbon, larger Re: lower than the prototype's
    e = base["aero"]["span_efficiency"]["value"] * 0.85
    cl_star = math.sqrt(math.pi * e * ar * cd0)
    s_guess = weight / (q * cl_star)
    mac_guess = math.sqrt(s_guess / ar) * 1000
    cl_sec = _section_best_cl(store, w0["airfoil"], RHO_SL * v * mac_guess / 1000 / MU_SL)
    cl_target = min(cl_star, cl_sec)
    ws_opt = q * cl_target
    ws = min(ws_stall, ws_opt)
    limited = "stall margin" if ws_stall < ws_opt else "best lift-to-drag"
    area = weight / ws
    span = math.sqrt(ar * area)
    root = 2 * area / (span * (1 + taper))
    p["wing"]["span_mm"] = _r(span * 1000, 10)
    p["wing"]["root_chord_mm"] = _r(root * 1000, 5)
    p["wing"]["tip_chord_mm"] = _r(root * taper * 1000, 5)
    why["wing"] = (
        f"Wing loading {ws / G0:.1f} kg/m² set by the {limited}: the cruise lift "
        f"coefficient {weight / (q * area):.2f} (best lift-to-drag near {cl_target:.2f}) "
        f"keeps stall at least {ratio:.2f} x below the {v:g} m/s cruise; same aspect "
        f"ratio {ar:.1f} and taper."
    )
    # --- Propellers ---
    pr0 = p0["propulsion"]
    m_base = base["summary"]["takeoff_mass"]["value"]
    dl0 = m_base * G0 / 4 / (math.pi * (pr0["prop_diameter_mm"] / 2000) ** 2)
    dl = dl0 * (mass_kg / m_base) ** (1 / 3)
    d = math.sqrt(4 * weight / 4 / (math.pi * dl)) * 1000
    d = max(INCH * 8, _r(d / INCH) * INCH)
    p["propulsion"]["prop_diameter_mm"] = round(d, 1)
    p["propulsion"]["prop_pitch_mm"] = round(d * pr0["prop_pitch_mm"] / pr0["prop_diameter_mm"], 1)
    why["propellers"] = (
        f"Disc loading {dl:.0f} N/m² (was {dl0:.0f}): heavier multirotors run "
        "higher disc loadings (trend with the cube root of mass), so the propeller "
        f"grows to {d / INCH:.0f} in, not in proportion to the span."
    )
    # --- Battery (energy, series count, packaging) ---
    chem = p0["battery"]["chemistry"]
    cells_s = 6 if mass_kg <= 8 else 12
    v_cell = 3.7 if chem == "lipo" else 3.6
    ah = battery_wh / (cells_s * v_cell)
    if chem == "lipo":
        par = max(1, math.ceil(ah * 1000 / LIPO_MAX_GROUP_MAH))
        cap = max(1000.0, _r(ah * 1000 / par, 500))
    else:
        par = max(1, min(10, math.ceil(ah * 1000 / LIION_CELL_MAH)))
        cap = (
            LIION_CELL_MAH if ah * 1000 / par <= LIION_CELL_MAH * 1.05 else _r(ah * 1000 / par, 500)
        )
    p["battery"].update({"cells_series": cells_s, "cells_parallel": par, "capacity_mah": cap})
    why["battery"] = (
        f"{cells_s}S keeps the motor currents moderate at {mass_kg:.1f} kg "
        "(6S up to 8 kg, 12S above: common QuadPlane practice); the pack fills the mass "
        "left after everything else, up to the target take-off mass."
    )
    # --- Fuselage ---
    f0 = p0["fuselage"]
    bat_l = (
        cells_s
        * v_cell
        * cap
        * par
        / 1000
        / PACK_SPECIFIC_ENERGY_WH_PER_KG[chem]
        / PACK_DENSITY_KG_PER_L[chem]
    )
    x_ratio = w0["x_le_mm"] / f0["length_mm"]
    length = max(
        f0["length_mm"] * (span * 1000 / w0["span_mm"]) ** 0.75,
        root * 1000 / max(0.2, 0.8 - x_ratio),
    )
    const_len = length * 0.45
    need_area = bat_l / 1000 / BATTERY_VOLUME_FRACTION / (const_len / 1000) * 1e6  # mm^2
    aspect = f0["height_mm"] / f0["width_mm"]
    width = max(f0["width_mm"], math.sqrt(need_area / aspect))
    p["fuselage"]["length_mm"] = _r(length, 10)
    p["fuselage"]["width_mm"] = _r(width, 5)
    p["fuselage"]["height_mm"] = _r(width * aspect, 5)
    why["fuselage"] = (
        f"Length grows with span^0.75 for the tail arm; the cross-section is set by "
        f"the {bat_l:.1f} L battery (at most 45 % of the constant section), so it "
        "grows differently from the length."
    )
    # --- Wing position, nose bay, tail ---
    p["wing"]["x_le_mm"] = _r(w0["x_le_mm"] / f0["length_mm"] * p["fuselage"]["length_mm"], 5)
    if p["wing"]["x_le_mm"] + p["wing"]["root_chord_mm"] > p["fuselage"]["length_mm"] * 0.8:
        p["wing"]["x_le_mm"] = _r(p["fuselage"]["length_mm"] * 0.8 - p["wing"]["root_chord_mm"], 5)
    t0 = p0["tail"]
    g0 = base["geometry"]
    vh = g0["tail"]["horizontal_volume_coefficient"]
    vv = g0["tail"]["vertical_volume_coefficient"]
    arm = t0["arm_mm"] * p["fuselage"]["length_mm"] / f0["length_mm"]
    mac = (2 / 3) * root * (1 + taper + taper**2) / (1 + taper) * 1000
    s_h = vh * area * mac / arm  # m^2 (projected horizontal)
    s_v = vv * area * span * 1000 / arm
    ar_t = t0["span_mm"] / t0["chord_mm"]
    if t0["type"] in ("v_tail", "inverted_v"):
        chord = math.sqrt(s_h * 1e6 / ar_t)
        p["tail"].update({"chord_mm": _r(chord, 5), "span_mm": _r(chord * ar_t, 10)})
    else:
        chord = math.sqrt(s_h * 1e6 / ar_t)
        fins = 2 if t0["type"] == "twin_boom_h" else 1
        p["tail"].update(
            {
                "chord_mm": _r(chord, 5),
                "span_mm": _r(chord * ar_t, 10),
                "height_mm": _r(s_v * 1e6 / (fins * chord), 5),
            }
        )
    p["tail"]["arm_mm"] = _r(arm, 5)
    if t0["type"] in ("v_tail", "inverted_v"):
        p["tail"]["height_mm"] = _r(
            t0["height_mm"] * p["fuselage"]["height_mm"] / f0["height_mm"], 5
        )
    why["tail"] = (
        f"Tail arm {arm:.0f} mm follows the fuselage; tail areas keep the original tail "
        f"volume coefficients (horizontal {vh:.2f}, vertical {vv:.3f})."
    )
    # --- Booms and motor stations (props clear the fuselage and the wing) ---
    half_f = p["fuselage"]["width_mm"] / 2
    yo = max(
        0.55 * d + half_f, 0.55 * d, p0["booms"]["lateral_offset_mm"] / w0["span_mm"] * span * 1000
    )
    yo = min(yo, 0.45 * span * 1000)
    p["booms"]["lateral_offset_mm"] = _r(yo, 5)
    le_at = p["wing"]["x_le_mm"] + yo * math.tan(math.radians(w0["sweep_deg"]))
    c_at = root * 1000 - (root - root * taper) * 1000 * yo / (span * 500)
    clear = 0.05 * d
    x_front = le_at - d / 2 - clear
    x_rear = le_at + c_at + d / 2 + clear
    margin = 0.08 * d
    boom_start = x_front - margin
    p["booms"]["x_offset_mm"] = _r(boom_start - p["wing"]["x_le_mm"], 1)
    p["motors"]["front_x_mm"] = _r(margin, 1)
    p["motors"]["rear_x_mm"] = _r(x_rear - boom_start, 1)
    p["booms"]["length_mm"] = _r(x_rear - boom_start + margin, 5)
    p["motors"]["height_mm"] = _r(p0["motors"]["height_mm"] * d / pr0["prop_diameter_mm"], 1)
    p["tilt"]["axis_x_mm"] = (
        p["motors"]["front_x_mm"] if p["layout"] == "front_tilt" else p["motors"]["rear_x_mm"]
    )
    p["landing_gear"]["height_mm"] = _r(
        p0["landing_gear"]["height_mm"] * (d / pr0["prop_diameter_mm"]) ** 0.5, 5
    )
    if p["layout"] == "quad_pusher":
        p["pusher"]["prop_diameter_mm"] = _r(
            p0["pusher"]["prop_diameter_mm"] * (mass_kg / m_base) ** 0.35, INCH
        )
        p["pusher"]["x_mm"] = _r(p["fuselage"]["length_mm"] - 20, 5)
    why["booms"] = (
        f"Boom offset {yo:.0f} mm and motor stations keep the {d:.0f} mm discs clear of "
        "the fuselage and of the wing's leading and trailing edges."
    )
    # Battery position: keep its fraction of the fuselage, refined by the balance step later.
    p["battery"]["x_mm"] = _r(
        p0["battery"]["x_mm"] / f0["length_mm"] * p["fuselage"]["length_mm"], 5
    )
    return p, why


def _fix_balance(p: dict[str, Any], r: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    c = settings["checks"]
    target = (c["static_margin_min"] + c["static_margin_max"]) / 2
    sm_max = r["summary"]["static_margin_max_payload"]["value"] / 100
    sm_min = r["summary"]["static_margin_min_payload"]["value"] / 100
    d_cg = -(target - (sm_max + sm_min) / 2) * r["geometry"]["wing"]["mac_mm"]
    m_bat = r["mass"]["battery"]["value"]
    dx = d_cg * r["summary"]["takeoff_mass"]["value"] / m_bat if m_bat > 0 else 0.0
    q = copy.deepcopy(p)
    lo = p["nose_bay"]["length_mm"] + 20
    hi = p["fuselage"]["length_mm"] * 0.75
    q["battery"]["x_mm"] = _r(min(hi, max(lo, p["battery"]["x_mm"] + dx)), 1)
    return q


def run_scale(
    parameters: dict[str, Any],
    mission: dict[str, Any],
    settings: dict[str, Any] | None,
    target_takeoff_mass_kg: float,
    *,
    mode: str = "full",
    cache_dir: str | None = None,
    progress: ProgressFn | None = None,
    settings_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    t0 = time.time()

    def report(frac: float, label: str) -> None:
        if progress:
            progress(frac, label)

    p0, m0, problems = validate_inputs(parameters, mission)
    if p0 is None or m0 is None or not (target_takeoff_mass_kg and target_takeoff_mass_kg > 0):
        return {
            "valid": False,
            "checks": problems
            or [
                {
                    "key": "input.target",
                    "level": "fail",
                    "label": "Target mass",
                    "message": "The target take-off mass must be above zero.",
                }
            ],
        }
    s = resolve_settings(settings)
    kw = {"cache_dir": cache_dir, "settings_meta": settings_meta}
    report(0.02, "Analysing the current design")
    base = run_analysis(p0, m0, s, mode="fast", **kw)
    if not base.get("valid"):
        return {
            "valid": False,
            "checks": base.get("checks", []),
            "message": "The current design cannot be analysed, so it cannot be scaled.",
        }
    m1 = copy.deepcopy(m0)
    m1["target_takeoff_mass_kg"] = float(target_takeoff_mass_kg)
    if target_takeoff_mass_kg > FINAL_SCALE_ABOVE_KG:
        m1["scale"] = "final"
    store = PolarStore(cache_dir, allow_xfoil=False)
    budget = target_takeoff_mass_kg * 0.99
    mass_design = target_takeoff_mass_kg
    chem = p0["battery"]["chemistry"]
    battery_wh = (
        base["battery"]["energy"]["value"]
        * target_takeoff_mass_kg
        / base["summary"]["takeoff_mass"]["value"]
    )
    history = []
    se = PACK_SPECIFIC_ENERGY_WH_PER_KG[chem]
    wh_endurance: float | None = None
    p, why = p0, {}
    for it in range(8):
        report(0.05 + 0.08 * it, f"Re-solving the layout at {mass_design:.1f} kg (pass {it + 1})")
        p, why = _design_for_mass(p0, m0, base, mass_design, s, battery_wh, store)
        g = build_geometry(p)
        ms = solve_mass(p, g, m1, s, start_kg=mass_design)
        m_bat = ms["pack"]["mass_g"] / 1000
        other = ms["total_max_g"] / 1000 - m_bat
        wh_budget = max(0.0, budget - other) * se
        r = run_analysis(p, m1, s, mode="fast", uncertainty=False, **kw)
        if r.get("valid"):
            end = r["summary"]["endurance_cruise"]["value"]
            vtol_min = r["performance"]["endurance_total"]["value"] - end
            used = ms["pack"]["energy_wh"]
            wh_endurance = (
                used * (m1["target_endurance_min"] + vtol_min) / max(1e-6, end + vtol_min)
            )
        # The target is a take-off mass: the battery fills the mass budget (the endurance this
        # gives is compared with the mission target; ``battery_for_target_endurance_wh`` says what
        # the target alone would need).
        new_wh = wh_budget
        new_mass = min(budget, other + new_wh / se)
        history.append(
            {
                "pass": it + 1,
                "design_mass_kg": round(mass_design, 3),
                "takeoff_mass_kg": round(ms["total_max_g"] / 1000, 3),
                "battery_wh": round(battery_wh, 1),
                "endurance_min": r["summary"]["endurance_cruise"]["value"]
                if r.get("valid")
                else None,
            }
        )
        done = abs(new_mass - mass_design) < 0.005 * mass_design and abs(
            new_wh - battery_wh
        ) < 0.01 * max(battery_wh, 1)
        battery_wh, mass_design = new_wh, max(new_mass, 0.3 * target_takeoff_mass_kg)
        if done:
            break
    p = _design_for_mass(p0, m0, base, mass_design, s, battery_wh, store)[0]
    # Booms: diameter from the bending check (standard tube sizes).
    r = run_analysis(p, m1, s, mode="fast", uncertainty=False, **kw)
    if r.get("valid"):
        p["booms"]["diameter_mm"] = max(
            p0["booms"]["diameter_mm"], r["structure"]["boom"]["min_diameter_mm"]
        )
        why["boom_tube"] = (
            f"Boom diameter {p['booms']['diameter_mm']:g} mm: the smallest standard "
            "tube that passes the boom bending check (full motor thrust and a 3 g "
            "landing) with margin."
        )
        report(0.75, "Balancing (battery position)")
        r = run_analysis(p, m1, s, mode="fast", uncertainty=False, **kw)
        if r.get("valid"):
            p = _fix_balance(p, r, s)
    # Final mass trim: the boom and rounding steps add mass; take it out of the battery so the
    # take-off mass stays within the target.
    for _ in range(5):
        ms = solve_mass(p, build_geometry(p), m1, s, start_kg=mass_design)
        excess = ms["total_max_g"] / 1000 - budget
        if excess <= 0:
            break
        m_bat = ms["pack"]["mass_g"] / 1000
        f = max(0.3, (m_bat - excess * 1.05) / m_bat)
        p["battery"]["capacity_mah"] = math.floor(p["battery"]["capacity_mah"] * f / 100) * 100
    report(0.8, "Full analysis of the scaled design")
    final = run_analysis(p, m1, s, mode=mode, **kw)
    rows = _table(p0, m0, base, p, m1, final, why) if final.get("valid") else []
    ratios = {
        "span": p["wing"]["span_mm"] / p0["wing"]["span_mm"],
        "root_chord": p["wing"]["root_chord_mm"] / p0["wing"]["root_chord_mm"],
        "fuselage_length": p["fuselage"]["length_mm"] / p0["fuselage"]["length_mm"],
        "fuselage_width": p["fuselage"]["width_mm"] / p0["fuselage"]["width_mm"],
        "prop_diameter": p["propulsion"]["prop_diameter_mm"] / p0["propulsion"]["prop_diameter_mm"],
        "tail_arm": p["tail"]["arm_mm"] / p0["tail"]["arm_mm"],
    }
    return json_safe(
        {
            "valid": bool(final.get("valid")),
            "target_takeoff_mass_kg": target_takeoff_mass_kg,
            "parameters": p,
            "mission": m1,
            "table": rows,
            "dimension_ratios": ratios,
            "iterations": history,
            "battery_for_target_endurance_wh": wh_endurance,
            "before": base.get("summary"),
            "analysis": final,
            "checks": final.get("checks", []),
            "duration_s": round(time.time() - t0, 2),
            "notes": [
                "Scaling re-solves the layout: wing loading from the stall margin and best "
                "lift-to-drag, "
                "propellers from disc loading, fuselage from battery volume, tail from tail volume "
                "coefficients, battery from endurance and the mass budget, booms and spar from the "
                "structure checks. Dimensions are never multiplied by one factor.",
                "Construction: "
                f"{'carbon composite (final)' if m1['scale'] == 'final' else m1['scale']}.",
                f"Cruise speed stays at the mission's {m1['cruise_speed_mps']:g} m/s; the stall "
                "margin then "
                "limits the wing loading, so the wing grows almost with the weight. A faster "
                "cruise "
                "speed in the mission would allow a smaller, lighter wing.",
            ],
        }
    )


def _table(
    p0: dict[str, Any],
    m0: dict[str, Any],
    a0: dict[str, Any],
    p1: dict[str, Any],
    m1: dict[str, Any],
    a1: dict[str, Any],
    why: dict[str, str],
) -> list[dict[str, Any]]:
    s0, s1 = a0["summary"], a1["summary"]

    def v(q: dict[str, Any]) -> float:
        return q["value"]

    def row(
        key: str, label: str, before: Any, after: Any, unit: str, reason: str
    ) -> dict[str, Any]:
        return {
            "key": key,
            "label": label,
            "before": before,
            "after": after,
            "unit": unit,
            "why": reason,
        }

    lm0, lm1 = a0["propulsion"]["lift_motor"], a1["propulsion"]["lift_motor"]
    b0, b1 = a0["battery"]["pack"], a1["battery"]["pack"]
    sp0 = a0["structure"]["wing_spar"]["spar"]
    sp1 = a1["structure"]["wing_spar"]["spar"]
    return [
        row(
            "takeoff_mass",
            "Take-off mass",
            v(s0["takeoff_mass"]),
            v(s1["takeoff_mass"]),
            "kg",
            "Converged component build-up; the battery fills what is left of the target mass "
            "(kept 1 % under it so the estimate does not cross the limit).",
        ),
        row(
            "empty_mass",
            "Empty mass",
            v(s0["empty_mass"]),
            v(s1["empty_mass"]),
            "kg",
            f"{m1['scale'].capitalize()} construction; structure, motors and wiring re-sized.",
        ),
        row(
            "wing_span", "Wingspan", p0["wing"]["span_mm"], p1["wing"]["span_mm"], "mm", why["wing"]
        ),
        row(
            "wing_area",
            "Wing area",
            v(a0["aero"]["wing_area"]),
            v(a1["aero"]["wing_area"]),
            "m²",
            why["wing"],
        ),
        row(
            "wing_loading",
            "Wing loading",
            v(a0["aero"]["wing_loading"]),
            v(a1["aero"]["wing_loading"]),
            "kg/m²",
            "Heavier aircraft carry more weight per square metre: the wing grows less than "
            "the weight, limited by the stall margin.",
        ),
        row(
            "cruise_cl",
            "Cruise lift coefficient",
            v(a0["aero"]["cl_cruise"]),
            v(a1["aero"]["cl_cruise"]),
            "",
            "Chosen near the best lift-to-drag point of the wing and airfoil.",
        ),
        row(
            "stall_speed",
            "Stall speed",
            v(s0["stall_speed"]),
            v(s1["stall_speed"]),
            "m/s",
            "The stall speed follows the wing loading and the larger wing's higher maximum lift "
            f"(higher Reynolds number); cruise {m1['cruise_speed_mps']:g} m/s "
            f"stays at least {v(s1['cruise_to_stall']):.2f} x above it.",
        ),
        row(
            "prop_diameter",
            "Lift propeller diameter",
            p0["propulsion"]["prop_diameter_mm"],
            p1["propulsion"]["prop_diameter_mm"],
            "mm",
            why["propellers"],
        ),
        row(
            "motor_class",
            "Lift motor (per motor, generic)",
            f"{lm0['kv_rpm_per_v']:.0f} Kv, {lm0.get('max_electrical_power_w', 0):.0f} W",
            f"{lm1['kv_rpm_per_v']:.0f} Kv, {lm1.get('max_electrical_power_w', 0):.0f} W",
            "",
            "Sized for the hover thrust-to-weight minimum with the bigger propeller and the new "
            "pack voltage: lower Kv on more cells and a larger propeller.",
        ),
        row(
            "hover_power",
            "Hover power",
            v(s0["hover_power"]),
            v(s1["hover_power"]),
            "W",
            "Grows faster than the mass because the disc loading rises.",
        ),
        row(
            "cruise_power",
            "Cruise power",
            v(s0["cruise_power"]),
            v(s1["cruise_power"]),
            "W",
            "Drag x speed through the propeller and motor model at the new size.",
        ),
        row(
            "battery",
            "Battery",
            f"{b0['label']}, {b0['energy_wh']:.0f} Wh",
            f"{b1['label']}, {b1['energy_wh']:.0f} Wh",
            "",
            why["battery"],
        ),
        row(
            "fuselage",
            "Fuselage (length x width x height)",
            f"{p0['fuselage']['length_mm']:.0f} x {p0['fuselage']['width_mm']:.0f} x "
            f"{p0['fuselage']['height_mm']:.0f}",
            f"{p1['fuselage']['length_mm']:.0f} x {p1['fuselage']['width_mm']:.0f} x "
            f"{p1['fuselage']['height_mm']:.0f}",
            "mm",
            why["fuselage"],
        ),
        row(
            "tail",
            "Tail (span x chord, arm)",
            f"{p0['tail']['span_mm']:.0f} x {p0['tail']['chord_mm']:.0f}, "
            f"{p0['tail']['arm_mm']:.0f}",
            f"{p1['tail']['span_mm']:.0f} x {p1['tail']['chord_mm']:.0f}, "
            f"{p1['tail']['arm_mm']:.0f}",
            "mm",
            why["tail"],
        ),
        row(
            "booms",
            "Booms (offset, length, diameter)",
            f"{p0['booms']['lateral_offset_mm']:.0f}, {p0['booms']['length_mm']:.0f}, "
            f"{p0['booms']['diameter_mm']:g}",
            f"{p1['booms']['lateral_offset_mm']:.0f}, {p1['booms']['length_mm']:.0f}, "
            f"{p1['booms']['diameter_mm']:g}",
            "mm",
            why["booms"] + " " + why.get("boom_tube", ""),
        ),
        row(
            "spar",
            "Wing spar",
            sp0,
            sp1,
            "",
            "Sized by the spar bending check at the manoeuvre load (tube for the printed "
            "prototype, "
            "carbon caps for the composite wing).",
        ),
        row(
            "endurance",
            "Wing-flight endurance",
            v(s0["endurance_cruise"]),
            v(s1["endurance_cruise"]),
            "min",
            f"Target {m1['target_endurance_min']:g} min; result of the battery and the new "
            "cruise power.",
        ),
        row(
            "static_margin",
            "Static margin (lightest camera)",
            v(s0["static_margin_min_payload"]),
            v(s1["static_margin_min_payload"]),
            "% MAC",
            "Battery placed to centre the static margin in the settings range.",
        ),
    ]
