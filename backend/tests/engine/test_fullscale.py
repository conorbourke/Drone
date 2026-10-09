"""Phase 7 full-scale checks: a 24 kg design (default prototype scaled with run_scale) runs the
Phase 3 analysis plus the full-scale checks with sensible results."""

from __future__ import annotations

import copy
from typing import Any

import numpy as np
import pytest

from app.defaults import DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS
from app.engine import fullscale as fs
from app.engine.scale import run_scale

FULLSCALE_KEYS = {
    "fullscale.mtow",
    "fullscale.mtow_layup",
    "fullscale.motor_out",
    "fullscale.spar_tube",
    "fullscale.boom_tube",
    "fullscale.landing_gear",
    "fullscale.battery_current",
}


@pytest.fixture(scope="module")
def scaled_24(polar_cache: str) -> dict[str, Any]:
    out = run_scale(
        copy.deepcopy(DEFAULT_DESIGN_PARAMETERS),
        copy.deepcopy(DEFAULT_MISSION),
        copy.deepcopy(DEFAULT_SETTINGS),
        24.0,
        mode="fast",
        cache_dir=polar_cache,
    )
    assert out["valid"]
    return {"parameters": out["parameters"], "mission": out["mission"]}


@pytest.fixture(scope="module")
def result(scaled_24: dict[str, Any], polar_cache: str) -> dict[str, Any]:
    return fs.run_fullscale_checks(
        scaled_24["parameters"],
        scaled_24["mission"],
        copy.deepcopy(DEFAULT_SETTINGS),
        cache_dir=polar_cache,
    )


def _check(r: dict[str, Any], key: str) -> dict[str, Any]:
    return next(c for c in r["checks"] if c["key"] == key)


def test_24_kg_design_runs_analysis_and_all_checks(result: dict[str, Any]) -> None:
    assert result["valid"]
    assert result["schema"] == fs.SCHEMA
    keys = {c["key"] for c in result["checks"]}
    assert keys >= FULLSCALE_KEYS
    # every Phase 3 check is still there
    assert {c["key"] for c in result["phase3_checks"]} <= keys
    assert {"hover_thrust_to_weight", "battery_current", "spar_strength"} <= keys
    for c in result["checks"]:
        assert c["level"] in ("ok", "warn", "fail", "info")
        assert len(c["message"]) > 20
    mtow = result["summary"]["takeoff_mass"]["value"]
    assert 20.0 < mtow <= 24.0
    assert result["mission"]["scale"] == "final"
    assert result["counts"]["fail"] == sum(1 for c in result["checks"] if c["level"] == "fail")
    # A3 note next to range and endurance
    re = result["range_endurance"]
    assert "IAA" in re["a3_note"] and "150 m" in re["a3_note"]
    assert re["range"]["value"] > 0 and re["endurance_cruise"]["value"] > 0


def test_mtow_thresholds_from_settings(result: dict[str, Any]) -> None:
    m = result["mtow"]
    assert [t["kg"] for t in m["thresholds"]] == [23.0, 24.0, 25.0]
    mass = m["mass_kg"]
    assert m["level"] == ("warn" if 23.0 <= mass <= 24.0 else "ok" if mass < 23 else "fail")
    assert m["show_on_every_mass_screen"] and m["banner"]
    s = copy.deepcopy(DEFAULT_SETTINGS)
    s["limits"] = {"warn_mtow_kg": 20.0, "design_mtow_kg": 21.0, "legal_mtow_kg": 25.0}
    t = fs.mtow_thresholds(22.0, s, "x")
    assert t["level"] == "fail" and "design limit" in t["banner"]
    assert fs.mtow_thresholds(25.0, s, "x")["level"] == "fail"
    assert "legal" in fs.mtow_thresholds(25.0, s, "x")["banner"]
    assert fs.mtow_thresholds(19.0, s, "x")["level"] == "ok"


def test_motor_out_plain_quad_fails_and_recommends_octo(
    scaled_24: dict[str, Any], polar_cache: str
) -> None:
    p = copy.deepcopy(scaled_24["parameters"])
    p["layout"] = "quad_pusher"
    r = fs.run_fullscale_checks(p, scaled_24["mission"], DEFAULT_SETTINGS, cache_dir=polar_cache)
    mo = r["motor_out"]
    assert mo["level"] == "fail"
    assert len(mo["cases"]) == 8
    # no case holds heading: a fixed-motor quad cannot cancel the failed rotor's torque pair
    assert not any(c["holds_attitude_and_heading"] for c in mo["cases"])
    rec = mo["recommendation"]
    assert rec["needed"] and "octocopter" in rec["message"] and "X8" in rec["layout"]
    assert rec["x8"]["required_static_thrust_per_motor_n"] > 0
    assert "octocopter" in _check(r, "fullscale.motor_out")["message"]
    assert _check(r, "fullscale.motor_out")["level"] == "fail"


def test_motor_out_symmetric_quad_matches_known_result() -> None:
    """CG midway between the motors: with one motor out the diagonal partner idles and the
    other two carry half the weight each (attitude held while spinning); yaw cannot be held
    (Mueller & D'Andrea 2014)."""
    rotors = fs._rotors("quad", 0.0, 1.0, 0.5, 100.0, None)
    w = 100.0
    spin = fs._lp(rotors, 0, w, 0.5, 0.04, goal="util", yaw=False)
    assert spin["feasible"]
    t = spin["thrusts_n"]
    assert t["rear-left (motor 2)"] == pytest.approx(0.0, abs=1e-6)
    assert t["front-left (motor 3)"] == pytest.approx(50.0, rel=1e-6)
    assert t["rear-right (motor 4)"] == pytest.approx(50.0, rel=1e-6)
    assert spin["residual_yaw_nm"] == pytest.approx(0.04 * 100.0, rel=1e-6)
    assert not fs._lp(rotors, 0, w, 0.5, 0.04, goal="util")["feasible"]
    # intact: hover at a quarter each, any yaw within reach
    full = fs._lp(rotors, None, w, 0.5, 0.04, goal="util")
    assert full["utilisation"] == pytest.approx(0.25, rel=1e-6)
    # a coaxial X8 survives any single failure
    x8 = fs._rotors("x8", 0.0, 1.0, 0.5, 100.0, None)
    for j in range(8):
        assert fs._lp(x8, j, w, 0.5, 0.04, goal="util")["feasible"]


def test_motor_out_tilt_quad_reports_cases(result: dict[str, Any]) -> None:
    mo = result["motor_out"]
    assert mo["layout"].startswith("quad, front pair tilting")
    assert mo["tilt_yaw_angle_deg"] == pytest.approx(10.0)
    assert mo["level"] in ("fail", "warn")
    for c in mo["cases"]:
        assert c["message"].startswith("With the ")
        if c["holds_attitude_and_heading"]:
            th = c["thrusts_n"]
            tmax = mo["max_static_thrust_per_motor_n"]
            assert len(th) == 3 and all(-1e-6 <= v <= tmax + 1e-6 for v in th.values())
            w = c["mass_kg"] * 9.80665
            assert 0.99 * w <= sum(th.values()) <= 1.03 * w
    if mo["level"] == "fail":
        assert mo["recommendation"]["needed"]


def test_structure_with_catalogue_tubes(result: dict[str, Any]) -> None:
    st = result["structure"]
    spar = st["spar"]
    assert spar["root_moment_ultimate_nm"] > 100
    assert spar["catalogue"], "seed catalogue tubes are evaluated"
    assert all("margin" in r and "stress_mpa" in r for r in spar["catalogue"])
    if not any(r["ok"] for r in spar["catalogue"]):
        assert "No catalogued carbon tube is enough" in spar["message"]
        need = spar["needed_standard_tube"]
        assert need and need["margin"] >= 0.25 and need["outer_mm"] > 25
    booms = st["booms"]
    assert booms["catalogue"] and booms["full_thrust_n"] > 50
    if not any(r["ok"] for r in booms["catalogue"]):
        assert "No catalogued carbon tube is enough" in booms["message"]
        assert booms["needed_outer_mm"]["outer_mm"] >= 25
        assert booms["needed_outer_mm"]["deflection_mm"] <= 0.01 * booms["arm_mm"] + 1e-6


def test_landing_gear_load(result: dict[str, Any]) -> None:
    g = result["landing_gear"]
    n = 1 + 1.5**2 / (2 * 9.80665 * 0.5 * 0.075)
    assert g["load_factor"] == pytest.approx(n)
    mass = result["mtow"]["mass_kg"]
    assert g["total_load_n"] == pytest.approx(n * mass * 9.80665, rel=1e-3)
    assert g["load_per_attachment_n"] == pytest.approx(g["total_load_n"] / 4)
    assert g["sink_rate_mps"] == 1.5 and "Raymer" in g["source"]


def test_battery_current_at_hover(result: dict[str, Any]) -> None:
    b = result["battery"]
    a = result["analysis"]["battery"]
    assert b["hover_current_a"] == pytest.approx(a["hover_current"]["value"])
    assert b["limit_a"] == pytest.approx(0.8 * a["continuous_rating_a"])
    assert b["hover_current_a"] > 30  # 24 kg on 12S
    assert b["li_ion_alternatives"], "catalogue cells give Li-ion alternatives"
    for alt in b["li_ion_alternatives"]:
        assert alt["energy_wh"] >= a["pack"]["energy_wh"] - 1e-6
        assert alt["config"].startswith(f"{a['pack']['cells_series']}S")


def test_layup_feeds_structural_mass(result: dict[str, Any]) -> None:
    lay = result["layup"]
    keys = [p["key"] for p in lay["parts"]]
    assert keys == ["wing_skins", "wing_spar", "fuselage", "nose_bay", "tail", "wing_root_fairing"]
    for p in lay["parts"]:
        assert p["plies"] and p["mass_g"] > 0
        for ply in p["plies"]:
            assert ply["fabric_g_m2"] in (93.0, 160.0, 200.0, 300.0)
            assert ply["orientation"]
    assert any(p["core"] for p in lay["parts"])
    sm = lay["structural_mass"]
    assert sm["layup_total_g"] == pytest.approx(sum(p["mass_g"] for p in lay["parts"]), abs=1)
    assert 2000 < sm["layup_total_g"] < 12000
    assert sm["takeoff_mass_with_layup_kg"] == pytest.approx(
        result["mtow"]["mass_kg"] + sm["difference_g"] / 1000, abs=0.01
    )
    assert len(lay["sources"]) >= 4
    lvl = result["mtow_layup"]["level"]
    assert _check(result, "fullscale.mtow_layup")["level"] == lvl


def test_invalid_design_is_reported() -> None:
    p = copy.deepcopy(DEFAULT_DESIGN_PARAMETERS)
    p["wing"]["span_mm"] = -5
    r = fs.run_fullscale_checks(p, DEFAULT_MISSION, DEFAULT_SETTINGS)
    assert r["valid"] is False and r["checks"]


def test_seed_catalogue_loads() -> None:
    cat = fs.load_seed_catalogue()
    assert any(c["category"] == "carbon_tube" for c in cat)
    assert all("id" in c for c in cat)
    assert np.isfinite(fs.wing_root_fairing_radius_mm(690))
