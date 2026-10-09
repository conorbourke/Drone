"""Check thresholds and levels (pass/warn/fail) with synthetic states."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.engine.checks import build_checks


def _state(engine_settings: dict[str, Any], **over: Any) -> dict[str, Any]:
    pack = {
        "i_continuous_a": 125.0,
        "i_burst_a": 250.0,
        "c_continuous": 25.0,
        "capacity_ah": 5.0,
        "chemistry": "lipo",
        "cells_series": 6,
    }
    s = {
        "settings": engine_settings,
        "mission": {"target_endurance_min": 45.0},
        "mass_max_kg": 3.0,
        "mass_high_kg": 3.3,
        "sm_max": 0.10,
        "sm_min": 0.08,
        "envelope": [
            {
                "payload_g": 150 + 60 * i,
                "cg_x_mm": 360 - i,
                "static_margin": 0.08 + 0.005 * i,
                "front_share": 0.5,
                "mass_kg": 3.0,
            }
            for i in range(5)
        ],
        "cruise_to_stall": 1.5,
        "stall_speed": 10.0,
        "tw_total": 2.2,
        "tw_pair": 2.2,
        "hover_current": 20.0,
        "peak_current": 25.0,
        "peak_cell_v": 3.6,
        "pack": pack,
        "transition": None,
        "spar": {
            "level": "ok",
            "spar": "tube",
            "stress_mpa": 300,
            "load_factor": 3,
            "safety_factor": 1.5,
            "allowable_mpa": 500,
            "margin": 0.6,
            "tip_deflection_limit_mm": 50,
            "tip_deflection_fraction": 0.05,
        },
        "boom": {
            "level": "ok",
            "tube": "20 mm",
            "stress_mpa": 50,
            "critical_case": "thrust",
            "allowable_mpa": 500,
            "margin": 9.0,
            "min_diameter_mm": 10,
        },
        "trim_max": -1.0,
        "trim_min": -0.5,
        "trim_ok": True,
        "cruise_thrust_available": 10.0,
        "cruise_thrust_required": 3.0,
        "cruise_throttle": 0.6,
        "endurance": {"value": 50.0, "low": 40.0, "high": 60.0},
        "vtol_exceeds_usable": False,
        "cn_beta": 0.05,
        "cl_beta": -0.02,
        "geometry_statuses": [],
        "spar_sizing": None,
    }
    s.update(over)
    return s


def _levels(checks: list[dict[str, Any]]) -> dict[str, str]:
    return {c["key"]: c["level"] for c in checks}


def test_all_ok_and_sources(engine_settings: dict[str, Any]) -> None:
    checks = build_checks(_state(engine_settings))
    assert set(_levels(checks).values()) == {"ok"}
    for c in checks:
        assert c["message"] and c["threshold_source"], c["key"]
    keys = set(_levels(checks))
    for required in (
        "hover_thrust_to_weight",
        "static_margin_max_payload",
        "static_margin_min_payload",
        "cg_envelope",
        "stall_margin",
        "battery_current",
        "mtow_design",
        "mtow_legal",
        "spar_strength",
        "boom_strength",
    ):
        assert required in keys


@pytest.mark.parametrize(
    ("mass", "design", "legal"),
    [(22.0, "ok", "ok"), (23.5, "warn", "ok"), (24.5, "fail", "ok"), (25.5, "fail", "fail")],
)
def test_mass_limits(engine_settings: dict[str, Any], mass: float, design: str, legal: str) -> None:
    lv = _levels(build_checks(_state(engine_settings, mass_max_kg=mass, mass_high_kg=mass)))
    assert lv["mtow_design"] == design
    assert lv["mtow_legal"] == legal


@pytest.mark.parametrize(
    ("sm", "level"), [(-0.02, "fail"), (0.03, "warn"), (0.10, "ok"), (0.25, "warn")]
)
def test_static_margin(engine_settings: dict[str, Any], sm: float, level: str) -> None:
    assert (
        _levels(build_checks(_state(engine_settings, sm_min=sm)))["static_margin_min_payload"]
        == level
    )


@pytest.mark.parametrize(("ratio", "level"), [(0.9, "fail"), (1.2, "warn"), (1.4, "ok")])
def test_stall(engine_settings: dict[str, Any], ratio: float, level: str) -> None:
    assert (
        _levels(build_checks(_state(engine_settings, cruise_to_stall=ratio)))["stall_margin"]
        == level
    )


def test_battery_and_thrust(engine_settings: dict[str, Any]) -> None:
    lv = _levels(build_checks(_state(engine_settings, peak_current=110.0)))
    assert lv["battery_current"] == "warn"  # above 80 % of 125 A
    lv = _levels(build_checks(_state(engine_settings, hover_current=130.0)))
    assert lv["battery_current"] == "fail"
    lv = _levels(build_checks(_state(engine_settings, tw_total=1.8)))
    assert lv["hover_thrust_to_weight"] == "fail"


def test_cg_envelope_flags_out_of_range(engine_settings: dict[str, Any]) -> None:
    s = _state(engine_settings)
    env = copy.deepcopy(s["envelope"])
    env[0]["static_margin"] = -0.01
    assert _levels(build_checks({**s, "envelope": env}))["cg_envelope"] == "fail"
    env[0]["static_margin"] = 0.08
    env[0]["front_share"] = 0.7
    assert _levels(build_checks({**s, "envelope": env}))["cg_envelope"] == "warn"
