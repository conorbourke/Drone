"""Structure checks against hand beam calculations."""

from __future__ import annotations

import math
from typing import Any

import pytest

from app.engine.geometry import build_geometry
from app.engine.structure import (
    TUBE_ALLOWABLE_PA,
    TUBE_E_PA,
    bending_moment,
    boom_check,
    span_load,
    tip_deflection,
    tube_section,
    wing_spar_check,
)


def _uniform_strips(semi: float, n: int = 200) -> list[dict[str, float]]:
    w = semi / n
    return [{"y": (i + 0.5) * w, "width": w, "ccl": 1.0} for i in range(n)] + [
        {"y": -(i + 0.5) * w, "width": w, "ccl": 1.0} for i in range(n)
    ]


def test_uniform_load_cantilever_by_hand() -> None:
    semi = 1.0
    lift = 100.0  # N on both sides: 50 N per side, q = 50 N/m
    load = span_load(_uniform_strips(semi), lift)
    q = lift / 2 / semi
    assert bending_moment(load) == pytest.approx(q * semi**2 / 2, rel=1e-4)
    sec = tube_section(12, 1)
    ei = TUBE_E_PA * sec["I_m4"]
    assert tip_deflection(load, ei, semi, 200) == pytest.approx(q * semi**4 / (8 * ei), rel=0.02)
    d_o, d_i = 0.012, 0.010
    assert sec["I_m4"] == pytest.approx(math.pi * (d_o**4 - d_i**4) / 64)


def test_wing_spar_stress_and_margin(params: dict[str, Any]) -> None:
    g = build_geometry(params)
    semi = 0.9
    spar = {"kind": "tube", "outer_mm": 12.0, "wall_mm": 1.0}
    out = wing_spar_check(g, spar, _uniform_strips(semi), 30.0, 1.0, 3.0, 1.5)
    m = (3 * 30 * 1.5 / 2 / semi) * semi**2 / 2
    sec = tube_section(12, 1)
    stress = m * sec["c_m"] / sec["I_m4"]
    assert out["root_moment_ultimate_nm"] == pytest.approx(m, rel=1e-3)
    assert out["stress_mpa"] == pytest.approx(stress / 1e6, rel=1e-3)
    assert out["margin"] == pytest.approx(TUBE_ALLOWABLE_PA / stress - 1, rel=1e-3)
    assert out["level"] == (
        "ok" if out["margin"] >= 0.25 else "warn" if out["margin"] >= 0 else "fail"
    )


def test_boom_full_thrust_by_hand(params: dict[str, Any]) -> None:
    g = build_geometry(params)
    params["landing_gear"]["type"] = "none"
    out = boom_check(params, g, 20.0, 32.0, 1.5)
    assert out["moment_thrust_ultimate_nm"] == pytest.approx(20.0 * out["arm_mm"] / 1000 * 1.5)
    sec = tube_section(20, 1)
    assert out["stress_mpa"] == pytest.approx(
        out["moment_thrust_ultimate_nm"] * sec["c_m"] / sec["I_m4"] / 1e6
    )
    assert out["level"] == "ok"
