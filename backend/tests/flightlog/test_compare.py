"""Predicted against measured: the re-evaluated predictions and the comparison rows."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.flightlog import compare_log, process_log
from app.flightlog.compare import Design
from app.flightlog.ocv import ocv_integral, ocv_per_cell, soc_from_ocv
from tests.flightlog.conftest import CRUISE_FACTOR, HOVER_FACTOR, LOGS

ROW_KEYS = {
    "key",
    "label",
    "phase",
    "unit",
    "kind",
    "predicted",
    "measured",
    "error_pct",
    "inside",
    "status",
    "explanation",
    "basis",
}


def test_design_reproduces_the_analysis_design_point(analysis: dict[str, Any]) -> None:
    d = Design(analysis)
    s = analysis["summary"]
    assert d.hover(d.mass_kg)["power_w"] == pytest.approx(s["hover_power"]["value"], rel=1e-6)
    assert d.cruise(d.v_design, d.mass_kg)["power_w"] == pytest.approx(
        s["cruise_power"]["value"], rel=1e-6
    )
    # heavier -> more hover power (about m^1.5); faster -> more cruise power at 20 m/s
    assert d.hover(d.mass_kg * 1.1)["power_w"] / d.hover(d.mass_kg)["power_w"] == pytest.approx(
        1.1**1.5, rel=0.05
    )
    assert d.cruise(20, d.mass_kg)["power_w"] > d.cruise(16, d.mass_kg)["power_w"]
    # the drag factor inverts the cruise model
    p = d.cruise(16, d.mass_kg, 1.3)["power_w"]
    assert d.drag_factor_for(16, d.mass_kg, p) == pytest.approx(1.3, rel=1e-4)


def test_ocv_curve_helpers() -> None:
    assert ocv_per_cell("lipo", 1.0) == 4.2 and ocv_per_cell("lipo", 0.0) == 3.27
    for v in (3.5, 3.8, 4.1):
        assert ocv_per_cell("lipo", soc_from_ocv("lipo", v)) == pytest.approx(v, abs=1e-9)
    assert 3.7 < ocv_integral("lipo", 0, 1) < 3.95


def test_comparison_rows(synthetic_logs: list[dict[str, Any]], analysis: dict[str, Any]) -> None:
    case = synthetic_logs[0]
    c = compare_log(case["processed"], analysis, logged_mass_kg=case["truth"]["mass_kg"])
    json.dumps(c, allow_nan=False)
    rows = {r["key"]: r for r in c["comparisons"]}
    for r in c["comparisons"]:
        assert set(r) >= ROW_KEYS
        if r["status"] != "unavailable":
            p = r["predicted"]
            assert p["low"] <= p["value"] <= p["high"] or r["kind"] == "lower_bound"
            assert r["explanation"]
    assert {
        "hover_power",
        "hover_current",
        "transition_peak_power",
        "cruise_power",
        "endurance_cruise",
        "transition_speed",
        "stall_speed",
    } <= set(rows)
    assert sum(1 for k in rows if k.startswith("energy_")) == 5
    assert rows["hover_power"]["error_pct"] == pytest.approx((HOVER_FACTOR - 1) * 100, abs=2)
    assert rows["cruise_power"]["error_pct"] == pytest.approx((CRUISE_FACTOR - 1) * 100, abs=2)
    # the cruise prediction was re-evaluated at the measured airspeed and the weighed mass
    assert c["conditions"]["cruise_airspeed_mps"] == pytest.approx(16.0, abs=0.2)
    assert c["mass_source"] == "weighed (given)"
    # more cruise power than predicted -> less endurance than predicted
    assert rows["endurance_cruise"]["error_pct"] < -5


def test_outside_rows_explain_causes(
    synthetic_logs: list[dict[str, Any]], analysis: dict[str, Any]
) -> None:
    proc = synthetic_logs[0]["processed"]
    # pretend the aircraft was much lighter than weighed: hover power is then far too high
    c = compare_log(proc, analysis, logged_mass_kg=2.0)
    row = next(r for r in c["comparisons"] if r["key"] == "hover_power")
    assert row["status"] == "outside" and row["inside"] is False
    assert row["explanation"].startswith("Measured above the predicted range")
    assert "heavier" in row["explanation"]


def test_without_weighed_mass_uses_predicted_mass(
    synthetic_logs: list[dict[str, Any]], analysis: dict[str, Any]
) -> None:
    c = compare_log(synthetic_logs[0]["processed"], analysis)
    assert c["mass_kg"] == analysis["mass"]["takeoff_max_payload"]["value"]
    assert c["notes"] and "Weigh" in c["notes"][0]


def test_invalid_analysis_is_refused(synthetic_logs: list[dict[str, Any]]) -> None:
    with pytest.raises(ValueError):
        compare_log(synthetic_logs[0]["processed"], {"valid": False})


SITL = LOGS / "sitl_quadplane.bin"


@pytest.mark.skipif(not SITL.exists(), reason="SITL sample log not present")
def test_sitl_sample_against_a_close_design(analysis: dict[str, Any]) -> None:
    """The SITL quadplane is a different (simulated) aircraft; the point is that every row is
    computed and outside rows carry explanations."""
    proc = process_log(SITL)
    c = compare_log(proc, analysis, logged_mass_kg=4.5)
    rows = {r["key"]: r for r in c["comparisons"]}
    assert rows["hover_power"]["measured"] > 0 and rows["cruise_power"]["measured"] > 0
    assert rows["cruise_power"]["status"] in ("inside", "outside")
    for r in c["comparisons"]:
        if r["status"] == "outside":
            assert "Likely causes" in r["explanation"] or r["kind"] == "lower_bound"
    # a 3S SITL pack against a 6S design: the battery factor is refused with a reason
    assert c["factors"]["battery_usable_energy"]["valid"] is False


def test_stored_synthetic_fixture(analysis: dict[str, Any]) -> None:
    """The committed synthetic log (make_synthetic.py) still compares as it was made."""
    truth = json.loads((LOGS / "synthetic_quadplane.json").read_text())
    proc = process_log(LOGS / "synthetic_quadplane.bin")
    assert [p["key"] for p in proc["phases"]] == list(truth["phases"])
    c = compare_log(proc, analysis, logged_mass_kg=truth["mass_kg"])
    f = c["factors"]
    # loose bounds: the engine may have moved since the fixture was written
    assert f["hover_power"]["value"] == pytest.approx(truth["hover_factor"], abs=0.04)
    assert f["cruise_power"]["value"] == pytest.approx(truth["cruise_factor"], abs=0.04)
