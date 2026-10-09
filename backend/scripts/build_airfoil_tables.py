"""Build the committed airfoil tables with XFOIL.

Run from ``backend/``::

    uv run python scripts/build_airfoil_tables.py

It (1) writes the NACA 4-digit coordinate files into ``app/engine/data/airfoils/`` from the
analytic equations, and (2) runs XFOIL (the ``xfoil`` Python package, XFOIL 6.99) for every
library section at the Reynolds numbers below, Ncrit 9, alpha -6...16 deg in 0.5 deg steps, and
writes ``app/engine/data/airfoil_polars.json``. Non-converged points are dropped and counted.

XFOIL's Fortran code prints to the process's standard output; the script silences file
descriptor 1 while XFOIL runs so only the progress lines appear.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.engine.airfoils import (  # noqa: E402
    COORDINATES_DIR,
    LIBRARY,
    LIBRARY_BY_ID,
    NACA_IDS,
    POLARS_FILE,
    coordinates,
    naca4,
    write_dat,
)

REYNOLDS = (60_000, 100_000, 200_000, 400_000, 800_000, 1_500_000, 3_000_000)
N_CRIT = 9.0
MACH = 0.0
ALPHA_START = -6.0
ALPHA_END = 16.0
ALPHA_STEP = 0.5
MAX_ITER = 100
REPANEL_NODES = 160
FIT_RANGE_DEG = (-2.0, 6.0)
XFOIL_PACKAGE = (
    "xfoil @ git+https://github.com/DARcorporation/xfoil-python"
    "@0a8c2fce02ba73b7f89f72306e43d78291d1e024"
)


@contextlib.contextmanager
def silenced_stdout() -> Iterator[None]:
    """Redirect file descriptor 1 (where XFOIL's Fortran writes) to /dev/null."""
    sys.stdout.flush()
    saved = os.dup(1)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
        yield
    finally:
        os.dup2(saved, 1)
        os.close(devnull)
        os.close(saved)


def _make_xfoil(airfoil_id: str) -> Any:
    import numpy as np
    from xfoil import XFoil
    from xfoil.model import Airfoil

    pts = coordinates(airfoil_id)
    xf = XFoil()
    xf.print = False
    xf.airfoil = Airfoil(np.array([p[0] for p in pts]), np.array([p[1] for p in pts]))
    xf.repanel(n_nodes=REPANEL_NODES)
    xf.n_crit = N_CRIT
    xf.M = MACH
    xf.max_iter = MAX_ITER
    return xf


def _sweep(xf: Any, start: float, end: float, step: float) -> list[tuple[float, ...]]:
    """One XFOIL ASEQ from ``start`` to ``end`` inclusive; NaN rows are non-converged."""
    n = round((end - start) / step) + 1
    a, cl, cd, cm, _cp = xf.aseq(start, end + step, step)
    rows = []
    for i in range(min(n, len(cl))):
        alpha = round(start + i * step, 3)
        if math.isfinite(a[i]) and abs(a[i] - alpha) > 1e-3:
            raise RuntimeError(f"XFOIL alpha {a[i]} does not match the expected {alpha}")
        rows.append((alpha, float(cl[i]), float(cd[i]), float(cm[i])))
    return rows


def run_polar(airfoil_id: str, re: float) -> dict[str, Any]:
    with silenced_stdout():
        xf = _make_xfoil(airfoil_id)
        xf.Re = re
        # Sweep outwards from zero so each point starts from a converged neighbour.
        xf.reset_bls()
        up = _sweep(xf, 0.0, ALPHA_END, ALPHA_STEP)
        xf.reset_bls()
        down = _sweep(xf, -ALPHA_STEP, ALPHA_START, -ALPHA_STEP)
        rows = sorted(up + down)
        # Second chance for points the sweep missed: a fresh boundary layer, one point.
        for i, row in enumerate(rows):
            if not math.isfinite(row[1]):
                xf.reset_bls()
                cl, cd, cm, _cp = xf.a(row[0])
                rows[i] = (row[0], float(cl), float(cd), float(cm))
    good = [
        r
        for r in rows
        if all(math.isfinite(v) for v in r[1:]) and r[2] > 0  # cd must be positive
    ]
    return summarise(int(re), good, len(rows) - len(good))


def _interp_alpha_at_zero_lift(alpha: list[float], cl: list[float]) -> float | None:
    """Zero-lift angle. At low Reynolds numbers a laminar bubble can make the lift curve cross
    zero more than once near alpha 0 (NACA 0012 at 60k does); take the crossing nearest 0."""
    crossings: list[float] = []
    for i in range(len(alpha) - 1):
        a0, a1 = alpha[i], alpha[i + 1]
        if a0 < -6.01 or a1 > 8.01:
            continue
        c0, c1 = cl[i], cl[i + 1]
        if c0 == 0.0:
            crossings.append(a0)
        elif (c0 < 0.0 < c1 or c1 < 0.0 < c0) and c1 != c0:
            crossings.append(a0 + (0.0 - c0) * (a1 - a0) / (c1 - c0))
    if alpha and alpha[-1] <= 8.01 and cl[-1] == 0.0:
        crossings.append(alpha[-1])
    return min(crossings, key=abs) if crossings else None


def _interp(xs: list[float], ys: list[float], x: float) -> float | None:
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1] and xs[i + 1] != xs[i]:
            f = (x - xs[i]) / (xs[i + 1] - xs[i])
            return ys[i] + f * (ys[i + 1] - ys[i])
    return None


def _r(value: float, digits: int) -> float:
    """Round, and turn XFOIL's -0.0 into 0.0."""
    return round(value, digits) + 0.0


def summarise(re: float, rows: list[tuple[float, ...]], dropped: int) -> dict[str, Any]:
    alpha = [r[0] for r in rows]
    cl = [r[1] for r in rows]
    cd = [r[2] for r in rows]
    cm = [r[3] for r in rows]
    out: dict[str, Any] = {
        "re": re,
        "cl_max": None,
        "alpha_cl_max_deg": None,
        "alpha_zero_lift_deg": None,
        "cl_alpha_per_rad": None,
        "cd_min": None,
        "cl_at_cd_min": None,
        "cm0": None,
        "cl_max_at_sweep_end": False,
        "converged_points": len(rows),
        "non_converged_points": dropped,
        "alpha": [_r(a, 3) for a in alpha],
        "cl": [_r(v, 5) for v in cl],
        "cd": [_r(v, 6) for v in cd],
        "cm": [_r(v, 5) for v in cm],
    }
    if not rows:
        return out
    i_max = max(range(len(cl)), key=lambda i: cl[i])
    out["cl_max"] = _r(cl[i_max], 4)
    out["alpha_cl_max_deg"] = alpha[i_max]
    # True when the lift was still rising at the end of the sweep: cl_max is then a lower bound.
    out["cl_max_at_sweep_end"] = alpha[i_max] >= ALPHA_END - 1e-9
    i_min = min(range(len(cd)), key=lambda i: cd[i])
    out["cd_min"] = _r(cd[i_min], 6)
    out["cl_at_cd_min"] = _r(cl[i_min], 4)

    fit = [
        (math.radians(a), c)
        for a, c in zip(alpha, cl, strict=True)
        if FIT_RANGE_DEG[0] <= a <= FIT_RANGE_DEG[1]
    ]
    if len(fit) >= 4:
        n = len(fit)
        mx = sum(p[0] for p in fit) / n
        my = sum(p[1] for p in fit) / n
        sxx = sum((p[0] - mx) ** 2 for p in fit)
        sxy = sum((p[0] - mx) * (p[1] - my) for p in fit)
        if sxx > 0:
            out["cl_alpha_per_rad"] = _r(sxy / sxx, 4)

    a0 = _interp_alpha_at_zero_lift(alpha, cl)
    if a0 is not None:
        out["alpha_zero_lift_deg"] = _r(a0, 3)
        cm_at = _interp(alpha, cm, a0)
        out["cm0"] = _r(cm_at, 5) if cm_at is not None else None
    return out


def _job(args: tuple[str, float]) -> tuple[str, dict[str, Any]]:
    airfoil_id, re = args
    return airfoil_id, run_polar(airfoil_id, re)


def sanity_check() -> dict[str, Any]:
    """NACA 0012, Re 1e6, Ncrit 9, alpha 4 deg: published XFOIL value CL ~0.43, CD ~0.0073."""
    with silenced_stdout():
        xf = _make_xfoil("naca0012")
        xf.Re = 1_000_000
        xf.reset_bls()
        rows = _sweep(xf, 0.0, 4.0, 0.5)
    alpha, cl, cd, cm = rows[-1]
    return {
        "case": "naca0012 Re 1e6 Ncrit 9 alpha 4 deg",
        "expected": {"cl": 0.43, "cd": 0.0073},
        "got": {"alpha": alpha, "cl": round(cl, 4), "cd": round(cd, 6), "cm": round(cm, 5)},
    }


def write_naca_files() -> None:
    for airfoil_id in sorted(NACA_IDS):
        entry = LIBRARY_BY_ID[airfoil_id]
        write_dat(COORDINATES_DIR / f"{airfoil_id}.dat", entry.name.upper(), naca4(airfoil_id[4:]))
    coordinates.cache_clear()


def main() -> None:
    write_naca_files()
    import xfoil

    started = time.time()
    jobs = [(entry.id, float(re)) for entry in LIBRARY for re in REYNOLDS]
    results: dict[str, list[dict[str, Any]]] = {entry.id: [] for entry in LIBRARY}
    workers = max(1, min(8, (os.cpu_count() or 2)))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for airfoil_id, polar in pool.map(_job, jobs):
            results[airfoil_id].append(polar)
            print(
                f"{airfoil_id:9s} Re {polar['re']:>9.0f}: cl_max {polar['cl_max']}, "
                f"cd_min {polar['cd_min']}, converged {polar['converged_points']}, "
                f"dropped {polar['non_converged_points']}",
                flush=True,
            )
    check = sanity_check()
    print(f"sanity check: {check}", flush=True)

    doc = {
        "generator": "backend/scripts/build_airfoil_tables.py",
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool": {
            "name": "XFOIL",
            "xfoil_version": "6.99",
            "python_package": XFOIL_PACKAGE,
            "python_package_version": getattr(xfoil, "__version__", "unknown"),
        },
        "settings": {
            "reynolds": list(REYNOLDS),
            "n_crit": N_CRIT,
            "mach": MACH,
            "alpha_start_deg": ALPHA_START,
            "alpha_end_deg": ALPHA_END,
            "alpha_step_deg": ALPHA_STEP,
            "max_iter": MAX_ITER,
            "repanel_nodes": REPANEL_NODES,
            "sweep": "0 to 16 deg, then boundary layer reset and -0.5 to -6 deg",
            "cl_alpha_fit_range_deg": list(FIT_RANGE_DEG),
            "cm0": "pitching moment about the quarter chord at the zero-lift angle",
            "non_converged": "dropped from the arrays and counted per polar",
        },
        "sanity_check": check,
        "airfoils": {
            airfoil_id: {"polars": sorted(polars, key=lambda p: p["re"])}
            for airfoil_id, polars in results.items()
        },
    }
    POLARS_FILE.write_text(json.dumps(doc, separators=(",", ":")) + "\n", encoding="utf-8")
    size_kb = POLARS_FILE.stat().st_size / 1024
    print(f"wrote {POLARS_FILE} ({size_kb:.0f} kB) in {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
