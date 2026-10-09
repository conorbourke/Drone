"""Validation runner: report shape, sources, and results (docs/phases/PHASE3.md section 5)."""

from __future__ import annotations

import json
from pathlib import Path

from app.validation import run_validation, text_summary, write_report

#: The committed snapshot (docs/phases/PHASE3.md section 5), refreshed by every full test run.
SNAPSHOT = Path(__file__).resolve().parents[3] / "docs" / "validation" / "latest.json"


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
    # Phase 3 acceptance: the suite passes. Skipped (no published reference found) and
    # informational rows are allowed; no case may fail.
    failing = {c["id"]: c["error_pct"] for c in report["cases"] if c["status"] == "fail"}
    assert not failing, failing
    assert report["summary"]["fail"] == 0
    # Lift slope is checked against exact / published lifting-surface values, not only Helmbold.
    by_id = {c["id"]: c for c in report["cases"]}
    for cid in ("textbook.circular_wing_exact", "avl.elliptic_ar10_published_vlm"):
        assert by_id[cid]["status"] == "pass", by_id[cid]
        assert by_id[cid]["tolerance_pct"] == 2.0
    published = [c for c in report["cases"] if c["id"].endswith(".endurance")]
    assert len(published) == 2
    for c in published:
        assert c["tolerance_pct"] == 30.0
        assert "Assumptions" in c["note"]
    assert "pass" in text_summary(report)
    if SNAPSHOT.parent.parent.is_dir():  # a source checkout (not the container image)
        write_report(report, SNAPSHOT)
