"""Derived geometry: Python port of the Tier 1 ``buildGeometry`` (frontend/src/engine/geometry.ts).

Coordinates (docs/phases/PHASE2.md section 1): origin at the nose tip on the centreline, x aft,
y to starboard, z up; millimetres in the parameters and in this module's output (areas in m^2).
The formulas and conventions are identical to the TypeScript engine so the golden fixture
``shared/fixtures/tier1_cases.json`` is reproduced to 0.1 % (``tests/engine/test_geometry.py``).

Render primitives are not ported (the browser draws); everything the analysis needs is.
"""

from __future__ import annotations

import copy
import itertools
import math
import re
from typing import Any, Literal

from app.engine import airfoils as airfoil_lib

# ---------------------------------------------------------------------------
# Constants shared with the TypeScript engine (frontend/src/engine/constants.ts)
# ---------------------------------------------------------------------------

#: Schema v2 defaults for v1 documents (Phase 2 contract section 2).
DESIGN_V2_DEFAULTS: dict[str, Any] = {
    "wing_twist_deg": 0.0,
    "boom_diameter_mm": 20.0,
    "tail_v_angle_deg": 40.0,
    "tail_airfoil": "naca0009",
    "propulsion": {"prop_diameter_mm": 330.0, "prop_pitch_mm": 140.0, "prop_blades": 2},
    "battery": {
        "chemistry": "lipo",
        "cells_series": 6,
        "cells_parallel": 1,
        "capacity_mah": 5000.0,
        "x_mm": 290.0,
    },
    "allowances": {"avionics_g": 220.0, "wiring_fraction": 0.06},
}

#: Nominal thickness, its position, camber and its position (fractions of chord) used before the
#: airfoil library is consulted. UIUC coordinates database nominal values; NACA exact.
AIRFOIL_SHAPE_FALLBACK: dict[str, dict[str, float]] = {
    "sd7037": {"t": 0.092, "xt": 0.28, "m": 0.030, "xm": 0.40},
    "sd7062": {"t": 0.140, "xt": 0.26, "m": 0.040, "xm": 0.40},
    "e387": {"t": 0.091, "xt": 0.31, "m": 0.038, "xm": 0.45},
    "mh32": {"t": 0.087, "xt": 0.29, "m": 0.024, "xm": 0.40},
    "s3021": {"t": 0.095, "xt": 0.28, "m": 0.030, "xm": 0.40},
    "ag35": {"t": 0.087, "xt": 0.28, "m": 0.022, "xm": 0.40},
    "clarky": {"t": 0.117, "xt": 0.28, "m": 0.034, "xm": 0.42},
    "naca2412": {"t": 0.12, "xt": 0.30, "m": 0.02, "xm": 0.40},
    "naca4412": {"t": 0.12, "xt": 0.30, "m": 0.04, "xm": 0.40},
    "naca0009": {"t": 0.09, "xt": 0.30, "m": 0.0, "xm": 0.0},
    "naca0012": {"t": 0.12, "xt": 0.30, "m": 0.0, "xm": 0.0},
}
AIRFOIL_SHAPE_GENERIC = {"t": 0.10, "xt": 0.30, "m": 0.02, "xm": 0.40}

ROUNDED_RECT_CORNER_FRACTION = 0.25
NOSE_LENGTH_DIAMETERS = 1.2
NOSE_LENGTH_MAX_FRACTION = 0.25
TAIL_LENGTH_DIAMETERS = 2.5
TAIL_LENGTH_MAX_FRACTION = 0.35
TAIL_END_SCALE = 0.3
NOSE_SEGMENTS = 12

ShapeMode = Literal["library", "builtin"]


def with_defaults(params: dict[str, Any]) -> dict[str, Any]:
    """Deep copy of the parameters with every schema v2 field present (never mutates)."""
    p = copy.deepcopy(params)
    d = DESIGN_V2_DEFAULTS
    p["wing"].setdefault("twist_deg", d["wing_twist_deg"])
    p["booms"].setdefault("diameter_mm", d["boom_diameter_mm"])
    p["tail"].setdefault("v_angle_deg", d["tail_v_angle_deg"])
    p["tail"].setdefault("airfoil", d["tail_airfoil"])
    for block in ("propulsion", "battery", "allowances"):
        p[block] = {**d[block], **(p.get(block) or {})}
    return p


def airfoil_shape(airfoil_id: str, mode: ShapeMode = "library") -> dict[str, Any]:
    """Thickness and camber of an airfoil.

    ``library``: computed from the committed coordinates (what the browser uses once
    ``/api/airfoils`` has loaded). ``builtin``: the nominal table (what the golden fixture uses).
    Unknown ids fall back to NACA digits, then a generic 10 % section.
    """
    key = airfoil_id.lower()
    if mode == "library" and key in airfoil_lib.LIBRARY_BY_ID:
        sg = airfoil_lib.section_geometry(key)
        if sg["thickness_pct"] > 0:
            return {
                "t": sg["thickness_pct"] / 100,
                "xt": (sg["x_thickness_pct"] / 100) if sg["x_thickness_pct"] > 0 else 0.3,
                "m": sg["camber_pct"] / 100,
                "xm": sg["x_camber_pct"] / 100,
                "origin": "summary",
            }
    if key in AIRFOIL_SHAPE_FALLBACK:
        return {**AIRFOIL_SHAPE_FALLBACK[key], "origin": "builtin"}
    m = re.fullmatch(r"naca(\d)(\d)(\d\d)", key)
    if m:
        cam = int(m.group(1)) / 100
        return {
            "t": int(m.group(3)) / 100,
            "xt": 0.3,
            "m": cam,
            "xm": int(m.group(2)) / 10 if cam > 0 else 0.0,
            "origin": "builtin",
        }
    return {**AIRFOIL_SHAPE_GENERIC, "origin": "generic"}


# ---------------------------------------------------------------------------
# Closed-form planform relations (Raymer ch. 4 "Wing geometry")
# ---------------------------------------------------------------------------


def trapezoid(span: float, root: float, tip: float) -> dict[str, float]:
    """Area, aspect ratio, taper, MAC and its spanwise station for a trapezoidal wing."""
    taper = tip / root
    area = span * (root + tip) / 2
    return {
        "taper": taper,
        "area": area,
        "ar": span * span / area,
        "mac": (2 / 3) * root * (1 + taper + taper * taper) / (1 + taper),
        "mac_y": (span / 6) * (1 + 2 * taper) / (1 + taper),
    }


def sweep_at(n: float, sweep_le_rad: float, ar: float, taper: float) -> float:
    """Sweep of the chord line at fraction n: tan L_n = tan L_LE - (4n/A)(1-l)/(1+l)."""
    return math.atan(math.tan(sweep_le_rad) - (4 * n / ar) * ((1 - taper) / (1 + taper)))


def section_perimeter(kind: str, w: float, h: float) -> float:
    if w <= 0 or h <= 0:
        return 0.0
    if kind == "ellipse":
        a, b = w / 2, h / 2
        return math.pi * (3 * (a + b) - math.sqrt((3 * a + b) * (a + 3 * b)))
    r = ROUNDED_RECT_CORNER_FRACTION * min(w, h)
    return 2 * (w + h) - (8 - 2 * math.pi) * r


def section_area(kind: str, w: float, h: float) -> float:
    if w <= 0 or h <= 0:
        return 0.0
    if kind == "ellipse":
        return math.pi * w * h / 4
    r = ROUNDED_RECT_CORNER_FRACTION * min(w, h)
    return w * h - (4 - math.pi) * r * r


def lifting_wetted(exposed_area: float, tc: float) -> float:
    """2 S_exposed (1 + 0.25 t/c) (Torenbeek 1982; within ~1 % of Raymer ch. 7)."""
    return 2 * exposed_area * (1 + 0.25 * tc)


def _fuselage(p: dict[str, Any]) -> dict[str, Any]:
    f = p["fuselage"]
    length = f["length_mm"]
    d = max(f["width_mm"], f["height_mm"])
    nose_len = min(NOSE_LENGTH_DIAMETERS * d, NOSE_LENGTH_MAX_FRACTION * length)
    tail_len = min(TAIL_LENGTH_DIAMETERS * d, TAIL_LENGTH_MAX_FRACTION * length)
    perim_full = section_perimeter(f["cross_section"], f["width_mm"], f["height_mm"])

    def station(x: float, s: float) -> dict[str, float]:
        return {
            "x_mm": x,
            "width_mm": f["width_mm"] * s,
            "height_mm": f["height_mm"] * s,
            "perimeter_mm": perim_full * s,
            "scale": s,
        }

    stations = []
    for i in range(NOSE_SEGMENTS + 1):
        x = nose_len * i / NOSE_SEGMENTS
        u = 1 - x / nose_len
        stations.append(station(x, math.sqrt(max(0.0, 1 - u * u))))
    stations.append(station(length - tail_len, 1.0))
    stations.append(station(length, TAIL_END_SCALE))
    wet = 0.0
    for a, b in itertools.pairwise(stations):
        dx = b["x_mm"] - a["x_mm"]
        dr = (b["perimeter_mm"] - a["perimeter_mm"]) / (2 * math.pi)
        wet += (a["perimeter_mm"] + b["perimeter_mm"]) / 2 * math.sqrt(dx * dx + dr * dr)
    amax = section_area(f["cross_section"], f["width_mm"], f["height_mm"])
    deq = math.sqrt(4 * amax / math.pi)
    return {
        "length_mm": length,
        "width_mm": f["width_mm"],
        "height_mm": f["height_mm"],
        "cross_section": f["cross_section"],
        "stations": stations,
        "nose_length_mm": nose_len,
        "tail_length_mm": tail_len,
        "max_cross_section_area_m2": amax / 1e6,
        "equivalent_diameter_mm": deq,
        "fineness_ratio": length / deq if deq > 0 else float("nan"),
        "wetted_area_m2": wet / 1e6,
        "nose_bay": {
            "x0_mm": 0.0,
            "x1_mm": p["nose_bay"]["length_mm"],
            "width_mm": p["nose_bay"]["width_mm"],
            "height_mm": p["nose_bay"]["height_mm"],
        },
    }


def _fmt(v: float) -> str:
    return str(round(v)) if math.isfinite(v) else "?"


def build_geometry(params: dict[str, Any], shapes: ShapeMode = "library") -> dict[str, Any]:
    """Derived geometry shared by every analysis module (mm; areas in m^2)."""
    p = with_defaults(params)
    statuses: list[dict[str, Any]] = []
    w = p["wing"]

    wing_shape = airfoil_shape(w["airfoil"], shapes)
    tz = trapezoid(w["span_mm"], w["root_chord_mm"], w["tip_chord_mm"])
    sweep_le = math.radians(w["sweep_deg"])
    semi = w["span_mm"] / 2
    fus_half = p["fuselage"]["width_mm"] / 2

    def chord_at(y: float) -> float:
        return w["root_chord_mm"] - (w["root_chord_mm"] - w["tip_chord_mm"]) * (y / semi)

    c_fus = chord_at(min(fus_half, semi))
    exposed = max(0.0, tz["area"] - min(fus_half, semi) * (w["root_chord_mm"] + c_fus))
    mac_xle = w["x_le_mm"] + tz["mac_y"] * math.tan(sweep_le)
    dihedral = math.radians(w["dihedral_deg"])
    wing = {
        "span_mm": w["span_mm"],
        "area_m2": tz["area"] / 1e6,
        "exposed_area_m2": exposed / 1e6,
        "aspect_ratio": tz["ar"],
        "taper_ratio": tz["taper"],
        "root_chord_mm": w["root_chord_mm"],
        "tip_chord_mm": w["tip_chord_mm"],
        "mac_mm": tz["mac"],
        "mac_y_mm": tz["mac_y"],
        "mac_x_le_mm": mac_xle,
        "ac_x_mm": mac_xle + 0.25 * tz["mac"],
        "sweep_le_deg": w["sweep_deg"],
        "sweep_quarter_chord_deg": math.degrees(sweep_at(0.25, sweep_le, tz["ar"], tz["taper"])),
        "sweep_max_thickness_deg": math.degrees(
            sweep_at(wing_shape["xt"], sweep_le, tz["ar"], tz["taper"])
        ),
        "thickness_ratio": wing_shape["t"],
        "x_max_thickness": wing_shape["xt"],
        "camber": wing_shape["m"],
        "shape_origin": wing_shape["origin"],
        "wetted_area_m2": lifting_wetted(exposed, wing_shape["t"]) / 1e6,
        "root_le": [w["x_le_mm"], 0.0, w["z_mm"]],
        "tip_le": [
            w["x_le_mm"] + semi * math.tan(sweep_le),
            semi,
            w["z_mm"] + semi * math.tan(dihedral),
        ],
        "incidence_deg": w["incidence_deg"],
        "twist_deg": w["twist_deg"],
        "dihedral_deg": w["dihedral_deg"],
        "airfoil": w["airfoil"],
    }

    # ----- Booms and rotors -----
    bx0 = w["x_le_mm"] + p["booms"]["x_offset_mm"]
    bx1 = bx0 + p["booms"]["length_mm"]
    yo = p["booms"]["lateral_offset_mm"]
    booms = [
        {
            "side": side,
            "start": [bx0, y, w["z_mm"]],
            "end": [bx1, y, w["z_mm"]],
            "diameter_mm": p["booms"]["diameter_mm"],
            "length_mm": p["booms"]["length_mm"],
        }
        for side, y in (("left", -yo), ("right", yo))
    ]
    xf = bx0 + p["motors"]["front_x_mm"]
    xr = bx0 + p["motors"]["rear_x_mm"]
    zm = w["z_mm"] + p["motors"]["height_mm"]
    dia = p["propulsion"]["prop_diameter_mm"]
    layout = p["layout"]
    front_tilts = layout == "front_tilt"
    rear_tilts = layout == "rear_tilt"
    rotors = [
        {
            "id": "front_left",
            "position": [xf, -yo, zm],
            "diameter_mm": dia,
            "tilts": front_tilts,
            "stopped_in_cruise": not front_tilts,
        },
        {
            "id": "front_right",
            "position": [xf, yo, zm],
            "diameter_mm": dia,
            "tilts": front_tilts,
            "stopped_in_cruise": not front_tilts,
        },
        {
            "id": "rear_left",
            "position": [xr, -yo, zm],
            "diameter_mm": dia,
            "tilts": rear_tilts,
            "stopped_in_cruise": not rear_tilts,
        },
        {
            "id": "rear_right",
            "position": [xr, yo, zm],
            "diameter_mm": dia,
            "tilts": rear_tilts,
            "stopped_in_cruise": not rear_tilts,
        },
    ]
    if layout == "quad_pusher":
        rotors.append(
            {
                "id": "pusher",
                "position": [p["pusher"]["x_mm"], 0.0, 0.0],
                "diameter_mm": p["pusher"]["prop_diameter_mm"],
                "tilts": False,
                "stopped_in_cruise": False,
            }
        )
    tilt_hinge_x = None if layout == "quad_pusher" else bx0 + p["tilt"]["axis_x_mm"]

    # ----- Tail -----
    t = p["tail"]
    tail_shape = airfoil_shape(t["airfoil"], shapes)
    is_v = t["type"] in ("v_tail", "inverted_v")
    gamma_deg = ((-1 if t["type"] == "inverted_v" else 1) * t["v_angle_deg"]) if is_v else 0.0
    gamma = math.radians(abs(gamma_deg))
    span_chord = t["span_mm"] * t["chord_mm"] / 1e6
    if is_v:
        planform = span_chord / math.cos(gamma)
        h_area = planform * math.cos(gamma)
        v_area = planform * math.sin(gamma)
        pitch_area = planform * math.cos(gamma) ** 2
        tail_ar = t["span_mm"] / (t["chord_mm"] * math.cos(gamma))
    else:
        fins = 2 if t["type"] == "twin_boom_h" else 1
        h_area = span_chord
        v_area = fins * t["height_mm"] * t["chord_mm"] / 1e6
        planform = h_area + v_area
        pitch_area = h_area
        tail_ar = t["span_mm"] / t["chord_mm"]
    tail_c4 = wing["ac_x_mm"] + t["arm_mm"]
    tail_le = tail_c4 - 0.25 * t["chord_mm"]
    tail_te = tail_le + t["chord_mm"]
    twin = t["type"] == "twin_boom_h"
    tail_z = (w["z_mm"] if twin else 0.0) + t["height_mm"]
    support = max(0.0, tail_te - bx1) if twin else max(0.0, tail_te - p["fuselage"]["length_mm"])
    s_ref = tz["area"] / 1e6
    tail = {
        "type": t["type"],
        "span_mm": t["span_mm"],
        "planform_area_m2": planform,
        "horizontal_area_m2": h_area,
        "vertical_area_m2": v_area,
        "pitch_effective_area_m2": pitch_area,
        "aspect_ratio": tail_ar,
        "chord_mm": t["chord_mm"],
        "panel_angle_deg": gamma_deg,
        "arm_mm": t["arm_mm"],
        "quarter_chord_x_mm": tail_c4,
        "le_x_mm": tail_le,
        "te_x_mm": tail_te,
        "z_mm": tail_z,
        "height_mm": t["height_mm"],
        "thickness_ratio": tail_shape["t"],
        "x_max_thickness": tail_shape["xt"],
        "wetted_area_m2": lifting_wetted(planform, tail_shape["t"]),
        "horizontal_volume_coefficient": h_area * t["arm_mm"] / (s_ref * tz["mac"]),
        "vertical_volume_coefficient": v_area * t["arm_mm"] / (s_ref * w["span_mm"]),
        "support_length_mm": support,
        "support_kind": "booms" if twin else "centre",
        "airfoil": t["airfoil"],
    }

    fuselage = _fuselage(p)
    gear_bottom = min(-p["fuselage"]["height_mm"] / 2, w["z_mm"] - p["booms"]["diameter_mm"] / 2)
    gear_bottom -= max(0.0, p["landing_gear"]["height_mm"])

    # ----- Geometry statuses (same rules and wording as Tier 1) -----
    mot = p["motors"]
    if mot["rear_x_mm"] - mot["front_x_mm"] < dia:
        statuses.append(
            {
                "key": "geometry.props_fore_aft",
                "label": "Propeller clearance (front to rear)",
                "level": "fail",
                "message": f"The front and rear propellers on each boom overlap: they are "
                f"{_fmt(mot['rear_x_mm'] - mot['front_x_mm'])} mm apart but {_fmt(dia)} mm across. "
                "Move the motors further apart or choose smaller propellers.",
            }
        )
    if 2 * yo < dia:
        statuses.append(
            {
                "key": "geometry.props_left_right",
                "label": "Propeller clearance (left to right)",
                "level": "fail",
                "message": "The left and right propellers overlap: the booms are "
                f"{_fmt(2 * yo)} mm "
                f"apart but the propellers are {_fmt(dia)} mm across. Move the booms outwards or "
                "choose smaller propellers.",
            }
        )
    if yo - dia / 2 < fus_half and 2 * yo >= dia:
        statuses.append(
            {
                "key": "geometry.props_fuselage",
                "label": "Propeller clearance (fuselage)",
                "level": "warn",
                "message": f"Seen from above, the propeller discs reach over the fuselage side "
                f"({_fmt(fus_half - (yo - dia / 2))} mm overlap). In hover the fuselage blocks "
                "part "
                "of the airflow; move the booms outwards if you can.",
            }
        )
    if yo <= semi:
        le_boom = w["x_le_mm"] + yo * math.tan(sweep_le)
        te_boom = le_boom + chord_at(yo)
        if xf + dia / 2 > le_boom:
            statuses.append(
                {
                    "key": "geometry.front_prop_wing",
                    "label": "Front propeller over the wing",
                    "level": "warn",
                    "message": "The front propeller discs reach "
                    f"{_fmt(xf + dia / 2 - le_boom)} mm over "
                    "the wing leading edge. The wing then blocks part of the hover airflow "
                    "(more hover "
                    "power); move the boom front or the front motor forward.",
                }
            )
        if xr - dia / 2 < te_boom:
            statuses.append(
                {
                    "key": "geometry.rear_prop_wing",
                    "label": "Rear propeller over the wing",
                    "level": "warn",
                    "message": "The rear propeller discs reach "
                    f"{_fmt(te_boom - (xr - dia / 2))} mm "
                    "over the wing trailing edge. Move the rear motor aft to keep the wing out "
                    "of the "
                    "hover airflow.",
                }
            )
    else:
        statuses.append(
            {
                "key": "geometry.booms_outside_wing",
                "label": "Booms outside the wing",
                "level": "fail",
                "message": "The booms are further out than the wing tips, so nothing holds them. "
                "Reduce the boom offset or increase the span.",
            }
        )
    if mot["rear_x_mm"] > p["booms"]["length_mm"] or mot["front_x_mm"] < 0:
        statuses.append(
            {
                "key": "geometry.motor_off_boom",
                "label": "Motor beyond the boom",
                "level": "warn",
                "message": "A motor station lies beyond the end of the boom. Lengthen the boom or "
                "move the motor onto it.",
            }
        )
    if tilt_hinge_x is not None:
        tilt_motor_x = xf if front_tilts else xr
        if abs(tilt_hinge_x - tilt_motor_x) > 0.25 * dia:
            statuses.append(
                {
                    "key": "geometry.tilt_axis",
                    "label": "Tilt axis position",
                    "level": "warn",
                    "message": "The tilt axis is "
                    f"{_fmt(abs(tilt_hinge_x - tilt_motor_x))} mm from the "
                    f"{'front' if front_tilts else 'rear'} motors that tilt. Put the axis close to "
                    "(normally just behind or in front of) the tilting motors.",
                }
            )
    if support > 0:
        statuses.append(
            {
                "key": "geometry.tail_support",
                "label": "Tail support",
                "level": "info",
                "message": (
                    f"The tail sits {_fmt(support)} mm behind the boom ends; the booms are "
                    "assumed to "
                    "be extended to carry it (included in the mass)."
                    if twin
                    else f"The tail sits {_fmt(support)} mm behind the fuselage; a carbon tail "
                    "boom "
                    "of that length is assumed (included in the mass and drag)."
                ),
            }
        )
    if is_v and (t["v_angle_deg"] < 10 or t["v_angle_deg"] > 70):
        statuses.append(
            {
                "key": "geometry.v_angle",
                "label": "V-tail angle",
                "level": "warn",
                "message": f"A V-tail angle of {_fmt(t['v_angle_deg'])} degrees gives very unequal "
                "pitch and yaw authority; 30-45 degrees is usual.",
            }
        )

    return {
        "layout": layout,
        "wing": wing,
        "tail": tail,
        "fuselage": fuselage,
        "booms": booms,
        "rotors": rotors,
        "lift_rotor_centre_x_mm": (xf + xr) / 2,
        "front_rotor_x_mm": xf,
        "rear_rotor_x_mm": xr,
        "tilt_hinge_x_mm": tilt_hinge_x,
        "landing_gear_bottom_z_mm": gear_bottom,
        "statuses": statuses,
        "parameters": p,
    }


def chord_at_y(g: dict[str, Any], y_mm: float) -> float:
    """Local wing chord (mm) at spanwise station y (mm, either side)."""
    w = g["wing"]
    semi = w["span_mm"] / 2
    return (
        w["root_chord_mm"] - (w["root_chord_mm"] - w["tip_chord_mm"]) * min(abs(y_mm), semi) / semi
    )
