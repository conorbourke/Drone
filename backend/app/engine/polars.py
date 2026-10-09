"""Section polars at the operating Reynolds numbers, and profile drag by strip integration.

* XFOIL (Drela, version 6.99 through the ``xfoil`` Python package, the same build as the Phase 2
  tables) runs at the actual Reynolds numbers of the wing root, mean aerodynamic chord and tip,
  the tail, and at the stall speed, with Ncrit from settings (default 9: Drela's value for a
  clean low-turbulence environment; lower it to model a rougher printed surface).
* Polars are cached on disk as ``{cache_dir}/{airfoil}-{re_rounded}-{ncrit}.json``; the Reynolds
  number is rounded to two significant figures (at most +/-5 %, well inside the scatter of
  low-Reynolds-number data) so nearby designs share polars.
* When XFOIL does not converge for a polar (too few points), when the solver worker fails, or in
  the fast path (``allow_xfoil=False``, used by the recommendation sweep), the polar comes from
  cached XFOIL polars at nearby Reynolds numbers or from the committed Phase 2 table
  (``data/airfoil_polars.json``: XFOIL at 60k-3M), interpolated linearly in log(Re). Every polar
  carries its ``source`` and a plain ``note`` so the report says which was used.

Profile drag: for each AVL strip, the local lift coefficient from AVL and the local chord
Reynolds number give cd from the polars (interpolated between the bracketing Reynolds numbers);
CD_profile = sum(cd_i c_i w_i) / S_ref. This replaces the Tier 1 flat-plate wing and tail
friction (docs/ENGINE.md "What Phase 3 replaces").
"""

from __future__ import annotations

import bisect
import contextlib
import copy
import itertools
import json
import math
import os
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.engine import airfoils as airfoil_lib
from app.engine.quantity import MU_SL, RHO_SL

XFOIL_ALPHA_START = -5.0
XFOIL_ALPHA_END = 15.0
XFOIL_ALPHA_STEP = 0.5
XFOIL_MAX_ITER = 80
XFOIL_REPANEL = 160
XFOIL_MIN_POINTS = 15
#: Tail polars stop at 10 deg: tails work at small angles and only the drag polar is used, and
#: thin symmetric sections at low Reynolds numbers converge slowly past the stall.
XFOIL_TAIL_ALPHA_END = 10.0
#: Generic polar for an airfoil with no data (Tier 1 GENERIC_POLAR; Selig et al. 1995-97).
GENERIC = {
    "wing": {
        "cl_max": 1.15,
        "alpha_cl_max_deg": 11.0,
        "alpha_zero_lift_deg": -2.5,
        "cl_alpha_per_rad": 5.9,
        "cd_min": 0.011,
        "cl_at_cd_min": 0.4,
        "cm0": -0.06,
    },
    "tail": {
        "cl_max": 0.85,
        "alpha_cl_max_deg": 10.0,
        "alpha_zero_lift_deg": 0.0,
        "cl_alpha_per_rad": 5.7,
        "cd_min": 0.012,
        "cl_at_cd_min": 0.0,
        "cm0": 0.0,
    },
}


def default_cache_dir() -> Path:
    base = os.environ.get("APP_DATA_DIR")
    if base:
        return Path(base) / "cache" / "polars"
    from app.config import _default_data_dir

    return _default_data_dir() / "cache" / "polars"


def round_re(re: float) -> int:
    """Two significant figures (e.g. 196 400 -> 200 000, 143 000 -> 140 000)."""
    if re <= 0 or not math.isfinite(re):
        return 0
    exp = math.floor(math.log10(re)) - 1
    return int(round(re / 10**exp) * 10**exp)


# ---------------------------------------------------------------------------
# XFOIL (runs inside the solver worker, see avl_model.SolverWorker)
# ---------------------------------------------------------------------------


def xfoil_polar_raw(
    airfoil: str,
    re: float,
    ncrit: float,
    alpha_start: float = XFOIL_ALPHA_START,
    alpha_end: float = XFOIL_ALPHA_END,
    step: float = XFOIL_ALPHA_STEP,
    max_iter: int = XFOIL_MAX_ITER,
) -> dict[str, Any]:
    """One XFOIL polar: sweep up from 0 deg, reset the boundary layer, sweep down; retry the
    missing points in the attached-flow range once from a fresh boundary layer."""
    import numpy as np
    from xfoil import XFoil
    from xfoil.model import Airfoil

    t0 = time.time()
    pts = airfoil_lib.coordinates(airfoil)
    xf = XFoil()
    xf.print = False
    xf.airfoil = Airfoil(np.array([p[0] for p in pts]), np.array([p[1] for p in pts]))
    xf.repanel(n_nodes=XFOIL_REPANEL)
    xf.n_crit = float(ncrit)
    xf.M = 0.0
    xf.max_iter = int(max_iter)
    xf.Re = float(re)

    def sweep(a0: float, a1: float, d: float) -> list[tuple[float, float, float, float]]:
        n = round((a1 - a0) / d) + 1
        _a, cl, cd, cm, _cp = xf.aseq(a0, a1 + d, d)
        rows = []
        for i in range(min(n, len(cl))):
            rows.append((round(a0 + i * d, 3), float(cl[i]), float(cd[i]), float(cm[i])))
        return rows

    xf.reset_bls()
    fine_end = min(alpha_end, 10.0)
    rows = sweep(0.0, fine_end, step)
    if alpha_end > fine_end:
        # Beyond 10 deg (only cl_max is needed there) continue in 1 deg steps from the converged
        # boundary layer: fewer slow, often non-converging points near the stall.
        rows += sweep(fine_end + 1.0, alpha_end, 1.0)
    xf.reset_bls()
    rows += sweep(-step, alpha_start, -step)
    rows.sort()
    for i, r in enumerate(rows):
        if not math.isfinite(r[1]) and -3.0 <= r[0] <= 10.0:
            xf.reset_bls()
            cl, cd, cm, _cp = xf.a(r[0])
            rows[i] = (r[0], float(cl), float(cd), float(cm))
    good = [r for r in rows if all(math.isfinite(v) for v in r[1:]) and r[2] > 0]
    return {
        "airfoil": airfoil,
        "re": float(re),
        "ncrit": float(ncrit),
        "alpha": [r[0] for r in good],
        "cl": [round(r[1], 5) for r in good],
        "cd": [round(r[2], 6) for r in good],
        "cm": [round(r[3], 5) for r in good],
        "alpha_end": float(alpha_end),
        "converged_points": len(good),
        "non_converged_points": len(rows) - len(good),
        "seconds": round(time.time() - t0, 2),
        "tool": "XFOIL 6.99 (xfoil-python 1.1.1)",
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---------------------------------------------------------------------------
# Section polar objects
# ---------------------------------------------------------------------------


def summarise(
    alpha: Sequence[float],
    cl: Sequence[float],
    cd: Sequence[float],
    cm: Sequence[float],
    alpha_end: float = XFOIL_ALPHA_END,
) -> dict[str, Any]:
    """cl_max, zero-lift angle, lift slope (-2...6 deg fit), cd_min and cm0, as Phase 2."""
    out: dict[str, Any] = {
        k: None
        for k in (
            "cl_max",
            "alpha_cl_max_deg",
            "alpha_zero_lift_deg",
            "cl_alpha_per_rad",
            "cd_min",
            "cl_at_cd_min",
            "cm0",
        )
    }
    if not alpha:
        return out
    i_max = max(range(len(cl)), key=lambda i: cl[i])
    out["cl_max"] = cl[i_max]
    out["alpha_cl_max_deg"] = alpha[i_max]
    out["cl_max_at_sweep_end"] = alpha[i_max] >= alpha_end - 1e-9
    i_min = min(range(len(cd)), key=lambda i: cd[i])
    out["cd_min"] = cd[i_min]
    out["cl_at_cd_min"] = cl[i_min]
    fit = [(math.radians(a), c) for a, c in zip(alpha, cl, strict=True) if -2.0 <= a <= 6.0]
    if len(fit) >= 4:
        n = len(fit)
        mx = sum(p[0] for p in fit) / n
        my = sum(p[1] for p in fit) / n
        sxx = sum((p[0] - mx) ** 2 for p in fit)
        sxy = sum((p[0] - mx) * (p[1] - my) for p in fit)
        if sxx > 0:
            out["cl_alpha_per_rad"] = sxy / sxx
    crossings = []
    for i in range(len(alpha) - 1):
        c0, c1 = cl[i], cl[i + 1]
        if alpha[i] < -6.01 or alpha[i + 1] > 8.01:
            continue
        if c0 == 0.0:
            crossings.append(alpha[i])
        elif (c0 < 0.0 < c1 or c1 < 0.0 < c0) and c1 != c0:
            crossings.append(alpha[i] + (0 - c0) * (alpha[i + 1] - alpha[i]) / (c1 - c0))
    if crossings:
        a0 = min(crossings, key=abs)
        out["alpha_zero_lift_deg"] = a0
        for i in range(len(alpha) - 1):
            if alpha[i] <= a0 <= alpha[i + 1] and alpha[i + 1] != alpha[i]:
                f = (a0 - alpha[i]) / (alpha[i + 1] - alpha[i])
                out["cm0"] = cm[i] + f * (cm[i + 1] - cm[i])
                break
    return out


class SectionPolar:
    """A polar at one Reynolds number (or a log(Re) blend of two)."""

    def __init__(
        self,
        airfoil: str,
        re: float,
        alpha: Sequence[float],
        cl: Sequence[float],
        cd: Sequence[float],
        cm: Sequence[float],
        source: str,
        note: str,
        ncrit: float = 9.0,
        alpha_end: float = XFOIL_ALPHA_END,
    ) -> None:
        self.airfoil = airfoil
        self.alpha_end = float(alpha_end)
        self.re = float(re)
        self.ncrit = ncrit
        self.alpha = list(alpha)
        self.cl = list(cl)
        self.cd = list(cd)
        self.cm = list(cm)
        self.source = source
        self.note = note
        self.summary = summarise(self.alpha, self.cl, self.cd, self.cm, self.alpha_end)
        self._branch = self._attached_branch()
        self.parts: list[tuple[float, SectionPolar]] = [(1.0, self)]

    def _attached_branch(self) -> tuple[list[float], list[float]]:
        """Monotonic cl -> cd curve from the most negative lift up to cl_max."""
        if not self.alpha:
            return [], []
        i_max = max(range(len(self.cl)), key=lambda i: self.cl[i])
        cls: list[float] = []
        cds: list[float] = []
        for i in range(i_max + 1):
            c = self.cl[i]
            if cls and c <= cls[-1]:
                # Keep the curve monotonic: drop a non-increasing point (laminar bubble kink).
                continue
            cls.append(c)
            cds.append(self.cd[i])
        return cls, cds

    @property
    def cl_max(self) -> float:
        return float(self.summary["cl_max"])

    def cd_at_cl(self, cl: float) -> tuple[float, bool]:
        """Profile drag at a lift coefficient; True when cl lies outside the polar."""
        cls, cds = self._branch
        if len(cls) < 2:
            return float("nan"), True
        if cl <= cls[0]:
            return cds[0], cl < cls[0] - 0.05
        if cl >= cls[-1]:
            return cds[-1], True
        j = bisect.bisect_right(cls, cl)
        f = (cl - cls[j - 1]) / (cls[j] - cls[j - 1])
        return cds[j - 1] + f * (cds[j] - cds[j - 1]), False

    def to_dict(self, include_arrays: bool = False) -> dict[str, Any]:
        d = {
            "airfoil": self.airfoil,
            "re": round(self.re),
            "ncrit": self.ncrit,
            "source": self.source,
            "note": self.note,
            **{k: (round(v, 6) if isinstance(v, float) else v) for k, v in self.summary.items()},
        }
        if include_arrays:
            d.update(alpha=self.alpha, cl=self.cl, cd=self.cd, cm=self.cm)
        return d


class BlendedPolar(SectionPolar):
    """Linear blend in log(Re) of two polars (weights w and 1 - w)."""

    def __init__(self, a: SectionPolar, b: SectionPolar, re: float) -> None:
        la, lb = math.log(a.re), math.log(b.re)
        w = 0.0 if lb == la else min(1.0, max(0.0, (math.log(re) - la) / (lb - la)))
        self.airfoil = a.airfoil
        self.re = float(re)
        self.ncrit = a.ncrit
        self.alpha, self.cl, self.cd, self.cm = [], [], [], []
        self.alpha_end = min(a.alpha_end, b.alpha_end)
        self.source = a.source if a.source == b.source else f"{a.source}+{b.source}"
        self.note = (
            f"interpolated in log(Re) between {round(a.re / 1000)}k ({a.source}) and "
            f"{round(b.re / 1000)}k ({b.source})"
        )
        self.parts = [(1 - w, a), (w, b)]
        self.summary = {}
        for k in (
            "cl_max",
            "alpha_cl_max_deg",
            "alpha_zero_lift_deg",
            "cl_alpha_per_rad",
            "cd_min",
            "cl_at_cd_min",
            "cm0",
        ):
            va, vb = a.summary.get(k), b.summary.get(k)
            self.summary[k] = None if va is None or vb is None else (1 - w) * va + w * vb
        self.summary["cl_max_at_sweep_end"] = bool(
            a.summary.get("cl_max_at_sweep_end") or b.summary.get("cl_max_at_sweep_end")
        )

    def cd_at_cl(self, cl: float) -> tuple[float, bool]:
        total = 0.0
        beyond = False
        for w, p in self.parts:
            cd, out = p.cd_at_cl(cl)
            total += w * cd
            beyond = beyond or (out and w > 0.2)
        return total, beyond


def generic_polar(airfoil: str, re: float, use: str) -> SectionPolar:
    """Parabolic drag polar from the generic values (airfoil not in the library)."""
    g = GENERIC["tail" if use == "tail" else "wing"]
    alphas = [a * 0.5 for a in range(-10, 31)]
    cl, cd, cm = [], [], []
    a0 = g["alpha_zero_lift_deg"]
    for a in alphas:
        c = min(g["cl_alpha_per_rad"] * math.radians(a - a0), g["cl_max"])
        cl.append(c)
        cd.append(g["cd_min"] + 0.012 * (c - g["cl_at_cd_min"]) ** 2)
        cm.append(g["cm0"])
    return SectionPolar(
        airfoil,
        re,
        alphas,
        cl,
        cd,
        cm,
        "generic",
        "airfoil not in the library: typical low-Reynolds values used "
        "(Selig et al., Summary of Low-Speed Airfoil Data)",
    )


# ---------------------------------------------------------------------------
# Store: cache, XFOIL runs and fallbacks
# ---------------------------------------------------------------------------


class PolarStore:
    """Provides polars for (airfoil, Re). ``allow_xfoil=False`` never runs XFOIL (fast path)."""

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        ncrit: float = 9.0,
        allow_xfoil: bool = True,
        xfoil_timeout_s: float = 45.0,
        use_cache: bool = True,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.ncrit = float(ncrit)
        self.allow_xfoil = allow_xfoil
        self.use_cache = use_cache
        self.xfoil_timeout_s = xfoil_timeout_s
        self.log: list[dict[str, Any]] = []
        self._mem: dict[tuple[str, int], SectionPolar] = {}
        self._failed: set[tuple[str, int]] = set()

    # ----- cache -----
    def _path(self, airfoil: str, re_r: int) -> Path:
        return self.cache_dir / f"{airfoil}-{re_r}-{self.ncrit:g}.json"

    def _load(self, airfoil: str, re_r: int, alpha_end: float = 0.0) -> SectionPolar | None:
        if not self.use_cache:
            return None
        key = (airfoil, re_r)
        if key in self._mem and self._mem[key].alpha_end >= alpha_end - 1e-9:
            return self._mem[key]
        path = self._path(airfoil, re_r)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if raw.get("converged_points", 0) < XFOIL_MIN_POINTS:
            return None
        if raw.get("alpha_end", XFOIL_ALPHA_END) < alpha_end - 1e-9:
            return None  # cached with a shorter sweep (tail polar); a full sweep is needed
        pol = SectionPolar(
            airfoil,
            raw["re"],
            raw["alpha"],
            raw["cl"],
            raw["cd"],
            raw["cm"],
            "xfoil",
            f"XFOIL at Re {re_r:,}, Ncrit {self.ncrit:g} (cached)",
            self.ncrit,
            raw.get("alpha_end", XFOIL_ALPHA_END),
        )
        self._mem[key] = pol
        return pol

    def cached_res(self, airfoil: str) -> list[int]:
        """Reynolds numbers (rounded) of cached XFOIL polars for this airfoil and Ncrit."""
        out: list[int] = []
        if self.use_cache and self.cache_dir.is_dir():
            suffix = f"-{self.ncrit:g}.json"
            for f in self.cache_dir.glob(f"{airfoil}-*{suffix}"):
                mid = f.name[len(airfoil) + 1 : -len(suffix)]
                if mid.isdigit():
                    out.append(int(mid))
        return sorted(out)

    def _run_xfoil(
        self, airfoil: str, re_r: int, alpha_end: float = XFOIL_ALPHA_END
    ) -> SectionPolar | None:
        from app.engine.avl_model import SolverError, solver_worker

        t0 = time.time()
        try:
            raw = solver_worker().request(
                {
                    "op": "xfoil",
                    "args": {
                        "airfoil": airfoil,
                        "re": re_r,
                        "ncrit": self.ncrit,
                        "alpha_end": alpha_end,
                    },
                },
                timeout=self.xfoil_timeout_s,
                cost=1,
            )
        except SolverError as exc:
            self.log.append(
                {
                    "airfoil": airfoil,
                    "re": re_r,
                    "event": "xfoil_failed",
                    "seconds": round(time.time() - t0, 2),
                    "detail": str(exc),
                }
            )
            return None
        ok = raw["converged_points"] >= XFOIL_MIN_POINTS
        self.log.append(
            {
                "airfoil": airfoil,
                "re": re_r,
                "event": "xfoil_run" if ok else "xfoil_not_converged",
                "seconds": round(time.time() - t0, 2),
                "converged_points": raw["converged_points"],
            }
        )
        if not ok:
            return None
        with contextlib.suppress(OSError):
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._path(airfoil, re_r).with_suffix(".tmp")
            tmp.write_text(json.dumps(raw))
            tmp.replace(self._path(airfoil, re_r))
        pol = SectionPolar(
            airfoil,
            raw["re"],
            raw["alpha"],
            raw["cl"],
            raw["cd"],
            raw["cm"],
            "xfoil",
            f"XFOIL at Re {re_r:,}, Ncrit {self.ncrit:g}",
            self.ncrit,
            raw.get("alpha_end", XFOIL_ALPHA_END),
        )
        self._mem[(airfoil, re_r)] = pol
        return pol

    def table_polar(self, airfoil: str, re: float) -> SectionPolar | None:
        rows = [r for r in airfoil_lib.polars(airfoil) if len(r.get("alpha", [])) >= 10]
        if not rows:
            return None
        pols = [
            SectionPolar(
                airfoil,
                r["re"],
                r["alpha"],
                r["cl"],
                r["cd"],
                r["cm"],
                "table",
                f"Phase 2 XFOIL table at Re {r['re']:,}",
            )
            for r in rows
        ]
        return _bracket(pols, re)

    # ----- public -----
    def get(self, airfoil: str, re: float, use: str = "wing") -> SectionPolar:
        re_r = round_re(re)
        if airfoil not in airfoil_lib.LIBRARY_BY_ID or re_r <= 0:
            return generic_polar(airfoil, re, use)
        alpha_end = XFOIL_TAIL_ALPHA_END if use == "tail" else XFOIL_ALPHA_END
        pol = self._load(airfoil, re_r, alpha_end)
        if pol is not None:
            return pol
        key = (airfoil, re_r)
        if self.allow_xfoil and key not in self._failed:
            pol = self._run_xfoil(airfoil, re_r, alpha_end)
            if pol is not None:
                return pol
            self._failed.add(key)
        # Fallback: cached XFOIL polars at nearby Re plus the Phase 2 table, log(Re) blend.
        cands: list[SectionPolar] = []
        for r in self.cached_res(airfoil):
            p = self._load(airfoil, r)
            if p is not None:
                cands.append(p)
        near = [p for p in cands if abs(math.log(p.re / re)) < math.log(1.6)]
        if near:
            pol = _bracket(near, re)
        else:
            pol = self.table_polar(airfoil, re) or generic_polar(airfoil, re, use)
        why = (
            "XFOIL did not converge"
            if key in self._failed
            else "fast path, XFOIL not run"
            if self.use_cache
            else "table polars requested"
        )
        pol = copy.copy(pol)
        pol.note = f"{pol.note} ({why})"
        return pol

    def ensure(self, jobs: Sequence[tuple[str, float]]) -> None:
        """Compute (or load) every polar in ``jobs`` up front."""
        for airfoil, re in jobs:
            self.get(airfoil, re)


def _bracket(pols: list[SectionPolar], re: float) -> SectionPolar:
    pols = sorted(pols, key=lambda p: p.re)
    if re <= pols[0].re:
        return pols[0]
    if re >= pols[-1].re:
        return pols[-1]
    for a, b in itertools.pairwise(pols):
        if a.re <= re <= b.re:
            return BlendedPolar(a, b, re)
    return pols[-1]


# ---------------------------------------------------------------------------
# Strip integration
# ---------------------------------------------------------------------------


def polar_at(polars: list[SectionPolar], re: float) -> SectionPolar:
    """Interpolate in log(Re) between the supplied polars (clamped at the ends)."""
    return _bracket(polars, re)


def strip_profile_drag(
    strips: list[dict[str, Any]], polars: list[SectionPolar], speed: float, s_ref: float
) -> dict[str, Any]:
    """Profile drag coefficient (on S_ref) from strips {chord, width, cl} in metres."""
    total = 0.0
    beyond = 0
    rows = []
    for s in strips:
        re = RHO_SL * speed * s["chord"] / MU_SL
        pol = polar_at(polars, re)
        cd, out = pol.cd_at_cl(s["cl"])
        if not math.isfinite(cd):
            continue
        beyond += int(out)
        area = s["chord"] * s["width"]
        total += cd * area
        rows.append({"y": s.get("y"), "re": re, "cl": s["cl"], "cd": cd, "area": area})
    return {"cd": total / s_ref, "strips_outside_polar": beyond, "rows": rows}
