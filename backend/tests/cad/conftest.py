"""Shared fixtures for the CAD tests: one full export of the default design per session, and
parameter sets for the layout/tail variants and a 3 m-span prototype."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from app.defaults import DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS


def design(**blocks: dict[str, Any]) -> dict[str, Any]:
    p = copy.deepcopy(DEFAULT_DESIGN_PARAMETERS)
    for name, values in blocks.items():
        if isinstance(values, dict):
            p[name] = {**p[name], **values}
        else:
            p[name] = values
    return p


def prototype_3m() -> dict[str, Any]:
    """A large prototype: 3 m span, 300 mm root chord (wider than the 240 mm envelope)."""
    return design(
        wing={"span_mm": 3000.0, "root_chord_mm": 300.0, "tip_chord_mm": 200.0, "x_le_mm": 380.0},
        fuselage={"length_mm": 1300.0, "width_mm": 150.0, "height_mm": 160.0},
        booms={
            "lateral_offset_mm": 450.0,
            "length_mm": 1000.0,
            "x_offset_mm": -320.0,
            "diameter_mm": 25.0,
        },
        motors={"front_x_mm": 50.0, "rear_x_mm": 950.0},
        tilt={"axis_x_mm": 50.0},
        propulsion={"prop_diameter_mm": 460.0},
        tail={"arm_mm": 900.0, "span_mm": 700.0, "chord_mm": 190.0, "height_mm": 250.0},
        nose_bay={"length_mm": 220.0, "width_mm": 130.0, "height_mm": 130.0},
        battery={"x_mm": 500.0, "capacity_mah": 10000.0},
        pusher={"prop_diameter_mm": 330.0, "x_mm": 1250.0},
    )


@pytest.fixture(scope="session")
def default_export(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Any], Path]:
    from app.cad import generate_files

    out = tmp_path_factory.mktemp("cad-default")
    calls: list[tuple[float, str]] = []
    manifest = generate_files(
        DEFAULT_DESIGN_PARAMETERS,
        DEFAULT_MISSION,
        DEFAULT_SETTINGS,
        out_dir=out,
        progress=lambda f, m: calls.append((f, m)),
        project={"project": "Test", "version": "v1", "date": "2026-01-01"},
    )
    manifest["_progress"] = calls
    return manifest, out
