"""Full analysis: shape of the result, quantities, timing, layouts, invalid input."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from app.engine.analysis import run_analysis

QUANTITY_KEYS = {"value", "low", "high", "unit", "label", "explain", "source"}


def _quantities(node: Any, path: str = "") -> list[tuple[str, dict[str, Any]]]:
    out = []
    if isinstance(node, dict):
        if set(node) >= QUANTITY_KEYS:
            out.append((path, node))
        else:
            for k, v in node.items():
                out.extend(_quantities(v, f"{path}.{k}"))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_quantities(v, f"{path}[{i}]"))
    return out


def test_default_full_analysis(default_full: dict[str, Any]) -> None:
    r = default_full
    assert r["valid"] and r["mode"] == "full"
    json.dumps(r, allow_nan=False)  # strict JSON
    qs = _quantities(r)
    assert len(qs) > 40
    for path, q in qs:
        if q["value"] is None:
            continue
        assert q["low"] <= q["value"] <= q["high"], path
        assert q["explain"] and q["source"] and q["label"], path
    s = r["summary"]
    assert 2.5 < s["takeoff_mass"]["value"] < 4.5
    assert 5 < s["endurance_cruise"]["value"] < 60
    assert s["endurance_cruise"]["low"] < s["endurance_cruise"]["high"]
    assert 8 < s["stall_speed"]["value"] < 14
    assert {c["key"] for c in r["checks"]} >= {
        "hover_thrust_to_weight",
        "cg_envelope",
        "mtow_legal",
    }
    assert len(r["drag"]["items"]) >= 7
    assert r["tier1_comparison"] and all(row["why"] for row in r["tier1_comparison"])
    assert r["transition"]["points"] and r["structure"]["wing_spar"]["span_loading"]
    assert len(r["balance"]["cg_envelope"]) == 5
    assert r["mission"]["payload_max"]["segments"][2]["key"] == "cruise"
    assert "IAA" in r["a3_note"]
    # Every polar came from XFOIL at the operating Reynolds numbers (cold cache in this session).
    assert all(p["source"] == "xfoil" for p in r["aero"]["polars"].values())


def test_cached_analysis_is_fast(
    default_full: dict[str, Any], polar_cache: str, params: dict[str, Any], mission: dict[str, Any]
) -> None:
    r = run_analysis(params, mission, None, mode="full", cache_dir=polar_cache)
    assert r["timings"]["total_s"] < 10
    assert r["timings"]["polars_s"] < 1
    assert r["summary"]["endurance_cruise"]["value"] == pytest.approx(
        default_full["summary"]["endurance_cruise"]["value"], rel=1e-6
    )


@pytest.mark.parametrize("layout", ["rear_tilt", "quad_pusher"])
def test_other_layouts(
    polar_cache: str, params: dict[str, Any], mission: dict[str, Any], layout: str
) -> None:
    params["layout"] = layout
    r = run_analysis(params, mission, None, mode="fast", cache_dir=polar_cache)
    assert r["valid"]
    if layout == "quad_pusher":
        assert r["propulsion"]["pusher_propeller"] is not None
        assert r["propulsion"]["cruise_rotors"] == 1


def test_invalid_input_returns_fail_checks(params: dict[str, Any], mission: dict[str, Any]) -> None:
    bad = copy.deepcopy(params)
    bad["wing"]["span_mm"] = -5
    r = run_analysis(bad, mission, None, mode="fast")
    assert r["valid"] is False
    assert r["checks"] and all(c["level"] == "fail" for c in r["checks"])


def test_phase3_settings_defaults_and_overrides(
    polar_cache: str, params: dict[str, Any], mission: dict[str, Any]
) -> None:
    r1 = run_analysis(
        params,
        mission,
        {"checks": {"manoeuvre_load_factor": 3.0}},
        mode="fast",
        cache_dir=polar_cache,
    )
    r2 = run_analysis(
        params,
        mission,
        {"checks": {"manoeuvre_load_factor": 6.0}},
        mode="fast",
        cache_dir=polar_cache,
    )
    m1 = r1["structure"]["wing_spar"]["root_moment_ultimate_nm"]
    m2 = r2["structure"]["wing_spar"]["root_moment_ultimate_nm"]
    assert m2 == pytest.approx(2 * m1, rel=0.05)
    assert r1["inputs"]["settings"]["analysis"]["ncrit"] == 9.0


def test_progress_and_catalogue_thrust_data(
    polar_cache: str, params: dict[str, Any], mission: dict[str, Any]
) -> None:
    seen: list[tuple[float, str]] = []
    motor = {
        "kv_rpm_per_v": 380,
        "resistance_ohm": 0.12,
        "no_load_current_a": 0.5,
        "max_current_a": 25,
        "thrust_data": [
            {"prop": "13x5.5", "voltage_v": 22.2, "throttle_pct": 50, "thrust_g": 900,
             "current_a": 5.0, "power_w": 111, "rpm": 4800},
            {"prop": "13x5.5", "voltage_v": 22.2, "throttle_pct": 75, "thrust_g": 1600,
             "current_a": 10.5, "power_w": 233, "rpm": 6400},
        ],
    }  # fmt: skip
    r = run_analysis(
        params,
        mission,
        None,
        mode="fast",
        cache_dir=polar_cache,
        parts={"lift_motor": motor},
        progress=lambda f, label: seen.append((f, label)),
    )
    assert r["valid"]
    assert r["propulsion"]["lift_propeller"]["fitted_to_test_data"] is True
    assert r["propulsion"]["lift_motor"]["assumed"] is False
    fractions = [f for f, _ in seen]
    assert fractions[0] < 0.1 and fractions[-1] == 1.0
    assert fractions == sorted(fractions)


# Browser (Tier 1) cruise power for the same designs, from frontend/src/engine/estimate.ts with
# the fixtures in frontend/src/engine/__fixtures__/designs.ts (defaultInput, quadPusherInput):
# the generic CT(J), CP(J) propeller at thrust = drag / cruise propeller count.
BROWSER_TIER1_CRUISE_POWER_W = {"front_tilt": 218.07, "quad_pusher": 127.50}


def _tier1_row(r: dict[str, Any], key: str) -> dict[str, Any]:
    return next(row for row in r["tier1_comparison"] if row["key"] == key)


def test_tier1_cruise_power_matches_browser(default_full: dict[str, Any]) -> None:
    row = _tier1_row(default_full, "cruise_power")
    assert row["tier1"] == pytest.approx(BROWSER_TIER1_CRUISE_POWER_W["front_tilt"], rel=0.03)
    assert "propeller" in row["why"]


def test_tier1_cruise_power_matches_browser_quad_pusher(
    polar_cache: str, params: dict[str, Any], mission: dict[str, Any]
) -> None:
    params["layout"] = "quad_pusher"
    r = run_analysis(params, mission, None, mode="fast", cache_dir=polar_cache)
    row = _tier1_row(r, "cruise_power")
    assert row["tier1"] == pytest.approx(BROWSER_TIER1_CRUISE_POWER_W["quad_pusher"], rel=0.03)


def test_tier1_propeller_efficiency_matches_the_browser_port() -> None:
    from app.engine.analysis import tier1_propeller_efficiency
    from app.engine.propulsion import generic_propeller

    # frontend/src/engine/propeller.test.ts style check: 330 x 140 mm lift propeller sharing
    # 3.63 N of drag between two at 16 m/s (default design, browser value 0.342 at J ~ 0.47).
    prop = generic_propeller(330, 140, 2)
    eta, j = tier1_propeller_efficiency(prop, 3.6287 / 2, 16.0)
    assert eta == pytest.approx(0.3423, rel=0.01)
    assert 0 < j < prop.j_zero_thrust
    # Thrust balance at the solved point: T = CT rho n^2 D^4.
    n = 16.0 / (j * prop.diameter_m)
    assert prop.ct(j) * 1.225 * n * n * prop.diameter_m**4 == pytest.approx(3.6287 / 2, rel=1e-6)
    assert tier1_propeller_efficiency(prop, 0.0, 16.0) == (0.0, 0.0)
