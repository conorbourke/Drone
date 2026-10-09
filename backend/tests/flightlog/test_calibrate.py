"""Calibration: the known synthetic factors are recovered, logs combine by inverse variance,
built weights give the structural factor."""

from __future__ import annotations

import math
from typing import Any

import pytest

from app.flightlog import compare_log, derive_calibration
from app.flightlog.calibrate import combine
from app.flightlog.compare import Design
from app.flightlog.ocv import ocv_integral
from tests.flightlog.conftest import CRUISE_FACTOR, HOVER_FACTOR


@pytest.fixture(scope="module")
def comparisons(
    synthetic_logs: list[dict[str, Any]], analysis: dict[str, Any]
) -> list[dict[str, Any]]:
    return [
        compare_log(c["processed"], analysis, logged_mass_kg=c["truth"]["mass_kg"])
        for c in synthetic_logs
    ]


def test_recovers_known_factors(
    comparisons: list[dict[str, Any]], analysis: dict[str, Any]
) -> None:
    cal = derive_calibration(comparisons, log_ids=[11, 12, 13])
    f = cal["factors"]
    assert cal["n_logs"] == 3
    hp, cp = f["hover_power"], f["cruise_power"]
    assert hp["valid"] and hp["n_logs"] == 3 and hp["source_logs"] == [11, 12, 13]
    assert hp["value"] == pytest.approx(HOVER_FACTOR, abs=0.015)
    assert cp["value"] == pytest.approx(CRUISE_FACTOR, abs=0.015)
    # truth inside the stated (one-sigma) band, which is narrower than any single log's
    assert abs(hp["value"] - HOVER_FACTOR) < 2 * hp["uncertainty"]
    assert abs(cp["value"] - CRUISE_FACTOR) < 2 * cp["uncertainty"]
    assert hp["uncertainty"] < min(p["uncertainty"] for p in hp["per_log"])
    # the drag factor is the power ratio corrected for propulsive efficiency: re-running the
    # cruise model with it reproduces +12 % power, and it is larger than 1.12 because the
    # hover-sized tilt propellers lose efficiency as thrust rises at this advance ratio
    d = Design(analysis)
    k = f["cruise_drag"]["value"]
    assert k > CRUISE_FACTOR
    for comp in comparisons:
        v = comp["conditions"]["cruise_airspeed_mps"]
        ratio = d.cruise(v, comp["mass_kg"], k)["power_w"] / d.cruise(v, comp["mass_kg"])["power_w"]
        assert ratio == pytest.approx(CRUISE_FACTOR, abs=0.03)
    # the synthetic pack follows the typical LiPo curve; the design model counts 3.7 V/cell,
    # so a healthy pack shows the curve's mean over the nominal (about 1.04)
    expected = ocv_integral("lipo", 0, 1) / 3.7
    assert f["battery_usable_energy"]["value"] == pytest.approx(expected, abs=0.02)
    assert f["structural_mass"]["valid"] is False


def test_single_log_and_missing_factors(comparisons: list[dict[str, Any]]) -> None:
    one = derive_calibration(comparisons[:1])
    assert one["factors"]["hover_power"]["n_logs"] == 1
    assert one["factors"]["hover_power"]["birge_ratio"] == 1.0
    none = derive_calibration([])
    assert none["factors"]["hover_power"]["valid"] is False


def test_combine_inverse_variance_and_scatter() -> None:
    c = combine([(1.0, 0.1), (1.2, 0.1)])
    assert c["value"] == pytest.approx(1.1)
    assert c["internal_uncertainty"] == pytest.approx(0.1 / math.sqrt(2))
    # the two disagree by 2 sigma-of-difference: scatter governs
    assert c["uncertainty"] == pytest.approx(
        max(c["internal_uncertainty"], c["scatter_uncertainty"])
    )
    w = combine([(1.0, 0.05), (2.0, 0.5)])
    assert w["value"] == pytest.approx((1.0 / 0.0025 + 2.0 / 0.25) / (1 / 0.0025 + 1 / 0.25))


def test_structural_mass_from_built_weights(comparisons: list[dict[str, Any]]) -> None:
    built = [
        {"group": "printed shell", "predicted_g": 420.0, "measured_g": 470.0},
        {"group": "wing", "predicted_g": 380.0, "measured_g": 400.0},
        {"group": "wing", "predicted_g": 100.0, "measured_g": 98.0},
        {"group": "booms", "predicted_g": 90.0, "measured_g": None},
    ]
    cal = derive_calibration(comparisons, built_weights=built)
    sm = cal["factors"]["structural_mass"]
    assert sm["valid"] and sm["source"] == "built weights"
    assert sm["value"] == pytest.approx(968 / 900)
    assert sm["per_group"]["wing"]["factor"] == pytest.approx(498 / 480)
    assert "booms" not in sm["per_group"]
    assert 0 < sm["uncertainty"] < 0.02
