"""Per-phase statistics: energy integration, steady subsets, vibration and motor balance."""

from __future__ import annotations

from typing import Any

import pytest

from app.flightlog import process_log
from app.flightlog.stats import VIBE_FAIL, VIBE_WARN, vibration_level
from tests.flightlog.conftest import LOGS


def test_energy_integration_matches_truth_and_autopilot(
    synthetic_logs: list[dict[str, Any]],
) -> None:
    for case in synthetic_logs:
        r = case["processed"]
        ec = r["energy_check"]
        truth = case["truth"]
        # the synthetic battery integrates at 50 Hz, the log has 10 Hz samples
        assert abs(ec["integrated_wh"] - truth["energy_wh"]) / truth["energy_wh"] < 0.01
        assert abs(ec["integrated_mah"] - truth["charge_mah"]) / truth["charge_mah"] < 0.01
        assert ec["agrees"] is True and abs(ec["diff_wh_pct"]) < 1.0
        # phases tile the flight: their energies add up to the flight's
        total = sum(p["energy_wh"] for p in r["phases"])
        assert total == pytest.approx(r["flight"]["energy_wh"], rel=0.002)
        # mean power x duration = energy for every phase
        for p in r["phases"]:
            assert p["power_w"]["mean"] * p["duration_s"] / 3600 == pytest.approx(
                p["energy_wh"], rel=0.01
            )


def test_steady_subsets_remove_climbs_and_turns(synthetic_logs: list[dict[str, Any]]) -> None:
    case = synthetic_logs[0]
    truth = case["truth"]
    ph = {p["key"]: p for p in case["processed"]["phases"]}
    hover_true = truth["hover_power_w"]
    to = ph["takeoff_hover"]["power_w"]
    # the climb raises the take-off mean; the steady part is the hover itself
    assert to["mean"] > hover_true * 1.05
    assert to["steady_mean"] == pytest.approx(hover_true, rel=0.03)
    cr = ph["cruise"]["power_w"]
    assert cr["steady_mean"] == pytest.approx(truth["cruise_power_w"], rel=0.02)
    assert cr["steady_s"] < ph["cruise"]["duration_s"] - 10  # two 6 s turns removed
    assert ph["cruise"]["airspeed_mps"]["steady_median"] == pytest.approx(
        truth["cruise_airspeed_mps"], abs=0.2
    )


def test_vibration_and_motor_balance(synthetic_logs: list[dict[str, Any]]) -> None:
    ph = {p["key"]: p for p in synthetic_logs[0]["processed"]["phases"]}
    v = ph["takeoff_hover"]["vibration"]
    assert v["level"] == "ok" and v["clipping"] == 0 and 8 < v["z"]["mean"] < 16
    m = ph["takeoff_hover"]["motors"]
    # front pair carries 60 % (design hover share): front motors work harder
    assert m["front_minus_rear_fraction"] > 0.05
    assert "front" in m["hints"][0]
    assert ph["takeoff_hover"]["esc"]["esc0"]["rpm_mean"] > 0


def test_vibration_thresholds_follow_ardupilot_guidance() -> None:
    assert (VIBE_WARN, VIBE_FAIL) == (30.0, 60.0)
    assert vibration_level(12) == "ok"
    assert vibration_level(45) == "warn"
    assert vibration_level(75) == "fail"


SITL = LOGS / "sitl_quadplane.bin"


@pytest.mark.skipif(not SITL.exists(), reason="SITL sample log not present")
def test_sitl_energy_against_bat_totals() -> None:
    r = process_log(SITL)
    ec = r["energy_check"]
    assert ec["logged_wh"] > 0 and ec["logged_mah"] > 0
    assert ec["agrees"] is True, ec
    total = sum(p["energy_wh"] for p in r["phases"])
    assert total == pytest.approx(r["flight"]["energy_wh"], rel=0.01)
