"""Scale to weight: a re-solved, non-proportional design that passes its checks at 24 kg."""

from __future__ import annotations

from typing import Any

from app.engine.scale import run_scale


def test_scale_to_24_kg(polar_cache: str, params: dict[str, Any], mission: dict[str, Any]) -> None:
    out = run_scale(params, mission, None, 24.0, mode="full", cache_dir=polar_cache)
    assert out["valid"]
    a = out["analysis"]
    mtow = a["summary"]["takeoff_mass"]["value"]
    assert 20.0 < mtow <= 24.0
    assert not [c for c in out["checks"] if c["level"] == "fail"], [
        c["message"] for c in out["checks"] if c["level"] == "fail"
    ]
    ratios = out["dimension_ratios"]
    assert max(ratios.values()) - min(ratios.values()) > 0.3, ratios  # not one factor
    assert out["mission"]["scale"] == "final"
    assert out["parameters"]["battery"]["cells_series"] == 12
    assert len(out["table"]) >= 15
    assert all(row["why"] for row in out["table"])
    assert out["parameters"]["wing"]["span_mm"] != params["wing"]["span_mm"]
