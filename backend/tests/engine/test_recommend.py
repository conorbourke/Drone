"""Recommendations respect every check and are ranked by endurance gained."""

from __future__ import annotations

from typing import Any

from app.engine.analysis import run_analysis
from app.engine.recommend import SEVERITY, candidate_changes, run_recommendations


def test_candidates_cover_the_contract_list(params: dict[str, Any]) -> None:
    keys = {c["key"] for c in candidate_changes(params)}
    for k in (
        "span+",
        "span-",
        "root_chord+",
        "tip_chord-",
        "aspect_ratio+",
        "fuselage_width-",
        "fuselage_height+",
        "battery_capacity+",
        "cells_parallel+",
        "prop_diameter-",
        "airfoil_e387",
    ):
        assert k in keys


def test_sweep(
    default_full: dict[str, Any], polar_cache: str, params: dict[str, Any], mission: dict[str, Any]
) -> None:
    out = run_recommendations(params, mission, None, cache_dir=polar_cache)
    assert out["valid"]
    assert out["duration_s"] < 60
    recs = out["recommendations"]
    assert recs, "the default design has at least one worthwhile change"
    gains = [r["endurance_gain_min"] for r in recs]
    assert gains == sorted(gains, reverse=True)
    assert all(g > 0 for g in gains)
    base = out["baseline_checks"]
    for r in recs:
        assert "endurance +" in r["sentence"] and r["patch"]
        for key, level in r["checks_after"].items():
            assert SEVERITY[level] <= SEVERITY.get(base.get(key, "ok"), 0), (r["key"], key)
    # Re-run the top recommendation independently: no check is worse than in the baseline.
    top = recs[0]
    again = run_analysis(top["parameters"], mission, None, mode="fast", cache_dir=polar_cache)
    for c in again["checks"]:
        assert SEVERITY[c["level"]] <= SEVERITY.get(base.get(c["key"], "ok"), 0)
    for rej in out["rejected"]:
        assert rej["reason"]
