"""Port of the Tier 1 mass model (docs/ENGINE.md reference outputs)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.engine.geometry import build_geometry
from app.engine.mass import carbon_tube_mass_per_m, cg_with_payload, solve_mass

FIXTURE = Path(__file__).resolve().parents[3] / "shared" / "fixtures" / "tier1_cases.json"


def _case(name: str) -> dict[str, Any]:
    return next(c for c in json.loads(FIXTURE.read_text())["cases"] if c["name"] == name)


@pytest.mark.parametrize(
    ("name", "mass_kg", "cg_max", "cg_min"),
    # Tier 1 reference outputs (docs/ENGINE.md); default battery at 290 mm since the Phase 3 review.
    [("default_prototype", 3.39, 339, 359), ("final_24kg", 22.1, 736, 805)],
)
def test_matches_tier1_reference_outputs(
    name: str, mass_kg: float, cg_max: float, cg_min: float, engine_settings: dict[str, Any]
) -> None:
    c = _case(name)
    g = build_geometry(c["parameters"], shapes="builtin")
    sol = solve_mass(g["parameters"], g, c["mission"], engine_settings)
    assert sol["result"]["converged"]
    assert sol["total_max_g"] / 1000 == pytest.approx(mass_kg, rel=0.01)
    assert sol["cg_max_x"] == pytest.approx(cg_max, abs=1.5)
    assert sol["cg_min_x"] == pytest.approx(cg_min, abs=1.5)


def test_starting_guess_does_not_change_the_answer(
    params: dict[str, Any], mission: dict[str, Any], engine_settings: dict[str, Any]
) -> None:
    g = build_geometry(params)
    a = solve_mass(params, g, mission, engine_settings, start_kg=1.0)
    b = solve_mass(params, g, mission, engine_settings, start_kg=9.0)
    assert a["total_max_g"] == pytest.approx(b["total_max_g"], abs=0.5)


def test_spar_override_and_payload_cg(
    params: dict[str, Any], mission: dict[str, Any], engine_settings: dict[str, Any]
) -> None:
    g = build_geometry(params)
    base = solve_mass(params, g, mission, engine_settings)
    heavy = solve_mass(
        params, g, mission, engine_settings, spar_tube={"outer_mm": 25, "wall_mm": 2}
    )
    assert heavy["total_max_g"] > base["total_max_g"]
    x, total = cg_with_payload(base, mission["payload_max_g"])
    assert x == pytest.approx(base["cg_max_x"])
    assert total == pytest.approx(base["total_max_g"])
    # Catalogue check from docs/ENGINE.md: a 20 x 18 mm tube is about 95 g/m.
    assert carbon_tube_mass_per_m(20) == pytest.approx(92.5, rel=0.05)
