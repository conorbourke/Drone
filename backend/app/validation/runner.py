"""Validation runner: runs every case group and builds the report (docs/phases/PHASE3.md sec. 5)."""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from typing import Any

from app.engine.analysis import ENGINE_VERSION
from app.validation import cases
from app.validation.report import build_report, write_report

ProgressFn = Callable[[float, str], None]

GROUPS: list[tuple[str, str, Callable[..., list[dict[str, Any]]]]] = [
    ("textbook", "Textbook and closed-form results", lambda cache_dir: cases.textbook_cases()),
    (
        "avl_reference",
        "AVL against published vortex-lattice values and Tier 1",
        lambda cache_dir: cases.avl_reference_cases(cache_dir),
    ),
    (
        "xfoil_reference",
        "XFOIL against wind-tunnel data",
        lambda cache_dir: cases.xfoil_reference_cases(cache_dir),
    ),
    (
        "published_design",
        "Published aircraft (endurance)",
        lambda cache_dir: cases.published_design_cases(cache_dir),
    ),
]


def run_validation(
    path: str | None = None,
    *,
    cache_dir: str | None = None,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Run the validation suite; return the report dict and write it to ``path`` when given.

    A group that raises is recorded as one failed case with the error, so the report always
    comes back."""
    t0 = time.time()
    rows: list[dict[str, Any]] = []
    timings: dict[str, float] = {}
    for i, (key, label, fn) in enumerate(GROUPS):
        if progress:
            progress(i / len(GROUPS), label)
        t = time.time()
        try:
            rows.extend(fn(cache_dir))
        except Exception as exc:  # report, never crash the worker
            rows.append(
                cases.row(
                    f"{key}.error",
                    key,
                    f"{label}: error",
                    "group failed to run",
                    None,
                    "",
                    "",
                    None,
                    None,
                    f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}",
                    status="fail",
                )
            )
        timings[key] = round(time.time() - t, 2)
    timings["total_s"] = round(time.time() - t0, 2)
    report = build_report(rows, ENGINE_VERSION, timings, [(k, lbl) for k, lbl, _ in GROUPS])
    if path:
        write_report(report, path)
    if progress:
        progress(1.0, "Done")
    return report
