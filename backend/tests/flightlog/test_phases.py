"""Phase detection on synthetic flights (known truth) and on the SITL sample."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.flightlog import process_log
from tests.flightlog.conftest import LOGS

EXPECTED = ["takeoff_hover", "transition", "cruise", "back_transition", "landing_hover"]


def test_synthetic_phases_match_truth(synthetic_logs: list[dict[str, Any]]) -> None:
    for case in synthetic_logs:
        r = case["processed"]
        json.dumps(r, allow_nan=False)
        truth = case["truth"]["phases"]
        assert [p["key"] for p in r["phases"]] == EXPECTED
        # the back-transition ends when the airspeed falls below the hover limit, which the
        # synthetic deceleration (1.2 m/s²) reaches this much before standstill
        v_hover = r["phase_detection"]["thresholds"]["hover_max_airspeed_mps"]
        early = v_hover / 1.2
        for p in r["phases"]:
            t0, t1 = truth[p["key"]]
            if p["key"] == "landing_hover":
                t0 -= early
            if p["key"] == "back_transition":
                t1 -= early
            assert abs(p["start_s"] - t0) < 1.0, (p["key"], p["start_s"], t0)
            assert abs(p["end_s"] - t1) < 1.0, (p["key"], p["end_s"], t1)
            assert p["decided_by"]["start"] and p["decided_by"]["end"]
        tr = r["phases"][1]
        assert tr["decided_by"]["start"].startswith("MODE change to FBWA")
        assert tr["decided_by"]["end"].startswith("MSG 'Transition done'")
        assert r["phases"][3]["decided_by"]["start"].startswith("MODE change to QLAND")
        assert "airspeed" in r["phases"][3]["decided_by"]["end"]
        assert r["phase_detection"]["signals"]["lift"].startswith("VTOL motor outputs")


SITL = LOGS / "sitl_quadplane.bin"


@pytest.mark.skipif(not SITL.exists(), reason="SITL sample log not present")
def test_sitl_sample_phases() -> None:
    r = process_log(SITL)
    json.dumps(r, allow_nan=False)
    assert r["firmware"].startswith("ArduPlane V4")
    assert r["vehicle"]["configuration"] == "quadplane"
    keys = [p["key"] for p in r["phases"]]
    assert keys == EXPECTED, keys
    by = {p["key"]: p for p in r["phases"]}
    assert by["transition"]["decided_by"]["start"].startswith("MODE change to AUTO")
    assert by["transition"]["decided_by"]["end"].startswith("MSG 'Transition done'")
    assert by["back_transition"]["decided_by"]["start"].startswith("MODE change to QLAND")
    assert by["cruise"]["duration_s"] > 60
    assert by["cruise"]["airspeed_mps"]["median"] > 15
    assert by["takeoff_hover"]["altitude_m"]["max"] > 20
    assert by["landing_hover"]["altitude_m"]["end"] < 1.5
    for p in r["phases"]:
        assert p["power_w"]["mean"] > 0 and p["energy_wh"] > 0
        assert p["vibration"]["level"] in ("ok", "warn", "fail")
