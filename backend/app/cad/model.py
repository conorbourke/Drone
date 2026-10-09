"""Parametric aircraft model for CAD: everything the solids need, computed in plain Python.

The model is derived from the design parameters through the Phase 3 geometry module
(``app.engine.geometry.build_geometry``) so the CAD, the analysis and the browser views agree.
Coordinates (docs/phases/PHASE2.md section 1): origin at the nose tip on the centreline, x aft,
y to starboard, z up, millimetres.

Construction choices (stated, and repeated in the printing notes):

* Wing panels are lofted through airfoil sections lying in planes ``y = const`` (the planform is
  the geometry module's trapezoid, sheared by sweep and dihedral exactly as ``build_geometry``
  places the tip), with incidence and linear twist about the quarter chord.
* Each wing panel starts at the fuselage side (``y = fuselage.width / 2``) and plugs into the
  fuselage: its spar tube passes through the fuselage wall into the wing-to-fuselage clamp. The
  part of the trapezoid inside the fuselage is carried by the fuselage (no wing skin there).
* The spar tube sits at 25 % chord on the local mid-thickness line, channel diameter = tube outer
  diameter + 0.3 mm clearance.
* The trailing edge is thickened linearly to at least 0.8 mm so it can be printed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import cache
from typing import Any

import numpy as np

from app.engine import airfoils as airfoil_lib
from app.engine.geometry import build_geometry, with_defaults

# ---------------------------------------------------------------------------
# Constants (stated in the notes and the manifest)
# ---------------------------------------------------------------------------

SPAR_CLEARANCE_MM = 0.3
KEY_CLEARANCE_MM = 0.2
SHELL_WALL_MM = 0.8  # two 0.4 mm perimeters of lightweight foaming PLA
ROOT_EXCLUSION_FRACTION = 0.15  # of the semi-span (panel length), measured from the root
TE_MIN_THICKNESS_MM = 0.8
AIRFOIL_POINTS_PER_SIDE = 41
SPAR_CHORD_FRACTION = 0.25
SPAR_SKIN_MIN_MM = 1.2  # material left above and below the spar channel
FIN_SPAR_CHORD_FRACTION = 0.45  # vertical members: clear of the horizontal spar at 25 %
HINGE_CHORD_FRACTION = 0.75
INCIDENCE_PIN_CHORD_FRACTION = 0.62
STANDARD_ROD_MM = [3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0]
TUBE_STOCK_LENGTH_MM = 1000.0  # common retail length of roll-wrapped carbon tube
SPLICE_SLEEVE_MM = 80.0
TAIL_MOUNT_TOP_MM = 18.0  # fin/pylon root above the tube it is clamped to

#: Filaments: density as printed (g/cm^3), price (EUR/kg, estimate), volumetric print rate of
#: CAD volume (mm^3/s, estimate including slow outer walls), display colour.
FILAMENTS: dict[str, dict[str, Any]] = {
    "LW-PLA": {
        "density": 0.70,
        "price_eur_per_kg": 40.0,
        "rate_mm3_s": 4.5,
        "color": "#E9E4D4",
        "name": "Lightweight foaming PLA (LW-PLA, e.g. colorFabb LW-PLA)",
    },
    "PETG": {
        "density": 1.27,
        "price_eur_per_kg": 22.0,
        "rate_mm3_s": 8.0,
        "color": "#2F6FBF",
        "name": "PETG",
    },
    "PA-CF": {
        "density": 1.17,
        "price_eur_per_kg": 60.0,
        "rate_mm3_s": 6.0,
        "color": "#3A3A3A",
        "name": "Carbon-fibre filled nylon (PA-CF / PA6-CF)",
    },
    "ASA": {
        "density": 1.07,
        "price_eur_per_kg": 25.0,
        "rate_mm3_s": 8.0,
        "color": "#C8462E",
        "name": "ASA",
    },
}

#: Print profiles: walls (perimeters), line width, infill fraction, layer height, nozzle.
PRINT_PROFILES: dict[str, dict[str, Any]] = {
    "lw_surface": {
        "filament": "LW-PLA",
        "nozzle_mm": 0.4,
        "layer_mm": 0.24,
        "walls": 2,
        "line_mm": 0.4,
        "infill": 0.06,
        "infill_pattern": "gyroid",
        "top_bottom_layers": 3,
    },
    "lw_shell": {
        "filament": "LW-PLA",
        "nozzle_mm": 0.4,
        "layer_mm": 0.2,
        "walls": 2,
        "line_mm": 0.4,
        "infill": 0.0,
        "infill_pattern": "none (the CAD wall is the shell)",
        "top_bottom_layers": 3,
    },
    "petg_mount": {
        "filament": "PETG",
        "nozzle_mm": 0.4,
        "layer_mm": 0.2,
        "walls": 4,
        "line_mm": 0.45,
        "infill": 0.4,
        "infill_pattern": "gyroid",
        "top_bottom_layers": 5,
    },
    "petg_light": {
        "filament": "PETG",
        "nozzle_mm": 0.4,
        "layer_mm": 0.2,
        "walls": 3,
        "line_mm": 0.45,
        "infill": 0.2,
        "infill_pattern": "gyroid",
        "top_bottom_layers": 4,
    },
    "pacf_mount": {
        "filament": "PA-CF",
        "nozzle_mm": 0.4,
        "layer_mm": 0.2,
        "walls": 4,
        "line_mm": 0.45,
        "infill": 0.5,
        "infill_pattern": "gyroid",
        "top_bottom_layers": 5,
    },
    "asa_mount": {
        "filament": "ASA",
        "nozzle_mm": 0.4,
        "layer_mm": 0.2,
        "walls": 4,
        "line_mm": 0.45,
        "infill": 0.5,
        "infill_pattern": "gyroid",
        "top_bottom_layers": 5,
    },
}

#: Generic servo cases when no Phase 4 servo is selected: length x width x height (mm), mass.
GENERIC_SERVOS: dict[str, dict[str, Any]] = {
    "micro": {"length_mm": 23.0, "width_mm": 12.2, "height_mm": 29.0, "mass_g": 9.0},
    "mini": {"length_mm": 28.0, "width_mm": 13.5, "height_mm": 30.0, "mass_g": 20.0},
    "standard": {"length_mm": 40.5, "width_mm": 20.0, "height_mm": 38.0, "mass_g": 45.0},
}


class CadError(RuntimeError):
    """A design the CAD kernel cannot turn into printable parts; the message is plain language."""


class EnvelopeError(CadError):
    """A printed piece does not fit the printer envelope in its chosen orientation."""


# ---------------------------------------------------------------------------
# Airfoil profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Profile:
    """Unit-chord section resampled at cosine spacing: x from leading to trailing edge."""

    airfoil: str
    x: np.ndarray
    yu: np.ndarray
    yl: np.ndarray

    def upper(self, xc: float | np.ndarray) -> Any:
        return np.interp(xc, self.x, self.yu)

    def lower(self, xc: float | np.ndarray) -> Any:
        return np.interp(xc, self.x, self.yl)

    def thickness(self, xc: float | np.ndarray) -> Any:
        return self.upper(xc) - self.lower(xc)

    def mid(self, xc: float | np.ndarray) -> Any:
        return 0.5 * (self.upper(xc) + self.lower(xc))

    def with_te_thickness(self, te_frac: float) -> Profile:
        """Thicken linearly along the chord so the trailing edge is at least ``te_frac``."""
        now = float(self.yu[-1] - self.yl[-1])
        d = max(0.0, te_frac - now)
        if d <= 0:
            return self
        return Profile(self.airfoil, self.x, self.yu + self.x * d / 2, self.yl - self.x * d / 2)

    def loop(self) -> np.ndarray:
        """Closed outline (N, 2): upper surface TE -> LE, then lower LE -> TE (open at the TE)."""
        up = np.column_stack([self.x[::-1], self.yu[::-1]])
        lo = np.column_stack([self.x[1:], self.yl[1:]])
        return np.vstack([up, lo])


@cache
def load_profile(airfoil_id: str) -> Profile:
    key = airfoil_id.lower()
    try:
        pts = list(airfoil_lib.coordinates(key))
    except KeyError:
        code = key[4:] if key.startswith("naca") else ""
        try:
            pts = airfoil_lib.naca4(code)
        except ValueError:
            pts = list(airfoil_lib.coordinates("naca0012"))
            key = "naca0012"
    arr = np.asarray(pts, dtype=float)
    i_le = int(np.argmin(arr[:, 0]))
    upper = arr[: i_le + 1][::-1]
    lower = arr[i_le:]
    x0 = float(arr[i_le, 0])
    span = max(float(arr[:, 0].max()) - x0, 1e-9)
    n = AIRFOIL_POINTS_PER_SIDE
    xs = 0.5 * (1 - np.cos(np.pi * np.arange(n) / (n - 1)))

    def resample(side: np.ndarray) -> np.ndarray:
        sx = (side[:, 0] - x0) / span
        order = np.argsort(sx, kind="stable")
        sx, sy = sx[order], side[order, 1] / span
        sx, idx = np.unique(sx, return_index=True)
        return np.interp(xs, sx, sy[idx])

    yu = resample(upper)
    yl = resample(lower)
    y_le = 0.5 * (yu[0] + yl[0])
    yu[0] = yl[0] = y_le
    return Profile(key, xs, yu, yl)


# ---------------------------------------------------------------------------
# Lifting surfaces
# ---------------------------------------------------------------------------


def _unit(v: Any) -> np.ndarray:
    a = np.asarray(v, dtype=float)
    return a / np.linalg.norm(a)


@dataclass
class Surface:
    """A lifting surface between span coordinates ``s0`` and ``s1``.

    Section planes have normal ``span_axis``; the unrotated leading edge at span coordinate s is
    ``origin + s * le_slope``; chord and incidence vary linearly between (``s_a``, ``s_b``).
    Incidence rotates the section about the quarter chord, positive nose up (about
    ``nhat x xhat``).
    """

    key: str
    label: str
    profile: Profile
    origin: np.ndarray
    le_slope: np.ndarray
    span_axis: np.ndarray
    xhat: np.ndarray
    nhat: np.ndarray
    s0: float
    s1: float
    s_a: float
    s_b: float
    chord_a: float
    chord_b: float
    angle_a_deg: float = 0.0
    angle_b_deg: float = 0.0
    spar_od_mm: float = 0.0
    spar_xc: float = SPAR_CHORD_FRACTION
    spar_s_start: float = 0.0
    spar_s_end: float = 0.0
    spar_open_root: bool = True
    root_exclusion_mm: float = 0.0
    root_s: float | None = None  # span coordinate of the structural root (default s0)
    keepouts: list[tuple[float, float, str]] = field(default_factory=list)
    stations: list[float] = field(default_factory=list)  # extra loft stations
    hinge: tuple[float, float] | None = None  # aileron / control-surface hinge ends (s)
    incidence_pin: dict[str, float] | None = None
    mirror_y: bool = False  # the solid is mirrored (left wing)

    @property
    def length(self) -> float:
        return self.s1 - self.s0

    def _lerp(self, s: float, a: float, b: float) -> float:
        if self.s_b == self.s_a:
            return a
        return a + (b - a) * (s - self.s_a) / (self.s_b - self.s_a)

    def chord(self, s: float) -> float:
        return self._lerp(s, self.chord_a, self.chord_b)

    def angle_deg(self, s: float) -> float:
        return self._lerp(s, self.angle_a_deg, self.angle_b_deg)

    def le_unrotated(self, s: float) -> np.ndarray:
        return self.origin + s * self.le_slope

    def axes(self, s: float) -> tuple[np.ndarray, np.ndarray]:
        th = math.radians(self.angle_deg(s))
        x2 = math.cos(th) * self.xhat - math.sin(th) * self.nhat
        n2 = math.sin(th) * self.xhat + math.cos(th) * self.nhat
        return x2, n2

    def quarter_chord(self, s: float) -> np.ndarray:
        return self.le_unrotated(s) + 0.25 * self.chord(s) * self.xhat

    def point(self, s: float, xc: float | np.ndarray, yc: float | np.ndarray) -> np.ndarray:
        c = self.chord(s)
        x2, n2 = self.axes(s)
        qc = self.quarter_chord(s)
        xc_a = np.atleast_1d(np.asarray(xc, dtype=float))
        yc_a = np.atleast_1d(np.asarray(yc, dtype=float))
        pts = qc + np.outer((xc_a - 0.25) * c, x2) + np.outer(yc_a * c, n2)
        return self._mirror(pts)

    def _mirror(self, pts: np.ndarray) -> np.ndarray:
        if self.mirror_y:
            pts = pts.copy()
            pts[..., 1] *= -1
        return pts

    def te_profile(self) -> Profile:
        cmin = min(self.chord(self.s0), self.chord(self.s1))
        return self.profile.with_te_thickness(TE_MIN_THICKNESS_MM / max(cmin, 1.0))

    def section(self, s: float) -> np.ndarray:
        loop = self.te_profile().loop()
        return self.point(s, loop[:, 0], loop[:, 1])

    def mid_point(self, s: float, xc: float) -> np.ndarray:
        pr = self.te_profile()
        return self.point(s, xc, float(pr.mid(xc)))[0]

    def upper_point(self, s: float, xc: float) -> np.ndarray:
        return self.point(s, xc, float(self.te_profile().upper(xc)))[0]

    def thickness_mm(self, s: float, xc: float) -> float:
        return float(self.te_profile().thickness(xc)) * self.chord(s)

    def axis_world(self) -> np.ndarray:
        a = self.span_axis.copy()
        if self.mirror_y:
            a[1] *= -1
        return a

    def spar_line(self) -> tuple[np.ndarray, np.ndarray] | None:
        if self.spar_od_mm <= 0 or self.spar_s_end <= self.spar_s_start:
            return None
        return (
            self.mid_point(self.spar_s_start, self.spar_xc),
            self.mid_point(self.spar_s_end, self.spar_xc),
        )

    def spar_point_at(self, s: float) -> np.ndarray:
        line = self.spar_line()
        if line is None:
            return self.mid_point(s, self.spar_xc)
        a, b = line
        t = (s - self.spar_s_start) / (self.spar_s_end - self.spar_s_start)
        return a + (b - a) * t

    def max_spar_od(self, s: float, xc: float = SPAR_CHORD_FRACTION) -> float:
        return self.thickness_mm(s, xc) - SPAR_CLEARANCE_MM - 2 * SPAR_SKIN_MIN_MM


# ---------------------------------------------------------------------------
# Fuselage profile (identical to the geometry module's stations)
# ---------------------------------------------------------------------------


@dataclass
class FuselageModel:
    length: float
    width: float
    height: float
    cross_section: str
    nose_length: float
    tail_length: float
    tail_end_scale: float
    nose_bay_length: float
    nose_bay_width: float
    nose_bay_height: float
    wall: float = SHELL_WALL_MM

    def scale(self, x: float) -> float:
        if x <= 0:
            return 0.0
        if x < self.nose_length:
            u = 1 - x / self.nose_length
            return math.sqrt(max(0.0, 1 - u * u))
        x_tail = self.length - self.tail_length
        if x <= x_tail:
            return 1.0
        t = min(1.0, (x - x_tail) / max(self.tail_length, 1e-9))
        return 1.0 + (self.tail_end_scale - 1.0) * t

    def half_size(self, x: float, inset: float = 0.0) -> tuple[float, float]:
        s = self.scale(x)
        return max(0.0, self.width * s / 2 - inset), max(0.0, self.height * s / 2 - inset)

    def section_points(self, x: float, inset: float = 0.0, n: int = 72) -> np.ndarray:
        a, b = self.half_size(x, inset)
        return section_outline(self.cross_section, a, b, n, x)

    def corner_radius(self, a: float, b: float) -> float:
        return 0.25 * min(2 * a, 2 * b) if self.cross_section == "rounded_rect" else 0.0


def section_outline(kind: str, a: float, b: float, n: int, x: float) -> np.ndarray:
    """Closed outline (y, z) of half-width a and half-height b, returned as 3D points at x."""
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    if kind == "ellipse":
        y, z = a * np.cos(t), b * np.sin(t)
    else:
        r = 0.25 * min(2 * a, 2 * b)
        # superellipse-free exact rounded rectangle sampled by angle around the centre
        y = np.empty_like(t)
        z = np.empty_like(t)
        for i, ang in enumerate(t):
            dy, dz = math.cos(ang), math.sin(ang)
            # ray-box intersection, then pull into the rounded corner when needed
            k = min(a / max(abs(dy), 1e-12), b / max(abs(dz), 1e-12))
            py, pz = dy * k, dz * k
            cy, cz = a - r, b - r
            if abs(py) > cy and abs(pz) > cz:
                sy, sz = math.copysign(1, py), math.copysign(1, pz)
                # intersect ray with the corner circle centred at (sy*cy, sz*cz)
                ox, oz = sy * cy, sz * cz
                bq = -2 * (dy * ox + dz * oz)
                cq_ = ox * ox + oz * oz - r * r
                disc = max(0.0, bq * bq - 4 * cq_)
                k = (-bq + math.sqrt(disc)) / 2
                py, pz = dy * k, dz * k
            y[i], z[i] = py, pz
    return np.column_stack([np.full_like(y, x), y, z])


# ---------------------------------------------------------------------------
# Components (generic until Phase 4 parts are selected)
# ---------------------------------------------------------------------------


def parse_mount_pattern(text: str) -> dict[str, Any]:
    """'25x25 M3', '16x19 M3', 'Ø30 M3' -> cross pattern spacings and screw size."""
    t = text.replace("\u00f8", "").replace("\u00d8", "").replace("\u00d7", "x").upper()
    screw = 3.0
    for tok in t.split():
        if tok.startswith("M") and tok[1:].replace(".", "").isdigit():
            screw = float(tok[1:])
    nums = []
    for tok in t.replace("X", " ").replace("MM", " ").split():
        try:
            nums.append(float(tok))
        except ValueError:
            continue
    nums = [v for v in nums if v > 6]
    if not nums:
        nums = [25.0, 25.0]
    if len(nums) == 1:
        nums = [nums[0], nums[0]]
    return {"a_mm": nums[0], "b_mm": nums[1], "screw": f"M{screw:g}", "screw_mm": screw}


def generic_motor(mass_g: float) -> dict[str, Any]:
    """Generic outrunner envelope from the engine's statistical motor mass (estimate)."""
    if mass_g < 70:
        pattern = "16x19 M3"
    elif mass_g < 250:
        pattern = "25x25 M3"
    elif mass_g < 600:
        pattern = "30x30 M3"
    else:
        pattern = "40x40 M4"
    d = min(100.0, max(24.0, 20.0 + 2.05 * math.sqrt(max(mass_g, 1.0))))
    h = 0.7 * d
    return {
        "mass_g": mass_g,
        "diameter_mm": round(d, 1),
        "height_mm": round(h, 1),
        "mount_pattern": pattern,
        "generic": True,
    }


def generic_servo(mass_g: float) -> dict[str, Any]:
    cls = "micro" if mass_g < 14 else "mini" if mass_g < 32 else "standard"
    return {**GENERIC_SERVOS[cls], "class": cls, "generic": True}


def _selection_by_role(parts_selection: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in parts_selection or []:
        if isinstance(item, dict) and item.get("role"):
            out[str(item["role"])] = item
    # Phase 4 parts-list role names (app.engine.selection.ROLE_ORDER) for the same components.
    for phase4, cad in (("cruise_motor", "pusher_motor"), ("spar_tube", "wing_spar")):
        if phase4 in out and cad not in out:
            out[cad] = out[phase4]
    return out


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


@dataclass
class CadModel:
    params: dict[str, Any]
    geometry: dict[str, Any]
    mission: dict[str, Any]
    settings: dict[str, Any]
    envelope: tuple[float, float, float]
    bed: tuple[float, float]
    surfaces: dict[str, Surface]
    fuselage: FuselageModel
    spar: dict[str, Any]
    tail_spar_od: float
    boom: dict[str, Any]
    tail_boom: dict[str, Any] | None
    lift_motor: dict[str, Any]
    pusher_motor: dict[str, Any] | None
    tilt_servo: dict[str, Any] | None
    control_servo: dict[str, Any]
    battery_box: dict[str, Any]
    payload_box: dict[str, Any]
    mass: dict[str, Any]
    balance: dict[str, Any]
    selection: dict[str, dict[str, Any]]
    tail_layout: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    @property
    def layout(self) -> str:
        return self.params["layout"]

    @property
    def wing_root_y(self) -> float:
        return self.fuselage.width / 2

    @property
    def spar_x(self) -> float:
        return float(self.surfaces["wing_right"].spar_point_at(self.wing_root_y)[0])


def _resolve_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    from app.engine.analysis import resolve_settings

    return resolve_settings(settings)


def _quantity_value(q: Any) -> float | None:
    if isinstance(q, dict):
        v = q.get("value")
        return float(v) if isinstance(v, int | float) and math.isfinite(v) else None
    if isinstance(q, int | float) and math.isfinite(q):
        return float(q)
    return None


def _spar_tube(
    p: dict[str, Any],
    g: dict[str, Any],
    mission: dict[str, Any],
    settings: dict[str, Any],
    ms: dict[str, Any],
    analysis: dict[str, Any] | None,
    sel: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    from app.engine import structure as stc

    item = sel.get("wing_spar")
    if item:
        spec = item.get("spec") or {}
        od = spec.get("outer_mm") or spec.get("outer_diameter_mm")
        if od:
            inner = spec.get("inner_diameter_mm")
            wall = spec.get("wall_mm") or ((float(od) - float(inner)) / 2 if inner else 1.0)
            return {"outer_mm": float(od), "wall_mm": float(wall), "source": "Phase 4 selection"}
    struct = (analysis or {}).get("structure") or {}
    sizing = struct.get("spar_sizing") or {}
    if sizing.get("outer_mm"):
        return {
            "outer_mm": float(sizing["outer_mm"]),
            "wall_mm": float(sizing.get("wall_mm") or 1.0),
            "source": "Sized by the latest analysis (structure check)",
        }
    detail = (struct.get("wing_spar") or {}).get("spar_detail") or {}
    if detail.get("kind") == "tube" and detail.get("outer_mm"):
        return {
            "outer_mm": float(detail["outer_mm"]),
            "wall_mm": float(detail.get("wall_mm") or 1.0),
            "source": "Spar of the latest analysis",
        }
    weight = ms["total_max_g"] / 1000 * 9.80665
    n = settings["checks"].get("manoeuvre_load_factor", 3.0)
    sf = settings["checks"].get("structural_safety_factor", 1.5)
    s = stc.size_spar_tube(g, stc.schrenk_strips(g), weight, 1.0, n, sf)
    return {
        "outer_mm": float(s["outer_mm"]),
        "wall_mm": float(s["wall_mm"]),
        "source": "Sized here with the structure module (Schrenk loading, "
        f"n = {n:g}, safety factor {sf:g}); no analysis was supplied",
    }


def _rod_at_most(limit: float) -> float:
    best = STANDARD_ROD_MM[0]
    for d in STANDARD_ROD_MM:
        if d <= limit:
            best = d
    return best


def build_model(
    parameters: dict[str, Any],
    mission: dict[str, Any],
    settings: dict[str, Any] | None,
    *,
    analysis: dict[str, Any] | None = None,
    parts_selection: Any = None,
) -> CadModel:
    from app.engine.mass import solve_mass

    p = with_defaults(parameters)
    g = build_geometry(p)
    s_all = _resolve_settings(settings)
    env = s_all["printer"]["usable_envelope_mm"]
    envelope = (float(env["x"]), float(env["y"]), float(env["z"]))
    bv = s_all["printer"]["build_volume_mm"]
    bed = (float(bv["x"]), float(bv["y"]))
    sel = _selection_by_role(parts_selection)
    warnings: list[str] = []
    if mission.get("scale") == "final":
        warnings.append(
            "The mission is set to the final (carbon) scale; these print files describe the "
            "3D-printed construction of the same geometry (moulds come in a later phase)."
        )

    ms = solve_mass(p, g, mission, s_all)
    spar = _spar_tube(p, g, mission, s_all, ms, analysis, sel)

    w = p["wing"]
    semi = w["span_mm"] / 2
    f = p["fuselage"]
    gf = g["fuselage"]
    fus = FuselageModel(
        length=f["length_mm"],
        width=f["width_mm"],
        height=f["height_mm"],
        cross_section=f["cross_section"],
        nose_length=gf["nose_length_mm"],
        tail_length=gf["tail_length_mm"],
        tail_end_scale=0.3,
        nose_bay_length=p["nose_bay"]["length_mm"],
        nose_bay_width=p["nose_bay"]["width_mm"],
        nose_bay_height=p["nose_bay"]["height_mm"],
    )
    root_y = fus.width / 2
    if root_y >= semi - 50:
        raise CadError(
            "The fuselage is almost as wide as the wingspan, so there is no wing panel to print. "
            "Increase the span or reduce the fuselage width."
        )
    sweep = math.tan(math.radians(w["sweep_deg"]))
    dih = math.tan(math.radians(w["dihedral_deg"]))
    wing_profile = load_profile(w["airfoil"])
    wing = Surface(
        key="wing_right",
        label="Wing R",
        profile=wing_profile,
        origin=np.array([w["x_le_mm"], 0.0, w["z_mm"]]),
        le_slope=np.array([sweep, 1.0, dih]),
        span_axis=np.array([0.0, 1.0, 0.0]),
        xhat=np.array([1.0, 0.0, 0.0]),
        nhat=np.array([0.0, 0.0, 1.0]),
        s0=root_y,
        s1=semi,
        s_a=0.0,
        s_b=semi,
        chord_a=w["root_chord_mm"],
        chord_b=w["tip_chord_mm"],
        angle_a_deg=w["incidence_deg"],
        angle_b_deg=w["incidence_deg"] + w["twist_deg"],
    )

    # ----- Spar: 25 % chord, as far out as the section is deep enough -----
    od = spar["outer_mm"]
    if wing.max_spar_od(root_y) < od:
        fit = wing.max_spar_od(root_y)
        raise CadError(
            f"The {od:g} mm spar tube does not fit in the wing root: the section is only deep "
            f"enough for {max(fit, 0):.1f} mm at 25 % chord (with {SPAR_CLEARANCE_MM} mm "
            f"clearance and {SPAR_SKIN_MIN_MM} mm of skin). Choose a thicker airfoil, a larger "
            "root chord or a thinner tube."
        )
    s_end = root_y
    for y in np.linspace(root_y, semi - 15.0, 400):
        if wing.max_spar_od(float(y)) >= od:
            s_end = float(y)
        else:
            break
    wing.spar_od_mm = od
    wing.spar_s_start = 0.0  # line defined from the centreline (into the clamp)
    wing.spar_s_end = s_end
    spar["end_y_mm"] = s_end
    spar_dir = wing.spar_line()
    assert spar_dir is not None
    a, b = spar_dir
    spar["axis_start"] = a.tolist()
    spar["axis_end"] = b.tolist()
    # Tube per side: from 5 mm short of the centreline to the channel end.
    p_in = wing.spar_point_at(5.0)
    spar["cut_length_mm"] = float(np.linalg.norm(b - p_in)) - 2.0
    spar["count"] = 2
    if spar["cut_length_mm"] > TUBE_STOCK_LENGTH_MM:
        # Two tubes per side joined by an internal sleeve; the splice is kept 100 mm away from
        # every printed joint (staggered joints: one continuous member always crosses a joint).
        first = TUBE_STOCK_LENGTH_MM - 20.0
        t = first / float(np.linalg.norm(b - p_in))
        y_spl = 5.0 + t * (s_end - 5.0)
        spar["splice_y_mm"] = y_spl
        spar["tube_lengths_mm"] = [first, spar["cut_length_mm"] - first]
        spar["sleeve"] = {
            "od_mm": spar["outer_mm"] - 2 * spar["wall_mm"] - 0.2,
            "length_mm": SPLICE_SLEEVE_MM,
        }
        splice_zone = (y_spl - 100.0, y_spl + 100.0, "spar splice sleeve")
    else:
        splice_zone = None

    # ----- Booms and the boom/spar crossing -----
    yo = p["booms"]["lateral_offset_mm"]
    boom_d = p["booms"]["diameter_mm"]
    boom_z = w["z_mm"]
    if not (root_y + boom_d < yo < semi - 20):
        raise CadError(
            f"The booms ({yo:g} mm from the centreline) are not under the wing panel between the "
            f"fuselage side ({root_y:g} mm) and the tip ({semi:g} mm); move the booms."
        )
    sp = wing.spar_point_at(yo)
    gap_needed = (od + SPAR_CLEARANCE_MM) / 2 + (boom_d + SPAR_CLEARANCE_MM) / 2 + 1.2
    if yo > s_end:
        warnings.append(
            "The spar tube ends inboard of the booms; the boom loads go into the printed wing "
            "only. Use a thinner spar or a thicker airfoil."
        )
    elif abs(sp[2] - boom_z) < gap_needed:
        new_z = float(sp[2] - gap_needed)
        warnings.append(
            f"The boom centreline was lowered by {boom_z - new_z:.1f} mm in the CAD so the boom "
            "passes under the wing spar instead of through it (the analysis keeps the "
            "parameter value)."
        )
        boom_z = new_z
    boom_len = p["booms"]["length_mm"]
    tg = g["tail"]
    if p["tail"]["type"] == "twin_boom_h":
        need = tg["te_x_mm"] + 15 - g["booms"][0]["start"][0]
        if need > boom_len:
            boom_len = need
    boom = {
        "diameter_mm": boom_d,
        "z_mm": boom_z,
        "y_mm": yo,
        "x0_mm": g["booms"][0]["start"][0],
        "length_mm": boom_len,
        "wall_mm": max(1.0, 0.05 * boom_d),
    }
    # Boom-to-wing clamp zone (keep joints away) and ailerons.
    cuff_w = boom_d + 12.0
    boom_zone = (yo - cuff_w / 2 - 12.0, yo + cuff_w / 2 + 12.0, "boom-to-wing clamp")
    a0 = max(0.5 * semi, boom_zone[1] + 15.0)
    a1 = 0.95 * semi
    wing.keepouts = [boom_zone] + ([splice_zone] if splice_zone else [])
    if a1 - a0 > 60:
        wing.hinge = (a0, a1)
        wing.keepouts += [
            (a0 - 12.0, a0 + 12.0, "aileron hinge end"),
            (a1 - 12.0, a1 + 12.0, "aileron hinge end"),
        ]
    if s_end < semi - 20:
        wing.keepouts.append((s_end - 25.0, s_end + 25.0, "end of the spar tube"))
    wing.root_exclusion_mm = ROOT_EXCLUSION_FRACTION * semi
    wing.stations = sorted({yo - cuff_w / 2, yo + cuff_w / 2, s_end, *(wing.hinge or ())})
    t_pin = wing.thickness_mm(root_y, INCIDENCE_PIN_CHORD_FRACTION)
    pin_d = _rod_at_most(0.45 * t_pin)
    wing.incidence_pin = {"xc": INCIDENCE_PIN_CHORD_FRACTION, "diameter_mm": pin_d, "depth": 35.0}

    left = Surface(**{**wing.__dict__, "key": "wing_left", "label": "Wing L", "mirror_y": True})
    left.keepouts = list(wing.keepouts)
    left.stations = list(wing.stations)

    # ----- Tail -----
    t = p["tail"]
    tail_profile = load_profile(t["airfoil"])
    chord_t = t["chord_mm"]
    t_le = tg["le_x_mm"]
    t_z = tg["z_mm"]
    tail_thick = float(tail_profile.thickness(SPAR_CHORD_FRACTION)) * chord_t
    tail_spar = _rod_at_most(
        min(0.6 * tail_thick, tail_thick - SPAR_CLEARANCE_MM - 2 * SPAR_SKIN_MIN_MM)
    )
    surfaces: dict[str, Surface] = {"wing_right": wing, "wing_left": left}
    ttype = t["type"]
    gamma = math.radians(t["v_angle_deg"])
    x_unit = np.array([1.0, 0.0, 0.0])
    tail_layout: dict[str, Any] = {"type": ttype}
    tail_boom = None
    support = tg["support_length_mm"]
    if ttype != "twin_boom_h" and support > 0:
        x_end = tg["te_x_mm"] - 0.15 * chord_t
        tb_od = 12.0 if w["span_mm"] < 2500 else 16.0
        x0 = fus.length - 70.0
        if x_end > fus.length + 5:
            tail_boom = {
                "diameter_mm": tb_od,
                "x0_mm": x0,
                "x1_mm": x_end,
                "z_mm": 0.0,
                "length_mm": x_end - x0,
            }

    if tail_boom is not None and p["layout"] == "quad_pusher":
        warnings.append(
            "The pusher propeller at the fuselage tail and the centre tail boom occupy the same "
            "place; choose the twin-boom H-tail for the quad + pusher layout or move the pusher."
        )

    def base_z_at(x: float) -> float:
        if tail_boom is not None and x >= fus.length - 1:
            return tail_boom["diameter_mm"] / 2 + TAIL_MOUNT_TOP_MM
        return fus.half_size(min(x, fus.length))[1]

    def fin_like(key: str, label: str, y: float, z0: float, z1: float) -> Surface:
        return Surface(
            key=key,
            label=label,
            profile=tail_profile,
            origin=np.array([t_le, y, 0.0]),
            le_slope=np.array([0.0, 0.0, 1.0]),
            span_axis=np.array([0.0, 0.0, 1.0]),
            xhat=x_unit,
            nhat=np.array([0.0, -1.0, 0.0]),
            s0=z0,
            s1=z1,
            s_a=z0,
            s_b=z1,
            chord_a=chord_t,
            chord_b=chord_t,
            spar_od_mm=tail_spar,
            spar_xc=FIN_SPAR_CHORD_FRACTION,
            spar_s_start=z0 - (TAIL_MOUNT_TOP_MM - 2.0),
            spar_s_end=z1 - 2.0,
            root_exclusion_mm=ROOT_EXCLUSION_FRACTION * (z1 - z0),
        )

    def hstab(key: str, label: str, half: float, z: float) -> Surface:
        return Surface(
            key=key,
            label=label,
            profile=tail_profile,
            origin=np.array([t_le, 0.0, z]),
            le_slope=np.array([0.0, 1.0, 0.0]),
            span_axis=np.array([0.0, 1.0, 0.0]),
            xhat=x_unit,
            nhat=np.array([0.0, 0.0, 1.0]),
            s0=-half,
            s1=half,
            s_a=-half,
            s_b=half,
            chord_a=chord_t,
            chord_b=chord_t,
            spar_od_mm=tail_spar,
            spar_s_start=-half + 8.0,
            spar_s_end=half - 8.0,
            spar_open_root=False,
        )

    x_fin_spar = t_le + FIN_SPAR_CHORD_FRACTION * chord_t
    if ttype == "conventional":
        z0 = base_z_at(x_fin_spar)
        surfaces["tail_hstab"] = hstab("tail_hstab", "H-stab", t["span_mm"] / 2, t_z)
        surfaces["tail_hstab"].keepouts = [(-20.0, 20.0, "fin attachment")]
        surfaces["tail_hstab"].root_exclusion_mm = ROOT_EXCLUSION_FRACTION * t["span_mm"] / 2
        surfaces["tail_hstab"].root_s = 0.0
        surfaces["tail_hstab"].stations = [0.0]
        if t_z - z0 < 30:
            raise CadError(
                "The conventional tail needs a fin: set the tail height (fin height) to at least "
                f"{z0 + 30:.0f} mm."
            )
        surfaces["tail_fin"] = fin_like("tail_fin", "Fin", 0.0, z0, t_z)
        tail_layout["vertical"] = ["tail_fin"]
        tail_layout["sockets"] = [{"surface": "tail_hstab", "x": x_fin_spar, "y": 0.0}]
    elif ttype in ("v_tail", "inverted_v"):
        sgn = 1.0 if ttype == "v_tail" else -1.0
        mount_half = 14.0
        s_root = mount_half / math.cos(gamma)
        length = (t["span_mm"] / 2) / math.cos(gamma)
        s_axis = np.array([0.0, math.cos(gamma), sgn * math.sin(gamma)])
        n_axis = np.array([0.0, -sgn * math.sin(gamma), math.cos(gamma)])
        for side, mirror in (("right", False), ("left", True)):
            surf = Surface(
                key=f"tail_{side}",
                label=f"V-tail {'R' if side == 'right' else 'L'}",
                profile=tail_profile,
                origin=np.array([t_le, 0.0, t_z]),
                le_slope=s_axis,
                span_axis=s_axis,
                xhat=x_unit,
                nhat=n_axis,
                s0=s_root,
                s1=length,
                s_a=0.0,
                s_b=length,
                chord_a=chord_t,
                chord_b=chord_t,
                spar_od_mm=tail_spar,
                spar_s_start=2.0,
                spar_s_end=length - 10.0,
                root_exclusion_mm=ROOT_EXCLUSION_FRACTION * length,
                mirror_y=mirror,
            )
            surfaces[surf.key] = surf
        tail_layout["v"] = {"gamma_deg": t["v_angle_deg"], "sign": sgn, "s_root": s_root}
        z0 = base_z_at(x_fin_spar)
        apex_bottom = t_z - (tail_thick / 2 + 4)
        if apex_bottom - z0 > 25:
            pylon = fin_like("tail_pylon", "Tail pylon", 0.0, z0, t_z)
            apex_low = min(t_z, t_z + sgn * s_root * math.sin(gamma)) - 0.5 * tail_thick
            pylon.spar_s_end = apex_low + 8.0  # into the apex socket
            surfaces["tail_pylon"] = pylon
            tail_layout["vertical"] = ["tail_pylon"]
        else:
            tail_layout["vertical"] = []
    else:  # twin_boom_h
        half = max(t["span_mm"] / 2, yo)
        if t["span_mm"] / 2 < yo:
            warnings.append(
                f"The H-tail span ({t['span_mm']:g} mm) is narrower than the boom spacing "
                f"({2 * yo:g} mm); the CAD stabiliser reaches the booms ({2 * half:g} mm), so its "
                "area is larger than in the analysis."
            )
        surfaces["tail_hstab"] = hstab("tail_hstab", "H-stab", half, t_z)
        surfaces["tail_hstab"].keepouts = [
            (yo - 22.0, yo + 22.0, "fin attachment"),
            (-yo - 22.0, -yo + 22.0, "fin attachment"),
        ]
        z0 = boom_z + boom_d / 2 + TAIL_MOUNT_TOP_MM
        if t_z - z0 < 30:
            raise CadError(
                "The twin-boom tail needs fins between the booms and the stabiliser: set the "
                f"tail height to at least {z0 + 30 - w['z_mm']:.0f} mm."
            )
        for side, y in (("right", yo), ("left", -yo)):
            surfaces[f"tail_fin_{side}"] = fin_like(
                f"tail_fin_{side}", f"Fin {'R' if side == 'right' else 'L'}", y, z0, t_z
            )
        tail_layout["vertical"] = ["tail_fin_right", "tail_fin_left"]
        tail_layout["sockets"] = [
            {"surface": "tail_hstab", "x": x_fin_spar, "y": yo},
            {"surface": "tail_hstab", "x": x_fin_spar, "y": -yo},
        ]

    # ----- Components -----
    def sel_spec(role: str) -> dict[str, Any] | None:
        item = sel.get(role)
        return (item or {}).get("spec") if item else None

    motor_sel = sel.get("lift_motor")
    motor = generic_motor(ms["lift_motor_mass_g"])
    if motor_sel:
        spec = motor_sel.get("spec") or {}
        motor = {
            **motor,
            "mass_g": float(motor_sel.get("mass_g") or motor["mass_g"]),
            "mount_pattern": spec.get("mount_pattern") or motor["mount_pattern"],
            "generic": False,
        }
        size = str(spec.get("stator_size") or "")
        if len(size) == 4 and size.isdigit():
            motor["diameter_mm"] = float(size[:2]) + 7.0
            motor["height_mm"] = float(size[2:]) + 16.0
    motor["pattern"] = parse_mount_pattern(motor["mount_pattern"])
    pusher = None
    if p["layout"] == "quad_pusher":
        pusher = generic_motor(max(ms["pusher_motor_mass_g"], 30.0))
        ps = sel.get("pusher_motor")
        if ps and (ps.get("spec") or {}).get("mount_pattern"):
            pusher["mount_pattern"] = ps["spec"]["mount_pattern"]
            pusher["generic"] = False
        pusher["pattern"] = parse_mount_pattern(pusher["mount_pattern"])
    comps = {c["key"]: c for c in ms["result"]["components"]}
    tilt_servo = None
    if p["layout"] != "quad_pusher":
        tm = comps.get("tilt_mechanism", {}).get("mass_g", 60.0)
        tilt_servo = generic_servo(0.5 * tm / 2)
        spec = sel_spec("tilt_servo")
        if spec and spec.get("length_mm"):
            tilt_servo = {
                "length_mm": float(spec["length_mm"]),
                "width_mm": float(spec["width_mm"]),
                "height_mm": float(spec["height_mm"]),
                "mass_g": float(sel["tilt_servo"].get("mass_g") or 0),
                "class": "selected",
                "generic": False,
            }
    cs_mass = comps.get("servos_wing", {}).get("mass_g", 20.0) / 2
    control_servo = generic_servo(cs_mass)

    # Battery and payload volumes (assembly only).
    pack_mass = ms["pack"]["mass_g"]
    bspec = sel_spec("battery")
    if bspec and all(bspec.get(k) for k in ("length_mm", "width_mm", "height_mm")):
        bl, bw, bh = float(bspec["length_mm"]), float(bspec["width_mm"]), float(bspec["height_mm"])
    else:
        vol = pack_mass / 2.2 * 1000  # mm^3 at ~2.2 g/cm^3 pack density (estimate)
        k = (vol / (3.3 * 1.0 * 0.95)) ** (1 / 3)
        bl, bw, bh = 3.3 * k, 1.0 * k, 0.95 * k
    battery_box = {
        "length_mm": bl,
        "width_mm": bw,
        "height_mm": bh,
        "center": [p["battery"]["x_mm"], 0.0, -fus.height / 2 + fus.wall + 2 + bh / 2],
        "mass_g": pack_mass,
    }
    a_in, b_in = fus.half_size(p["battery"]["x_mm"], fus.wall)
    if bw / 2 > a_in or bh / 2 > b_in:
        warnings.append(
            f"The battery ({bl:.0f} x {bw:.0f} x {bh:.0f} mm, estimated from its energy) is wider "
            "or taller than the fuselage inside at its position."
        )
    nb = p["nose_bay"]
    payload_box = {
        "length_mm": 0.6 * nb["length_mm"],
        "width_mm": 0.7 * nb["width_mm"],
        "height_mm": 0.6 * nb["height_mm"],
        "center": [0.55 * nb["length_mm"], 0.0, 0.0],
        "mass_g": mission.get("payload_max_g", 0.0),
    }

    # Balance marks for the drawings.
    balance: dict[str, Any] = {
        "cg_x_mm": ms["cg_max_x"],
        "cg_source": "mass model (no analysis supplied)",
        "np_x_mm": None,
    }
    if analysis:
        bal = analysis.get("balance") or {}
        cg = _quantity_value(bal.get("cg_max_payload_x"))
        npx = _quantity_value(bal.get("neutral_point_x"))
        if cg is not None:
            balance["cg_x_mm"] = cg
            balance["cg_source"] = "latest analysis (heaviest payload)"
        if npx is not None:
            balance["np_x_mm"] = npx
        cgmin = _quantity_value(bal.get("cg_min_payload_x"))
        if cgmin is not None:
            balance["cg_min_x_mm"] = cgmin

    return CadModel(
        params=p,
        geometry=g,
        mission=mission,
        settings=s_all,
        envelope=envelope,
        bed=bed,
        surfaces=surfaces,
        fuselage=fus,
        spar=spar,
        tail_spar_od=tail_spar,
        boom=boom,
        tail_boom=tail_boom,
        lift_motor=motor,
        pusher_motor=pusher,
        tilt_servo=tilt_servo,
        control_servo=control_servo,
        battery_box=battery_box,
        payload_box=payload_box,
        mass=ms,
        balance=balance,
        selection=sel,
        tail_layout=tail_layout,
        warnings=warnings + [s["message"] for s in g["statuses"] if s["level"] == "fail"],
    )
