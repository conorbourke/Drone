"""Transition sweep: margins positive, sensible trends; the transition margin and the separate
top-speed thrust margin are each reported over their own speed range and match the checks."""

from __future__ import annotations

import itertools
from dataclasses import replace
from typing import Any

import pytest

from app.engine import battery as bat
from app.engine.propulsion import generic_propeller, size_generic_motor
from app.engine.transition import (
    SWEEP_END_FACTOR,
    TOP_SPEED_MARGIN_MIN,
    TRANSITION_END_BUFFER,
    transition_sweep,
)


def _run(
    layout: str = "front_tilt", ct: float = 1.0, mass: float = 3.3, pitch_mm: float = 140
) -> dict:
    prop = generic_propeller(330, pitch_mm, 2)
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


@pytest.mark.parametrize("layout", ["front_tilt", "rear_tilt", "quad_pusher"])
def test_sweep_monotonic_and_margins_on_their_own_ranges(layout: str) -> None:
    tr = _run(layout)
    pts = tr["points"]
    speeds = [p["speed_mps"] for p in pts]
    assert speeds[0] == 0.0
    assert speeds[-1] == pytest.approx(SWEEP_END_FACTOR * 16.0)
    assert all(b > a for a, b in itertools.pairwise(speeds))  # strictly increasing
    v_end = tr["transition_end_speed_mps"]
    assert v_end == pytest.approx(TRANSITION_END_BUFFER * tr["speed_wing_100pct_mps"])
    assert any(s == pytest.approx(v_end) for s in speeds)  # the range ends on a sweep point
    assert tr["transition_complete_within_sweep"] is True
    if layout != "quad_pusher":
        assert tr["tilt_complete_speed_mps"] <= tr["speed_wing_100pct_mps"] + 1e-9
    # Transition margin: defined exactly on hover..end of transition; min and its speed match.
    for p in pts:
        inside = p["speed_mps"] <= v_end + 1e-9
        assert (p["thrust_margin"] is not None) == inside, p["speed_mps"]
        assert p["phase"] == ("transition" if inside else "wing_borne")
    tm = [(p["thrust_margin"], p["speed_mps"]) for p in pts if p["thrust_margin"] is not None]
    assert tr["min_thrust_margin"] == min(tm)[0]
    assert tr["min_margin_speed_mps"] == min(tm)[1]
    # Top-speed margin: defined on wing-borne points only; min and its speed match.
    fwd = [
        (p["forward_thrust_margin"], p["speed_mps"])
        for p in pts
        if p["forward_thrust_margin"] is not None
    ]
    assert fwd and all(p["wing_borne"] for p in pts if p["forward_thrust_margin"] is not None)
    top = tr["top_speed"]
    assert (top["margin_min"], top["margin_min_speed_mps"]) == min(fwd)
    assert top["margin_at_sweep_end"] == pts[-1]["forward_thrust_margin"]
    assert top["required_margin"] == TOP_SPEED_MARGIN_MIN


def test_hover_propellers_limit_top_speed() -> None:
    """Low-pitch hover propellers tilted forward unload at high advance ratio: the top-speed
    margin fails to reach 1.0 at 1.3 x cruise and is a warning, while the transition itself is
    fine."""
    tr = _run("front_tilt")
    top = tr["top_speed"]
    assert tr["level"] == "ok" and tr["min_thrust_margin"] > 1.3
    assert top["margin_at_sweep_end"] < 1.0
    assert top["level"] == "warn"
    assert tr["top_speed_limited_within_sweep"] is True
    assert 16.0 < tr["top_speed_mps"] < SWEEP_END_FACTOR * 16.0
    # Advance ratio at the sweep end is close to the propeller's zero-thrust value.
    assert 0.85 * top["zero_thrust_advance_ratio"] < top["advance_ratio_at_sweep_end"]
    assert top["advance_ratio_at_sweep_end"] < top["zero_thrust_advance_ratio"]
    # A higher-pitch propeller unloads later and clears the top-speed margin.
    steep = _run("front_tilt", pitch_mm=250)["top_speed"]
    assert steep["margin_min"] > top["margin_min"]
    # The pusher (cruise-pitch propeller) has a large top-speed reserve.
    pusher = _run("quad_pusher")["top_speed"]
    assert pusher["level"] == "ok" and pusher["margin_min"] > TOP_SPEED_MARGIN_MIN


def test_default_design_result_matches_its_checks(default_full: dict[str, Any]) -> None:
    tr = default_full["transition"]
    pts = tr["points"]
    checks = {c["key"]: c for c in default_full["checks"]}
    # The quantity the UI reads, the summary, the check and the chart marker agree.
    tm = [(p["thrust_margin"], p["speed_mps"]) for p in pts if p["thrust_margin"] is not None]
    assert tr["min_margin"]["value"] == pytest.approx(min(tm)[0])
    assert tr["min_thrust_margin"] == pytest.approx(min(tm)[0])
    assert tr["min_margin_speed_mps"] == min(tm)[1]
    assert default_full["summary"]["transition_min_margin"]["value"] == tr["min_thrust_margin"]
    assert checks["transition_margin"]["value"] == tr["min_thrust_margin"]
    assert checks["transition_margin"]["level"] == tr["level"] == "ok"
    # Default design: the transition is limited at hover (about 2.2); above cruise speed the
    # tilted hover propellers run out of thrust near 1.3 x cruise: a separate warning.
    assert tr["min_margin_speed_mps"] == 0.0
    assert 1.8 < tr["min_thrust_margin"] < 2.6
    assert tr["transition_end_speed_mps"] < 16.0
    top = tr["top_speed"]
    fwd = [p["forward_thrust_margin"] for p in pts if p["forward_thrust_margin"] is not None]
    assert top["margin_min"] == min(fwd)
    assert tr["top_speed_margin"]["value"] == top["margin_min"]
    assert default_full["summary"]["top_speed_thrust_margin"]["value"] == top["margin_min"]
    assert checks["top_speed_margin"]["value"] == top["margin_min"]
    assert checks["top_speed_margin"]["level"] == top["level"] == "warn"
    assert 0.9 < top["margin_at_sweep_end"] < TOP_SPEED_MARGIN_MIN
    assert "unloading" in checks["top_speed_margin"]["message"]
    assert checks["cruise_thrust"]["level"] == "ok"  # cruise itself is fine
