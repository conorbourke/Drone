"""Airfoil library: coordinates, computed section geometry and XFOIL polars.

Coordinates are unit-chord, Selig order (upper surface from the trailing edge to the leading
edge, then the lower surface back to the trailing edge). The UIUC sections are committed
``.dat`` files (see ``data/airfoils/README.md``); the NACA 4-digit sections are generated from
the published equations by :func:`naca4`, and the generated files are committed too so every
section is read the same way.

Polars come from ``data/airfoil_polars.json``, written by ``scripts/build_airfoil_tables.py``
(XFOIL). Nothing here runs XFOIL at request time.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

DATA_DIR = Path(__file__).resolve().parent / "data"
COORDINATES_DIR = DATA_DIR / "airfoils"
POLARS_FILE = DATA_DIR / "airfoil_polars.json"

UIUC_SOURCE = (
    "UIUC Airfoil Coordinates Database (M. Selig, University of Illinois), Selig format, "
    "copied from the AeroSandbox 4.2.10 airfoil database."
)
NACA_SOURCE = (
    "NACA 4-digit section equations (Abbott and von Doenhoff, Theory of Wing Sections, 1959), "
    "generated analytically: 160 panels, cosine spacing, standard open trailing edge."
)

AirfoilUse = Literal["wing", "tail"]


@dataclass(frozen=True)
class AirfoilEntry:
    id: str
    name: str
    description: str
    use: AirfoilUse
    source: str


LIBRARY: tuple[AirfoilEntry, ...] = (
    AirfoilEntry(
        "sd7037",
        "Selig/Donovan SD7037",
        "Thin, gentle stall and low drag at low speed: a proven all-rounder for small UAV and "
        "glider wings. The default wing section.",
        "wing",
        UIUC_SOURCE,
    ),
    AirfoilEntry(
        "sd7062",
        "Selig/Donovan SD7062",
        "Thicker and more cambered than SD7037: more maximum lift and room for a stiffer spar, "
        "at the cost of a little more drag. Suits heavier, slower aircraft.",
        "wing",
        UIUC_SOURCE,
    ),
    AirfoilEntry(
        "e387",
        "Eppler E387",
        "Classic low-Reynolds-number section, measured in detail in wind tunnels. Good lift at "
        "low speed; below about 100 000 Reynolds number a laminar bubble adds drag.",
        "wing",
        UIUC_SOURCE,
    ),
    AirfoilEntry(
        "mh32",
        "Hepperle MH 32",
        "Thin, lightly cambered section with low drag for faster cruise. Lower maximum lift, so "
        "it needs a larger wing or higher stall speed.",
        "wing",
        UIUC_SOURCE,
    ),
    AirfoilEntry(
        "s3021",
        "Selig S3021",
        "Thin sailplane section with low drag over a narrow lift range. Efficient in cruise, "
        "less forgiving near the stall.",
        "wing",
        UIUC_SOURCE,
    ),
    AirfoilEntry(
        "ag35",
        "Drela AG35",
        "Very thin section designed for small hand-launched gliders: very low drag at low "
        "Reynolds numbers, but thin, so the wing structure needs care.",
        "wing",
        UIUC_SOURCE,
    ),
    AirfoilEntry(
        "clarky",
        "Clark Y",
        "Flat-bottomed classic: forgiving, easy to build and to jig on a flat surface. More drag "
        "than modern low-speed sections.",
        "wing",
        UIUC_SOURCE,
    ),
    AirfoilEntry(
        "naca2412",
        "NACA 2412",
        "General-purpose section with mild camber and a docile stall. Designed for larger "
        "aircraft, so less efficient than the low-speed sections on a small drone.",
        "wing",
        NACA_SOURCE,
    ),
    AirfoilEntry(
        "naca4412",
        "NACA 4412",
        "Strongly cambered general-purpose section: high lift at low speed, but a larger "
        "nose-down pitching moment that the tail must balance.",
        "wing",
        NACA_SOURCE,
    ),
    AirfoilEntry(
        "naca0009",
        "NACA 0009",
        "Thin symmetric section, the usual choice for tail surfaces: low drag, works the same "
        "with positive or negative lift. The default tail section.",
        "tail",
        NACA_SOURCE,
    ),
    AirfoilEntry(
        "naca0012",
        "NACA 0012",
        "Thicker symmetric section for tail surfaces: stiffer and easier to build, slightly more "
        "drag than NACA 0009.",
        "tail",
        NACA_SOURCE,
    ),
)

LIBRARY_BY_ID: dict[str, AirfoilEntry] = {entry.id: entry for entry in LIBRARY}
NACA_IDS = frozenset(entry.id for entry in LIBRARY if entry.id.startswith("naca"))

SUMMARY_KEYS = (
    "re",
    "cl_max",
    "alpha_cl_max_deg",
    "alpha_zero_lift_deg",
    "cl_alpha_per_rad",
    "cd_min",
    "cl_at_cd_min",
    "cm0",
)


# ---------------------------------------------------------------------------
# Coordinates
# ---------------------------------------------------------------------------


def naca4(code: str, panels: int = 160) -> list[tuple[float, float]]:
    """NACA 4-digit section in Selig order, unit chord, cosine spacing.

    ``panels`` is the number of panels around the section (``panels + 1`` points, the leading
    edge shared). Standard (open) trailing edge, thickness distribution coefficient -0.1015.
    """
    if len(code) != 4 or not code.isdigit():
        raise ValueError(f"Not a NACA 4-digit code: {code!r}")
    m = int(code[0]) / 100.0
    p = int(code[1]) / 10.0
    t = int(code[2:]) / 100.0
    n_side = panels // 2
    xs = [0.5 * (1.0 - math.cos(math.pi * i / n_side)) for i in range(n_side + 1)]

    def surfaces(x: float) -> tuple[tuple[float, float], tuple[float, float]]:
        yt = (
            5.0
            * t
            * (0.2969 * math.sqrt(x) - 0.1260 * x - 0.3516 * x**2 + 0.2843 * x**3 - 0.1015 * x**4)
        )
        if m == 0.0 or p == 0.0:
            yc, dyc = 0.0, 0.0
        elif x < p:
            yc = m / p**2 * (2 * p * x - x**2)
            dyc = 2 * m / p**2 * (p - x)
        else:
            yc = m / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x - x**2)
            dyc = 2 * m / (1 - p) ** 2 * (p - x)
        theta = math.atan(dyc)
        upper = (x - yt * math.sin(theta), yc + yt * math.cos(theta))
        lower = (x + yt * math.sin(theta), yc - yt * math.cos(theta))
        return upper, lower

    upper = [surfaces(x)[0] for x in reversed(xs)]
    lower = [surfaces(x)[1] for x in xs[1:]]
    return [(round(x, 6), round(y, 6)) for x, y in upper + lower]


def parse_dat(text: str) -> list[tuple[float, float]]:
    """Selig-format ``.dat``: a name line, then ``x y`` pairs."""
    points: list[tuple[float, float]] = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            points.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    if len(points) < 20:
        raise ValueError("Too few coordinate points.")
    return points


def write_dat(path: Path, name: str, points: list[tuple[float, float]]) -> None:
    lines = [name] + [f"{x:10.6f} {y:10.6f}" for x, y in points]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


@cache
def coordinates(airfoil_id: str) -> tuple[tuple[float, float], ...]:
    if airfoil_id not in LIBRARY_BY_ID:
        raise KeyError(airfoil_id)
    path = COORDINATES_DIR / f"{airfoil_id}.dat"
    return tuple(parse_dat(path.read_text(encoding="ascii")))


# ---------------------------------------------------------------------------
# Section geometry
# ---------------------------------------------------------------------------


def _interp(xs: list[float], ys: list[float], x: float) -> float:
    """Linear interpolation on increasing ``xs`` (clamped at the ends)."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    span = xs[hi] - xs[lo]
    if span <= 0:
        return ys[lo]
    f = (x - xs[lo]) / span
    return ys[lo] + f * (ys[hi] - ys[lo])


def _monotonic(points: list[tuple[float, float]]) -> tuple[list[float], list[float]]:
    points = sorted(points)
    xs: list[float] = []
    ys: list[float] = []
    for x, y in points:
        if xs and x <= xs[-1]:
            continue
        xs.append(x)
        ys.append(y)
    return xs, ys


@cache
def section_geometry(airfoil_id: str) -> dict[str, float]:
    """Maximum thickness and camber (percent of chord) and their chordwise positions."""
    pts = list(coordinates(airfoil_id))
    le = min(range(len(pts)), key=lambda i: pts[i][0])
    upper_x, upper_y = _monotonic(pts[: le + 1])
    lower_x, lower_y = _monotonic(pts[le:])
    best_t = (0.0, 0.0)
    best_c = (0.0, 0.0)
    n = 400
    for i in range(1, n):
        x = 0.5 * (1.0 - math.cos(math.pi * i / n))
        yu = _interp(upper_x, upper_y, x)
        yl = _interp(lower_x, lower_y, x)
        thickness = yu - yl
        camber = 0.5 * (yu + yl)
        if thickness > best_t[0]:
            best_t = (thickness, x)
        if abs(camber) > abs(best_c[0]):
            best_c = (camber, x)
    return {
        "thickness_pct": round(best_t[0] * 100, 2),
        "x_thickness_pct": round(best_t[1] * 100, 1),
        "camber_pct": round(best_c[0] * 100, 2),
        "x_camber_pct": round(best_c[1] * 100, 1) if abs(best_c[0]) > 1e-4 else 0.0,
    }


# ---------------------------------------------------------------------------
# Polars
# ---------------------------------------------------------------------------


@cache
def polar_table() -> dict[str, Any]:
    """The committed XFOIL table. Raises FileNotFoundError when it has not been built."""
    return json.loads(POLARS_FILE.read_text(encoding="utf-8"))


def polars(airfoil_id: str) -> list[dict[str, Any]]:
    table = polar_table()
    return list(table["airfoils"].get(airfoil_id, {}).get("polars", []))


def polar_summary(airfoil_id: str) -> list[dict[str, Any]]:
    return [{key: p.get(key) for key in SUMMARY_KEYS} for p in polars(airfoil_id)]


def airfoil_summary(entry: AirfoilEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "name": entry.name,
        "description": entry.description,
        "use": entry.use,
        **section_geometry(entry.id),
        "source": entry.source,
        "polar_summary": polar_summary(entry.id),
    }


def airfoil_detail(entry: AirfoilEntry) -> dict[str, Any]:
    table = polar_table()
    return {
        **airfoil_summary(entry),
        "coordinates": [[x, y] for x, y in coordinates(entry.id)],
        "polars": polars(entry.id),
        "polar_settings": table.get("settings", {}),
        "polar_tool": table.get("tool", {}),
    }
