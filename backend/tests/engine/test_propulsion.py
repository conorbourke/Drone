"""Motor + propeller operating points against hand calculations."""

from __future__ import annotations

import math

import pytest

from app.engine.propulsion import (
    Motor,
    fit_thrust_data,
    generic_propeller,
    max_thrust,
    operating_point,
    size_generic_motor,
)
from app.engine.quantity import RHO_SL


def test_static_point_by_hand() -> None:
    prop = generic_propeller(330, 140, 2)
    motor = Motor(kv_rpm_per_v=400, r_ohm=0.1, i0_a=0.5, i_max_a=20)
    op = operating_point(prop, motor, thrust_n=8.0, airspeed=0.0, bus_voltage=22.0)
    d = 0.33
    n = math.sqrt(8.0 / (prop.ct0 * RHO_SL * d**4))  # T = CT0 rho n^2 D^4
    assert op["n"] == pytest.approx(n, rel=1e-5)
    q = prop.cp0 * RHO_SL * n**2 * d**5 / (2 * math.pi)  # Q = P / omega
    kt = 60 / (2 * math.pi * 400)
    i = q / kt + 0.5
    v = i * 0.1 + 2 * math.pi * n / (400 * 2 * math.pi / 60)
    assert op["current_a"] == pytest.approx(i, rel=1e-5)
    assert op["motor_voltage_v"] == pytest.approx(v, rel=1e-5)
    assert op["throttle"] == pytest.approx(v / 22.0, rel=1e-5)
    assert op["shaft_power_w"] == pytest.approx((i - 0.5) * kt * 2 * math.pi * n, rel=1e-5)
    assert op["battery_power_w"] == pytest.approx(v * i / 0.95, rel=1e-5)
    assert 0.6 < prop.figure_of_merit() < 0.7


def test_forward_flight_efficiency_and_max_thrust() -> None:
    prop = generic_propeller(330, 140, 2)
    motor = size_generic_motor(prop, max_thrust_n=18.0, bus_voltage=21.0)
    static = max_thrust(prop, motor, 0.0, 21.0)
    assert static["thrust_n"] == pytest.approx(18.0, rel=0.03)  # sized for it
    op = operating_point(prop, motor, 2.0, 16.0, 21.0)
    assert op["thrust_n"] == pytest.approx(2.0, rel=1e-4)
    assert op["j"] == pytest.approx(16.0 / (op["n"] * 0.33))
    assert 0 < op["eta_prop"] < 0.7
    assert max_thrust(prop, motor, 16.0, 21.0)["thrust_n"] < static["thrust_n"]
    # A higher pitch propeller is more efficient at the same cruise thrust.
    hi = generic_propeller(330, 190, 2)
    assert operating_point(hi, motor, 2.0, 16.0, 21.0)["eta_prop"] > op["eta_prop"]


def test_generic_motor_hovers_at_the_implied_throttle() -> None:
    prop = generic_propeller(330, 140, 2)
    motor = size_generic_motor(prop, max_thrust_n=16.0, bus_voltage=21.0)
    hover = operating_point(prop, motor, 8.0, 0.0, 21.0)  # thrust-to-weight 2
    assert 0.55 < hover["throttle"] < 0.8


def test_fit_from_thrust_data() -> None:
    d = 0.381
    ct0 = 0.11
    points = []
    for rpm in (3000, 4000, 5000):
        n = rpm / 60
        thrust_n = ct0 * RHO_SL * n * n * d**4
        points.append(
            {
                "rpm": rpm,
                "thrust_g": thrust_n / 9.80665 * 1000,
                "current_a": 5.0,
                "voltage_v": 22.2,
                "throttle_pct": 50,
                "power_w": 100,
                "prop": "15x5",
            }
        )
    fit = fit_thrust_data(points, 381, Motor(400, 0.1, 0.5, 30))
    assert fit is not None
    assert fit["ct0"] == pytest.approx(ct0, rel=1e-6)
    assert fit["points"] == 3
