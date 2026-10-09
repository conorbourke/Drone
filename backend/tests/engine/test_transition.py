"""Transition sweep: margins positive, sensible trends."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.engine import battery as bat
from app.engine.propulsion import generic_propeller, size_generic_motor
from app.engine.transition import transition_sweep


def _run(layout: str = "front_tilt", ct: float = 1.0, mass: float = 3.3) -> dict:
    prop = generic_propeller(330, 140, 2)
    motor = size_generic_motor(prop, 2 * 3.3 * 9.81 * 1.03 * 0.55 / 2, 21.0)
    prop = replace(prop, ct_factor=ct)
    pack = bat.pack_model(
        {"chemistry": "lipo", "cells_series": 6, "cells_parallel": 1, "capacity_mah": 5000}
    )
    pusher = generic_propeller(254, 178, 2) if layout == "quad_pusher" else None
    pmotor = size_generic_motor(pusher, 0.5 * mass * 9.81, 21.0) if pusher else None
    return transition_sweep(
        layout,
        mass,
        0.396,
        1.05,
        0.04,
        0.95,
        8.18,
        16.0,
        0.5,
        90.0,
        prop,
        motor,
        pack,
        1.3,
        pusher,
        pmotor,
        8.0,
    )


@pytest.mark.parametrize("layout", ["front_tilt", "rear_tilt", "quad_pusher"])
def test_margins_positive_and_wing_takes_over(layout: str) -> None:
    tr = _run(layout)
    assert tr["min_thrust_margin"] > 1.0
    fractions = [p["wing_lift_fraction"] for p in tr["points"]]
    assert fractions == sorted(fractions)  # monotonic: the wing takes over as speed rises
    assert fractions[0] == 0.0 and fractions[-1] == pytest.approx(1.0)
    assert tr["speed_wing_80pct_mps"] < tr["speed_wing_100pct_mps"] < 16.0
    assert tr["level"] in ("ok", "warn")
    assert tr["energy_wh"] > 0 and tr["duration_s"] == pytest.approx(16.0)
    if layout != "quad_pusher":
        tilts = [p["tilt_deg"] for p in tr["points"]]
        assert tilts[-1] == pytest.approx(90.0)
        assert max(tilts) <= 90.0 + 1e-9


def test_weaker_propellers_lower_the_margin() -> None:
    strong = _run(ct=1.0)["min_thrust_margin"]
    weak = _run(ct=0.7)["min_thrust_margin"]
    assert weak < strong


def test_too_heavy_fails() -> None:
    tr = _run(mass=8.0)
    assert tr["min_thrust_margin"] < 1.0
    assert tr["level"] == "fail"
