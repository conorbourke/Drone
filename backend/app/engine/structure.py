"""Basic structure checks: wing spar root bending and tip deflection, boom bending.

Loads (docs/phases/PHASE3.md section 2):

* Wing: the AVL span loading (c cl along the span, from the high-lift trimmed case) scaled so the
  wing carries n x W x (wing share of the total lift) at the manoeuvre load factor n from settings
  (default 3.0), times the structural safety factor (default 1.5) for the ultimate load. Root
  bending moment M = integral of l(y) y dy over the semi-span (spar continuous through the
  fuselage; inertia relief from the wing's own weight ignored, which is conservative).
* Prototype spar: a round carbon tube, stress sigma = M (D/2) / I with I = pi (D^4 - d^4) / 64;
  tip deflection by double integration of M/(E I) at the limit load (n x W, no safety factor) and
  at 1 g. Final-scale spar: two carbon caps of area A at depth h (the Tier 1 sizing), sigma =
  M / (A h).
* Booms: cantilevers from the wing spar. Case 1, full static thrust of a lift motor at its station
  (from the propulsion model at full throttle). Case 2, a hard vertical landing: n_land = 3 x the
  weight shared by the four skid or leg attachment points (estimate for small-UAV skid landings at
  about 2 m/s; Raymer ch. 11 uses 3 g for light-aircraft gear). Both times the safety factor.

Materials (estimates, stated with sources; replaced by catalogue tubes in Phase 4):

* Roll-wrapped carbon/epoxy tube: axial modulus E = 100 GPa (supplier datasheets for UD-rich
  roll-wrapped tubes 90-130 GPa), flexural strength ~1000 MPa, allowable 500 MPa at ultimate load
  after a 0.5 knock-down for compression-side local buckling, defects, clamp holes and fatigue.
* Final-scale UD carbon caps: E = 120 GPa, allowable 600 MPa (the Tier 1 knock-down from
  ~1500 MPa UD strength).

Pass/warn/fail on the margin of safety MS = allowable / stress - 1 at ultimate load: fail below
0, warn below 0.25 (engine rule: covers the +/-15-20 % uncertainty of these estimates), ok above.
Tip deflection at the limit load above 10 % of the semi-span is a warning (stiffness rule of
thumb for small UAV wings, estimate).
"""

from __future__ import annotations

import math
from typing import Any

from app.engine.mass import carbon_tube_mass_per_m, carbon_tube_wall_m
from app.engine.quantity import G0

TUBE_E_PA = 100e9
TUBE_ALLOWABLE_PA = 500e6
CAP_E_PA = 120e9
CAP_ALLOWABLE_PA = 600e6
LANDING_LOAD_FACTOR = 3.0
MARGIN_WARN = 0.25
TIP_DEFLECTION_WARN = 0.10
#: Standard outer diameters (mm) and walls (mm) of commonly sold roll-wrapped carbon tubes.
STANDARD_TUBES_MM = [8, 10, 12, 14, 15, 16, 18, 20, 22, 25, 28, 30, 32, 35, 40, 45, 50]
STANDARD_WALLS_MM = [1.0, 1.5, 2.0, 2.5, 3.0]
MATERIAL_SOURCE = (
    "Roll-wrapped carbon/epoxy tube, E = 100 GPa, allowable 500 MPa at ultimate load (datasheet "
    "flexural strength ~1000 MPa with a 0.5 knock-down for local buckling, defects, holes and "
    "fatigue); estimate until Phase 4 catalogue tubes."
)


def tube_section(outer_mm: float, wall_mm: float) -> dict[str, float]:
    d_o = outer_mm / 1000
    d_i = max(0.0, d_o - 2 * wall_mm / 1000)
    i = math.pi * (d_o**4 - d_i**4) / 64
    return {"I_m4": i, "c_m": d_o / 2, "area_m2": math.pi * (d_o**2 - d_i**2) / 4}


def _level(margin: float) -> str:
    if not math.isfinite(margin) or margin < 0:
        return "fail"
    return "warn" if margin < MARGIN_WARN else "ok"


def span_load(strips: list[dict[str, Any]], total_lift_n: float) -> list[dict[str, float]]:
    """Scale the AVL c*cl distribution (one side, y >= 0) to carry ``total_lift_n`` (both sides).

    Returns stations sorted by y with ``y`` (m), ``w`` (strip width, m) and ``l`` (N/m)."""
    right = [s for s in strips if s["y"] > 0]
    integral = sum(s["ccl"] * s["width"] for s in right)
    k = (total_lift_n / 2) / integral if integral > 0 else 0.0
    return [
        {"y": s["y"], "w": s["width"], "l": k * s["ccl"]}
        for s in sorted(right, key=lambda r: r["y"])
    ]


def schrenk_strips(g: dict[str, Any], n: int = 40) -> list[dict[str, float]]:
    """Schrenk's approximate span loading (NACA TM 948, 1940): the average of the planform
    chord and the elliptic chord of the same area; strips on both sides, metres."""
    w = g["wing"]
    b = w["span_mm"] / 1000
    s = w["area_m2"]
    semi = b / 2
    out = []
    for i in range(n):
        y = (i + 0.5) * semi / n
        c = (w["root_chord_mm"] - (w["root_chord_mm"] - w["tip_chord_mm"]) * y / semi) / 1000
        ce = 4 * s / (math.pi * b) * math.sqrt(max(0.0, 1 - (y / semi) ** 2))
        for sign in (1, -1):
            out.append({"y": sign * y, "width": semi / n, "ccl": (c + ce) / 2, "chord": c})
    return out


def bending_moment(load: list[dict[str, float]], y0: float = 0.0) -> float:
    """Moment (N m) about the station y0 of the load outboard of it."""
    return sum(s["l"] * s["w"] * (s["y"] - y0) for s in load if s["y"] > y0)


def tip_deflection(
    load: list[dict[str, float]], ei: float, semi_m: float, n_pts: int = 60
) -> float:
    """Cantilever tip deflection (m) by integrating M/(EI) twice from the root (y = 0)."""
    if ei <= 0:
        return float("inf")
    ys = [semi_m * i / n_pts for i in range(n_pts + 1)]
    curv = [bending_moment(load, y) / ei for y in ys]
    slope = [0.0]
    for i in range(1, len(ys)):
        slope.append(slope[-1] + 0.5 * (curv[i] + curv[i - 1]) * (ys[i] - ys[i - 1]))
    defl = 0.0
    for i in range(1, len(ys)):
        defl += 0.5 * (slope[i] + slope[i - 1]) * (ys[i] - ys[i - 1])
    return defl


def wing_spar_check(
    g: dict[str, Any],
    spar: dict[str, Any],
    strips: list[dict[str, Any]],
    weight_n: float,
    wing_lift_share: float,
    n: float,
    sf: float,
) -> dict[str, Any]:
    semi = g["wing"]["span_mm"] / 2000
    limit_lift = n * weight_n * wing_lift_share
    load_ult = span_load(strips, limit_lift * sf)
    load_lim = span_load(strips, limit_lift)
    load_1g = span_load(strips, weight_n * wing_lift_share)
    m_ult = bending_moment(load_ult)
    if spar["kind"] == "tube":
        sec = tube_section(spar["outer_mm"], spar["wall_mm"])
        stress = m_ult * sec["c_m"] / sec["I_m4"]
        allow = TUBE_ALLOWABLE_PA
        ei = TUBE_E_PA * sec["I_m4"]
        desc = f"carbon tube {spar['outer_mm']:g} mm x {spar['wall_mm']:.1f} mm wall"
        source = MATERIAL_SOURCE
    else:
        stress = (
            m_ult / (spar["cap_area_m2"] * spar["depth_m"])
            if spar["cap_area_m2"] > 0
            else float("inf")
        )
        allow = CAP_ALLOWABLE_PA
        ei = CAP_E_PA * 2 * spar["cap_area_m2"] * (spar["depth_m"] / 2) ** 2
        desc = (
            f"carbon spar caps {spar['cap_area_m2'] * 1e6:.0f} mm² each, "
            f"{spar['depth_m'] * 1000:.0f} mm deep"
        )
        source = (
            "UD carbon caps, E = 120 GPa, allowable 600 MPa (Tier 1 knock-down from ~1500 MPa)."
        )
    margin = allow / stress - 1 if stress > 0 else float("inf")
    defl_lim = tip_deflection(load_lim, ei, semi)
    defl_1g = tip_deflection(load_1g, ei, semi)
    return {
        "spar": desc,
        "spar_detail": spar,
        "load_factor": n,
        "safety_factor": sf,
        "root_moment_ultimate_nm": m_ult,
        "stress_mpa": stress / 1e6,
        "allowable_mpa": allow / 1e6,
        "margin": margin,
        "level": _level(margin),
        "tip_deflection_limit_mm": defl_lim * 1000,
        "tip_deflection_1g_mm": defl_1g * 1000,
        "tip_deflection_fraction": defl_lim / semi if semi > 0 else float("nan"),
        "span_loading": [{"y_mm": s["y"] * 1000, "lift_n_per_m": s["l"]} for s in load_lim],
        "source": source,
    }


def size_spar_tube(
    g: dict[str, Any],
    strips: list[dict[str, Any]],
    weight_n: float,
    wing_lift_share: float,
    n: float,
    sf: float,
) -> dict[str, Any]:
    """Lightest standard tube that fits the root depth and reaches MS >= 0.25 at ultimate load."""
    w = g["wing"]
    max_od = 0.85 * w["thickness_ratio"] * w["root_chord_mm"]
    load = span_load(strips, n * weight_n * wing_lift_share * sf)
    m_ult = bending_moment(load)
    best = None
    largest = None
    for od in STANDARD_TUBES_MM:
        if od > max_od:
            continue
        for wall in STANDARD_WALLS_MM:
            if 2 * wall >= od * 0.6:
                continue
            sec = tube_section(od, wall)
            stress = m_ult * sec["c_m"] / sec["I_m4"]
            ms = TUBE_ALLOWABLE_PA / stress - 1 if stress > 0 else float("inf")
            mass = carbon_tube_mass_per_m(od, wall)
            cand = {"outer_mm": float(od), "wall_mm": wall, "margin": ms, "mass_g_per_m": mass}
            if largest is None or ms > largest["margin"]:
                largest = cand
            if ms >= MARGIN_WARN and (best is None or mass < best["mass_g_per_m"]):
                best = cand
    if best is not None:
        return {**best, "fits": True, "max_outer_mm": max_od}
    if largest is not None:
        return {**largest, "fits": False, "max_outer_mm": max_od}
    od = max(8.0, math.floor(max_od))
    return {
        "outer_mm": od,
        "wall_mm": carbon_tube_wall_m(od) * 1000,
        "margin": float("nan"),
        "fits": False,
        "max_outer_mm": max_od,
        "mass_g_per_m": carbon_tube_mass_per_m(od),
    }


def boom_check(
    p: dict[str, Any], g: dict[str, Any], max_thrust_n: float, weight_n: float, sf: float
) -> dict[str, Any]:
    """Boom root bending at the wing spar for full thrust and for a hard landing."""
    w = g["wing"]
    spar_x = (
        w["root_le"][0]
        + abs(g["booms"][0]["start"][1]) * math.tan(math.radians(w["sweep_le_deg"]))
        + w["x_max_thickness"] * w["root_chord_mm"]
    ) / 1000
    xf = g["front_rotor_x_mm"] / 1000
    xr = g["rear_rotor_x_mm"] / 1000
    arm = max(abs(spar_x - xf), abs(xr - spar_x))
    m_thrust = max_thrust_n * arm * sf
    gear = p["landing_gear"]["type"]
    if gear == "skids":
        gear_x = [xf + 0.25 * (xr - xf), xr - 0.25 * (xr - xf)]
    elif gear == "legs":
        gear_x = [xf, xr]
    else:
        gear_x = []
    if gear_x:
        reaction = LANDING_LOAD_FACTOR * weight_n / 4
        m_land = max(reaction * abs(x - spar_x) for x in gear_x) * sf
    else:
        m_land = 0.0
    d = p["booms"]["diameter_mm"]
    wall = carbon_tube_wall_m(d) * 1000
    sec = tube_section(d, wall)
    m_max = max(m_thrust, m_land)
    stress = m_max * sec["c_m"] / sec["I_m4"]
    margin = TUBE_ALLOWABLE_PA / stress - 1 if stress > 0 else float("inf")
    tip = max_thrust_n * arm**3 / (3 * TUBE_E_PA * sec["I_m4"])
    return {
        "tube": f"carbon tube {d:g} mm x {wall:.1f} mm wall",
        "outer_mm": d,
        "wall_mm": wall,
        "arm_mm": arm * 1000,
        "moment_thrust_ultimate_nm": m_thrust,
        "moment_landing_ultimate_nm": m_land,
        "critical_case": "full motor thrust" if m_thrust >= m_land else "hard landing",
        "stress_mpa": stress / 1e6,
        "allowable_mpa": TUBE_ALLOWABLE_PA / 1e6,
        "margin": margin,
        "level": _level(margin),
        "tip_deflection_full_thrust_mm": tip * 1000,
        "landing_load_factor": LANDING_LOAD_FACTOR,
        "safety_factor": sf,
        "source": MATERIAL_SOURCE,
        "weight_n": weight_n,
        "g": G0,
    }


def min_boom_diameter(
    p: dict[str, Any], g: dict[str, Any], max_thrust_n: float, weight_n: float, sf: float
) -> float:
    """Smallest standard boom diameter (Tier 1 wall rule) with MS >= 0.25."""
    for od in STANDARD_TUBES_MM:
        q = {**p, "booms": {**p["booms"], "diameter_mm": float(od)}}
        if boom_check(q, g, max_thrust_n, weight_n, sf)["margin"] >= MARGIN_WARN:
            return float(od)
    return float(STANDARD_TUBES_MM[-1])
