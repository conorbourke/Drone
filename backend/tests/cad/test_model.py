"""The CAD model agrees with the Python geometry module (Phase 5 contract section 1)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.cad import geometry_agreement
from app.cad import parts as P
from app.cad import split as S
from app.cad.model import (
    SPAR_CHORD_FRACTION,
    SPAR_CLEARANCE_MM,
    CadError,
    build_model,
    load_profile,
    parse_mount_pattern,
)
from app.defaults import DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS
from tests.cad.conftest import design


def test_default_wing_matches_geometry_module(default_export):
    manifest, _ = default_export
    agreement = manifest["checks"]["geometry_agreement"]
    assert agreement["max_diff_mm"] <= 0.5, agreement
    plan = agreement["planform"]
    for key in ("span_mm", "root_chord_mm", "tip_chord_mm", "mac_mm", "mac_y_mm", "ac_x_mm"):
        assert plan[key]["diff_mm"] <= 0.5, (key, plan[key])
    assert plan["area_m2"]["mean_chord_diff_mm"] <= 0.5


def test_swept_twisted_wing_matches_geometry_module():
    """Sweep, twist, dihedral and incidence are applied as in the geometry module."""
    p = design(
        wing={
            "sweep_deg": 12.0,
            "twist_deg": -3.0,
            "dihedral_deg": 6.0,
            "incidence_deg": 4.0,
            "airfoil": "e387",
        },
        booms={"lateral_offset_mm": 330.0},
    )
    model = build_model(p, DEFAULT_MISSION, DEFAULT_SETTINGS)
    spec = next(s for s in P.part_specs(model) if s.key == "wing_right")
    pieces, _ = S.make_pieces(model, spec)
    agreement = geometry_agreement(model, pieces)
    assert agreement["max_diff_mm"] <= 0.5, agreement
    # The tip leading edge sits where build_geometry puts it (before the incidence rotation).
    tip = model.geometry["wing"]["tip_le"]
    surf = model.surfaces["wing_right"]
    assert np.allclose(surf.le_unrotated(surf.s1), tip, atol=1e-6)


def test_spar_channel_at_quarter_chord_with_clearance():
    model = build_model(DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS)
    surf = model.surfaces["wing_right"]
    a, b = surf.spar_line()
    w = model.params["wing"]
    for s, end in ((0.0, a), (surf.spar_s_end, b)):
        c = surf.chord(s)
        le_x = w["x_le_mm"] + s * math.tan(math.radians(w["sweep_deg"]))
        # 25 % chord along the (rotated) chord line: x within the incidence projection.
        assert abs(end[0] - (le_x + SPAR_CHORD_FRACTION * c)) < 0.02 * c
    assert model.spar["outer_mm"] > 0
    assert surf.spar_od_mm + SPAR_CLEARANCE_MM == pytest.approx(model.spar["outer_mm"] + 0.3)


def test_airfoil_profiles_load_and_unknown_falls_back():
    pr = load_profile("sd7037")
    assert pr.x[0] == 0 and pr.x[-1] == pytest.approx(1.0)
    assert 0.08 < float(pr.thickness(0.3)) < 0.1
    assert load_profile("naca2415").airfoil == "naca2415"
    assert load_profile("no-such-foil").airfoil == "naca0012"


def test_mount_pattern_parsing():
    assert parse_mount_pattern("25x25 M3") == {
        "a_mm": 25.0,
        "b_mm": 25.0,
        "screw": "M3",
        "screw_mm": 3.0,
    }
    assert parse_mount_pattern("Ø30 M4")["b_mm"] == 30.0
    assert parse_mount_pattern("16x19 M3")["b_mm"] == 19.0


def test_impossible_design_fails_with_a_plain_message():
    p = design(wing={"root_chord_mm": 120.0, "tip_chord_mm": 100.0, "airfoil": "mh32"})
    with pytest.raises(CadError, match="spar tube does not fit"):
        build_model(
            p,
            DEFAULT_MISSION,
            DEFAULT_SETTINGS,
            analysis={"structure": {"spar_sizing": {"outer_mm": 16.0, "wall_mm": 1.0}}},
        )
