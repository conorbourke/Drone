"""Validation runner: report shape, sources, and results (docs/phases/PHASE3.md section 5)."""

from __future__ import annotations

import json
from pathlib import Path

from app.validation import run_validation, text_summary, write_report

#: The committed snapshot (docs/phases/PHASE3.md section 5), refreshed by every full test run.
SNAPSHOT = Path(__file__).resolve().parents[3] / "docs" / "validation" / "latest.json"

#: Cases that fail and are reported as such (see the case notes); everything else must pass.
KNOWN_DEVIATIONS = {"textbook.helmbold_ar4"}


def test_runner_writes_report(tmp_path: Path, polar_cache: str) -> None:
    path = tmp_path / "validation" / "latest.json"
    report = run_validation(str(path), cache_dir=polar_cache)
    assert path.is_file()
    assert json.loads(path.read_text())["summary"] == report["summary"]
    groups = {c["group"] for c in report["cases"]}
    assert groups == {"textbook", "avl_reference", "xfoil_reference", "published_design"}
    for c in report["cases"]:
        assert c["status"] in ("pass", "fail", "skipped", "info")
        assert c["reference"]["source"], c["id"]
        assert c["compared"], c["id"]
        if c["status"] in ("pass", "fail") and c["tolerance_pct"] is not None:
            assert c["error_pct"] is not None
    failing = {c["id"] for c in report["cases"] if c["status"] == "fail"}
    assert failing <= KNOWN_DEVIATIONS, failing
    published = [c for c in report["cases"] if c["id"].endswith(".endurance")]
    assert len(published) == 2
    for c in published:
        assert c["tolerance_pct"] == 30.0
        assert "Assumptions" in c["note"]
    assert "pass" in text_summary(report)
    if SNAPSHOT.parent.parent.is_dir():  # a source checkout (not the container image)
        write_report(report, SNAPSHOT)
