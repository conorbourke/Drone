"""Validation report: JSON document, file writing and a plain-text summary."""

from __future__ import annotations

import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPORT_SCHEMA = "validation-report/1"
TOLERANCE_NOTE = (
    "Tolerances are per case. Textbook and vortex-lattice cases use 0.1-4 %; airfoil data 10-20 % "
    "(wind-tunnel scatter); published aircraft +/-30 %, deliberately wide because their geometry "
    "is partly assumed: those cases show the size of the error honestly rather than prove accuracy."
)


def default_report_path() -> Path:
    """``{APP_DATA_DIR}/validation/latest.json``."""
    base = os.environ.get("APP_DATA_DIR")
    if base:
        return Path(base) / "validation" / "latest.json"
    from app.config import _default_data_dir

    return _default_data_dir() / "validation" / "latest.json"


def _clean(v: Any) -> Any:
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def build_report(
    rows: list[dict[str, Any]],
    engine_version: str,
    timings: dict[str, float],
    groups: list[tuple[str, str]],
) -> dict[str, Any]:
    counts = {
        s: sum(1 for r in rows if r["status"] == s) for s in ("pass", "fail", "skipped", "info")
    }
    return _clean(
        {
            "schema": REPORT_SCHEMA,
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "engine_version": engine_version,
            "summary": {**counts, "cases": len(rows), "all_passed": counts["fail"] == 0},
            "tolerance_note": TOLERANCE_NOTE,
            "groups": [{"key": k, "label": lbl} for k, lbl in groups],
            "cases": rows,
            "timings": timings,
        }
    )


def write_report(report: dict[str, Any], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p


def text_summary(report: dict[str, Any]) -> str:
    """One line per case: status, name, engine vs reference, error and tolerance."""
    lines = []
    for r in report["cases"]:
        ref = r["reference"]["value"]
        eng = r["engine"]["value"]
        err = r["error_pct"]
        tol = r["tolerance_pct"]
        lines.append(
            f"{r['status'].upper():7s} {r['name']}: engine "
            f"{'n/a' if eng is None else f'{eng:.5g}'} vs ref "
            f"{'n/a' if ref is None else f'{ref:.5g}'} "
            f"{r['reference']['unit']}"
            + (f", error {err:+.1f} %" if err is not None else "")
            + (f" (tolerance {tol:g} %)" if tol is not None else "")
        )
    s = report["summary"]
    lines.append(f"{s['pass']} pass, {s['fail']} fail, {s['skipped']} skipped, {s['info']} info")
    return "\n".join(lines)
