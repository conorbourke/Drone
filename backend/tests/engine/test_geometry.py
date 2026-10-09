"""Python geometry reproduces the Tier 1 golden fixture to 0.1 % (docs/ENGINE.md)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from app.engine.geometry import build_geometry, sweep_at, trapezoid

FIXTURE = Path(__file__).resolve().parents[3] / "shared" / "fixtures" / "tier1_cases.json"


def _diff(expected: Any, actual: Any, path: str, rel: float, out: list[str]) -> None:
    if isinstance(expected, (bool, str)) or expected is None:
        if expected != actual:
            out.append(f"{path}: expected {expected!r}, got {actual!r}")
    elif isinstance(expected, (int, float)):
        tol = max(rel * abs(expected), 1e-6)
        if not isinstance(actual, (int, float)) or abs(expected - actual) > tol:
            out.append(f"{path}: expected {expected}, got {actual}")
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            out.append(f"{path}: list length differs")
            return
        for i, (e, a) in enumerate(zip(expected, actual, strict=True)):
            _diff(e, a, f"{path}[{i}]", rel, out)
    elif isinstance(expected, dict):
        if not isinstance(actual, dict):
            out.append(f"{path}: missing object")
            return
        for k, v in expected.items():
            _diff(v, actual.get(k), f"{path}.{k}", rel, out)


def _cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE.read_text())["cases"]


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_reproduces_tier1_fixture_geometry(case: dict[str, Any]) -> None:
    doc = json.loads(FIXTURE.read_text())
    assert doc["tolerance_relative"] == 0.001
    geometry = build_geometry(case["parameters"], shapes="builtin")
    problems: list[str] = []
    _diff(case["geometry"], geometry, case["name"], 0.001, problems)
    assert problems == []


def test_fixture_has_the_three_cases() -> None:
    assert [c["name"] for c in _cases()] == ["default_prototype", "final_24kg", "quad_pusher"]


def test_trapezoid_against_hand_formulas() -> None:
    # Rectangular wing: MAC equals the chord, at the quarter span.
    r = trapezoid(2.0, 0.25, 0.25)
    assert r["area"] == pytest.approx(0.5)
    assert r["ar"] == pytest.approx(8.0)
    assert r["mac"] == pytest.approx(0.25)
    assert r["mac_y"] == pytest.approx(0.5)
    # Tapered wing (Raymer ch. 4): MAC = 2/3 cr (1 + l + l^2) / (1 + l).
    t = trapezoid(1.8, 0.26, 0.18)
    lam = 0.18 / 0.26
    assert t["mac"] == pytest.approx(2 / 3 * 0.26 * (1 + lam + lam * lam) / (1 + lam))
    assert t["area"] == pytest.approx(0.396)
    assert math.degrees(sweep_at(0.25, 0.0, t["ar"], t["taper"])) < 0  # taper sweeps c/4 forward


def test_library_shapes_use_the_coordinates(params: dict[str, Any]) -> None:
    g = build_geometry(params)
    assert g["wing"]["shape_origin"] == "summary"
    assert 0.08 < g["wing"]["thickness_ratio"] < 0.10
