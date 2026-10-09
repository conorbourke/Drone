from __future__ import annotations

from app.flightlog.logging_guide import log_bitmask, logging_guide, quadplane_log_bitmask


def test_quadplane_log_bitmask() -> None:
    bm = quadplane_log_bitmask()
    # fast + medium attitude, GPS, PM, CTUN, NTUN, IMU, CMD, battery, TECS, RC in/out
    assert bm["value"] == 1 + 2 + 4 + 8 + 16 + 32 + 128 + 256 + 512 + 2048 + 8192 == 11199
    assert bm["computation"].endswith("= 11199")
    assert log_bitmask([0, 0, 1]) == 3


def test_guide_lists_every_parameter_with_a_reason() -> None:
    g = logging_guide()
    names = " ".join(p["param"] for p in g["parameters"])
    for p in (
        "LOG_BITMASK",
        "LOG_DISARMED",
        "BATT_MONITOR",
        "BATT_CAPACITY",
        "SERVO_BLH_TRATE",
        "ARSPD_TYPE",
        "Q_TILT",
    ):
        assert p in names
    assert all(p["reason"] for p in g["parameters"])
    assert any("Mission Planner" in s for s in g["download"])
    assert any("QGroundControl" in s for s in g["download"])
