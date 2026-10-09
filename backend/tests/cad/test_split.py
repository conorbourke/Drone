"""Splitting for the printer (Phase 5 contract section 2)."""

from __future__ import annotations

import numpy as np
import pytest

from app.cad import parts as P
from app.cad import split as S
from app.cad.joints import KEY_CLEARANCE_MM
from app.cad.model import CadError, EnvelopeError, build_model
from app.defaults import DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS
from tests.cad.conftest import design, prototype_3m

COARSE_MM = 0.2  # mesh tolerance for the fit checks of the variants (speed)


def build_all(params, settings=DEFAULT_SETTINGS):
    model = build_model(params, DEFAULT_MISSION, settings)
    out = []
    for spec in P.part_specs(model):
        pieces, plan = S.make_pieces(model, spec)
        for pc in pieces:
            S.orient_piece(pc, model.envelope, model.bed, COARSE_MM)
        out.append((spec, pieces, plan))
    return model, out


def assert_all_fit(model, built):
    env = np.asarray(model.envelope)
    for _, pieces, _ in built:
        for pc in pieces:
            ext = pc.vertices.max(axis=0) - pc.vertices.min(axis=0)
            assert np.all(ext <= env + 1e-6), (pc.label, ext)
            assert pc.solid.isValid(), pc.label
            assert len(pc.solid.Solids()) == 1, pc.label


VARIANTS = {
    "rear_tilt": design(layout="rear_tilt"),
    "quad_pusher": design(layout="quad_pusher", tail={"type": "twin_boom_h"}),
    "conventional": design(tail={"type": "conventional"}),
    "v_tail": design(tail={"type": "v_tail"}),
    "twin_boom_h": design(tail={"type": "twin_boom_h"}),
    "ellipse_legs": design(fuselage={"cross_section": "ellipse"}, landing_gear={"type": "legs"}),
}


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_layouts_and_tail_types_build_and_fit(name):
    """All three layouts and four tail types (the default covers front tilt + inverted V)."""
    model, built = build_all(VARIANTS[name])
    assert_all_fit(model, built)
    keys = {spec.key for spec, _, _ in built}
    layout = model.params["layout"]
    if layout == "quad_pusher":
        assert "pusher_mount" in keys and "tilt_hinge" not in keys
        assert {"motor_mount_front", "motor_mount_rear"} <= keys
    else:
        assert {"tilt_hinge", "tilt_motor_mount"} <= keys
    tail = model.params["tail"]["type"]
    expected = {
        "conventional": {"tail_hstab", "tail_fin", "tail_mount"},
        "v_tail": {"tail_right", "tail_left", "tail_apex", "tail_pylon"},
        "inverted_v": {"tail_right", "tail_left", "tail_apex"},
        "twin_boom_h": {"tail_hstab", "tail_fin_right", "tail_fin_left", "tail_mount"},
    }[tail]
    assert expected <= keys, keys


@pytest.fixture(scope="module")
def proto():
    return build_all(prototype_3m())


def test_large_prototype_pieces_fit(proto):
    model, built = proto
    assert model.params["wing"]["span_mm"] == 3000
    assert_all_fit(model, built)
    wing = next(p for s, p, _ in built if s.key == "wing_right")
    assert len(wing) >= 6  # 1.5 m panel in pieces of under 240 mm


def _wing_plans(built):
    return [(s, pcs, plan) for s, pcs, plan in built if s.split == "surface"]


@pytest.mark.parametrize("which", ["default", "proto"])
def test_no_joint_in_root_zone_or_keepouts(which, proto, default_built):
    model, built = proto if which == "proto" else default_built
    for spec, _, plan in _wing_plans(built):
        surf = model.surfaces[spec.surface]
        root = surf.s0 if surf.root_s is None else surf.root_s
        for s in plan["stations_mm"]:
            if surf.root_exclusion_mm:
                if surf.root_s is None:
                    assert s - surf.s0 >= surf.root_exclusion_mm - 1e-6, (spec.key, s)
                else:
                    assert abs(s - root) >= surf.root_exclusion_mm - 1e-6, (spec.key, s)
            for a, b, why in surf.keepouts:
                assert not (a <= s <= b), (spec.key, s, why)
    wing = model.surfaces["wing_right"]
    assert wing.root_exclusion_mm == pytest.approx(0.15 * model.params["wing"]["span_mm"] / 2)
    assert any("boom" in why for _, _, why in wing.keepouts)


@pytest.fixture(scope="module")
def default_built():
    return build_all(DEFAULT_DESIGN_PARAMETERS)


def test_fuselage_joints_avoid_wing_clamp_and_flange(default_built):
    model, built = default_built
    _, pieces, plan = next(b for b in built if b[0].key == "fuselage")
    assert len(pieces) >= 2
    for x in plan["stations_mm"]:
        for k in plan["keepouts"]:
            assert not (k["from_mm"] <= x <= k["to_mm"]), (x, k)
        assert abs(x - model.spar_x) > 30
        assert x > model.fuselage.nose_bay_length + 15


def _inside(solid, p) -> bool:
    import cadquery as cq

    return solid.isInside(cq.Vector(*map(float, p)), 1e-3)


def test_wing_joints_have_keys_bonding_and_continuous_spar(default_built):
    model, built = default_built
    _, pieces, plan = next(b for b in built if b[0].key == "wing_right")
    surf = model.surfaces["wing_right"]
    axis = surf.axis_world()
    assert plan["stations_mm"]
    for i, s in enumerate(plan["stations_mm"]):
        inb, outb = pieces[i], pieces[i + 1]
        rec = next(j for j in inb.joints if j["side"] == "pins" and j["station_mm"] == round(s, 2))
        assert len(rec["keys"]) == 2
        assert all(k["clearance_mm"] == KEY_CLEARANCE_MM for k in rec["keys"])
        assert rec["bonding"]["grooves"] >= 1
        assert rec["channel"]["continuous"]
        for k in rec["keys"]:
            p = np.array(k["position_mm"]) + axis * 4.0
            assert _inside(inb.solid, p), "pin missing"
            assert not _inside(outb.solid, p), "socket missing"
        # Spar channel straight through the joint: empty on the axis, material around it.
        if rec["channel"]["kind"] == "spar tube":
            c = surf.spar_point_at(s)
            r = rec["channel"]["channel_mm"] / 2
            for d in (-2.0, 2.0):
                q = c + axis * d
                assert not _inside(inb.solid, q) and not _inside(outb.solid, q)
            up = np.array([0.0, 0.0, r + 0.6])
            assert _inside(inb.solid, c - axis * 2 + up) and _inside(outb.solid, c + axis * 2 + up)


def test_fuselage_joints_have_keys(default_built):
    _, built = default_built
    _, pieces, plan = next(b for b in built if b[0].key == "fuselage")
    for i in range(len(plan["stations_mm"])):
        rec = next(j for j in pieces[i].joints if j["side"] == "pins")
        assert len(rec["keys"]) == 2
        for k in rec["keys"]:
            p = np.array(k["position_mm"]) + np.array([3.0, 0, 0])
            assert _inside(pieces[i].solid, p)
            assert not _inside(pieces[i + 1].solid, p)
        assert "frame ring" in rec["bonding"]["kind"]


def test_envelope_check_fails_loudly():
    small = {
        **DEFAULT_SETTINGS,
        "printer": {
            **DEFAULT_SETTINGS["printer"],
            "usable_envelope_mm": {"x": 120.0, "y": 120.0, "z": 120.0},
        },
    }
    model = build_model(DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, small)
    spec = next(s for s in P.part_specs(model) if s.key == "wing_right")
    with pytest.raises(CadError):
        S.make_pieces(model, spec)
    # The exported-mesh check itself
    pc = S.Piece("x", 1, 1, "Test piece", None)
    pc.vertices = np.array([[0, 0, 0], [250.0, 10, 10]])
    with pytest.raises(EnvelopeError, match="larger than the usable envelope"):
        S.check_fit(pc, (240.0, 240.0, 240.0))
