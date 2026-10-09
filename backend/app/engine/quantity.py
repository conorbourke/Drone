"""Quantities, statuses, uncertainty rules and the standard atmosphere for the server engine.

Every number the engine reports is a *Quantity*: a plain dict

    {"value", "low", "high", "unit", "label", "explain", "source"}

the same shape as the Tier 1 TypeScript engine (``frontend/src/engine/quantity.ts``). ``explain``
is one or two plain sentences for a hobbyist; ``source`` names the method and the reference.

Uncertainty rules (docs/ENGINE.md "Uncertainty model", kept identical so Tier 1 and Tier 2 ranges
mean the same thing: roughly a one-standard-deviation band, not a guaranteed bound):

1. Power-law products: relative half-range sqrt(sum (a_i u_i)^2) (first-order propagation,
   ISO GUM style).
2. Non-linear models: one factor at a time at its low and high end, downward and upward
   deviations combined separately by root-sum-square.

Air: sea-level ISA (ICAO Doc 7488 / ISO 2533), as Tier 1.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any

# ---------------------------------------------------------------------------
# Physical constants and the sea-level standard atmosphere
# ---------------------------------------------------------------------------

#: Standard gravity, m/s^2 (CGPM 1901 / ISO 80000-3).
G0 = 9.80665
#: Sea-level ISA pressure (Pa), temperature (K) and the specific gas constant of dry air.
P_SL = 101_325.0
T_SL = 288.15
R_AIR = 287.05287
#: Sea-level ISA density, kg/m^3 (ICAO Doc 7488 / ISO 2533). Equals P_SL / (R_AIR T_SL).
RHO_SL = 1.225
#: Sea-level dynamic viscosity, kg/(m s) (Sutherland's law at 288.15 K, ISA).
MU_SL = 1.789e-5
#: Sea-level speed of sound, m/s (ISA).
A_SL = 340.294

ISA_SOURCE = "Sea-level International Standard Atmosphere (ICAO Doc 7488 / ISO 2533)."


def reynolds(speed_mps: float, length_m: float, rho: float = RHO_SL, mu: float = MU_SL) -> float:
    """Reynolds number rho V L / mu."""
    return rho * speed_mps * length_m / mu


def dynamic_pressure(speed_mps: float, rho: float = RHO_SL) -> float:
    return 0.5 * rho * speed_mps * speed_mps


def mach(speed_mps: float) -> float:
    return speed_mps / A_SL


# ---------------------------------------------------------------------------
# Quantity builders
# ---------------------------------------------------------------------------


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def clean(x: Any) -> float | None:
    """A JSON-safe float: NaN and infinities become None."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def q_range(
    value: float, low: float, high: float, unit: str, label: str, explain: str, source: str
) -> dict[str, Any]:
    """A Quantity with an explicit range; the range always contains the value."""
    if not _finite(value):
        return {
            "value": None,
            "low": None,
            "high": None,
            "unit": unit,
            "label": label,
            "explain": explain,
            "source": source,
        }
    lo = min(low, value) if _finite(low) else value
    hi = max(high, value) if _finite(high) else value
    return {
        "value": float(value),
        "low": float(lo),
        "high": float(hi),
        "unit": unit,
        "label": label,
        "explain": explain,
        "source": source,
    }


def q_rel(value: float, rel: float, unit: str, label: str, explain: str, source: str) -> dict:
    """Symmetric relative half-range (0.1 = +/-10 %)."""
    d = abs(value) * abs(rel) if _finite(value) else 0.0
    return q_range(value, value - d, value + d, unit, label, explain, source)


def q_abs(value: float, half: float, unit: str, label: str, explain: str, source: str) -> dict:
    return q_range(value, value - abs(half), value + abs(half), unit, label, explain, source)


def q_exact(value: float, unit: str, label: str, explain: str, source: str) -> dict:
    """Geometry taken from the owner's own numbers: low = high = value."""
    return q_range(value, value, value, unit, label, explain, source)


def q_asym(
    value: float, rel_low: float, rel_high: float, unit: str, label: str, explain: str, source: str
) -> dict:
    """Asymmetric relative range: value (1 - rel_low) .. value (1 + rel_high)."""
    return q_range(
        value, value * (1 - rel_low), value * (1 + rel_high), unit, label, explain, source
    )


def rss(values: Iterable[float]) -> float:
    return math.sqrt(sum(v * v for v in values))


def rss_powers(terms: Iterable[tuple[float, float]]) -> float:
    """Rule 1: relative half-range of y = c prod x_i^a_i from (a_i, u_i) pairs."""
    return math.sqrt(sum((a * u) ** 2 for a, u in terms))


def one_at_a_time(nominal: float, perturbed: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """Rule 2: combine downward and upward deviations separately by root-sum-square."""
    down: list[float] = []
    up: list[float] = []
    for a, b in perturbed:
        da = a - nominal if _finite(a) else 0.0
        db = b - nominal if _finite(b) else 0.0
        down.append(min(0.0, da, db))
        up.append(max(0.0, da, db))
    return nominal - rss(down), nominal + rss(up)


# ---------------------------------------------------------------------------
# Statuses (checks and notes)
# ---------------------------------------------------------------------------

LEVEL_ORDER = {"fail": 0, "warn": 1, "ok": 2, "info": 3}


def status(key: str, label: str, level: str, message: str, **extra: Any) -> dict[str, Any]:
    """A status ``{key, label, level, message}`` plus optional fields (value, threshold...)."""
    out: dict[str, Any] = {"key": key, "label": label, "level": level, "message": message}
    out.update(extra)
    return out


def sort_statuses(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fail, warn, ok, then info; stable within a level."""
    return [
        s
        for _, s in sorted(
            enumerate(items), key=lambda p: (LEVEL_ORDER.get(p[1]["level"], 9), p[0])
        )
    ]


def fmt(v: float | None, digits: int = 0) -> str:
    """Format a number for a plain message ("?" when unknown)."""
    if v is None or not math.isfinite(v):
        return "?"
    if digits == 0:
        return f"{round(v):d}"
    return f"{v:.{digits}f}"


def fmt_range(q: dict[str, Any], digits: int = 0) -> str:
    """ "95 (88-104)" style text for a Quantity."""
    return (
        f"{fmt(q.get('value'), digits)} ({fmt(q.get('low'), digits)}-{fmt(q.get('high'), digits)})"
    )
