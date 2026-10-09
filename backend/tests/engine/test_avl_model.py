"""AVL model: builds and trims for every layout and tail type; convergence; neutral point."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.engine import avl_model
from app.engine.avl_model import build_avl_input, run_avl, wing_strips
from app.engine.geometry import build_geometry

LAYOUTS = ["front_tilt", "rear_tilt", "quad_pusher"]
TAILS = ["conventional", "v_tail", "inverted_v", "twin_boom_h"]


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("tail", TAILS)
def test_builds_runs_and_trims(params: dict[str, Any], layout: str, tail: str) -> None:
    params["layout"] = layout
    params["tail"]["type"] = tail
    g = build_geometry(params)
    out = run_avl(g, [{"name": "cruise", "xref": 0.36, "cl": 0.5, "trim": True}])
    case = out["cases"][0]
    assert case["trim_converged"]
    assert case["totals"]["CL"] == pytest.approx(0.5, abs=1e-3)
    assert abs(case["totals"]["Cm"]) < 1e-3
    assert abs(case["elevator_deg"]) < 15
    assert 0.85 < case["totals"]["e"] < 1.05
    assert case["stab"]["dCm/dalpha"] < 0  # statically stable about this CG
    assert "elevator" in out["controls"]
    strips = wing_strips(case)
    assert len(strips) == 2 * avl_model.WING_NSPAN
    expected_fins = {"conventional": "Fin", "twin_boom_h": "Fin (YDUP)"}
    if tail in expected_fins:
        assert expected_fins[tail] in case["strips"]


def test_avl_file_has_references_and_surfaces(params: dict[str, Any]) -> None:
    g = build_geometry(params)
    inp = build_avl_input(g, claf_wing=0.97, claf_tail=0.93)
    text = inp["avl"]
    assert f"{g['wing']['area_m2']:.6f} {g['wing']['mac_mm'] / 1000:.6f} 1.800000" in text
    assert "AFILE\nsd7037.dat" in text and "naca0009.dat" in inp["files"]
    assert "CONTROL\nelevator 1.0 0.70" in text
    assert "BODY\nFuselage" in text and "fuse.dat" in inp["files"]


def test_lattice_is_converged(params: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """A 1.5-1.7 x finer lattice changes CL_alpha and CDi by under 1 % and Xnp by < 0.5 % MAC."""
    g = build_geometry(params)
    case = [{"name": "c", "xref": 0.36, "cl": 0.5, "trim": True}]
    base = run_avl(g, case)["cases"][0]
    monkeypatch.setattr(avl_model, "WING_NCHORD", 10)
    monkeypatch.setattr(avl_model, "WING_NSPAN", 72)
    monkeypatch.setattr(avl_model, "TAIL_NCHORD", 8)
    monkeypatch.setattr(avl_model, "TAIL_NSPAN", 18)
    fine = run_avl(g, case)["cases"][0]
    assert fine["totals"]["CDff"] == pytest.approx(base["totals"]["CDff"], rel=0.01)
    assert fine["stab"]["dCL/dalpha"] == pytest.approx(base["stab"]["dCL/dalpha"], rel=0.01)
    mac = g["wing"]["mac_mm"] / 1000
    assert abs(fine["stab"]["neutral point"] - base["stab"]["neutral point"]) < 0.005 * mac


def test_neutral_point_moves_aft_with_a_bigger_tail(params: dict[str, Any]) -> None:
    g = build_geometry(params)
    case = [{"name": "c", "xref": 0.36, "cl": 0.5, "trim": True}]
    np_small = run_avl(g, case)["cases"][0]["stab"]["neutral point"]
    big = copy.deepcopy(params)
    big["tail"]["span_mm"] *= 1.4
    np_big = run_avl(build_geometry(big), case)["cases"][0]["stab"]["neutral point"]
    assert np_big > np_small + 0.005


def test_worker_survives_a_crash() -> None:
    """A Fortran failure in the worker raises SolverError and the next request works."""
    from app.engine.avl_model import SolverError, run_avl_text, solver_worker

    with pytest.raises(SolverError):
        solver_worker().request({"op": "nope"})
    rect = "R\n0.0\n0 0 0.0\n8 1 8\n0 0 0\n0.0\nSURFACE\nW\n6 1.0 12 1.0\nYDUPLICATE\n0.0\n"
    rect += "SECTION\n0 0 0 1 0\nSECTION\n0 4 0 1 0\n"
    res = run_avl_text(rect, [{"name": "a", "alpha": 4.0, "xref": 0.0}])
    assert res[0]["totals"]["CL"] > 0.25
