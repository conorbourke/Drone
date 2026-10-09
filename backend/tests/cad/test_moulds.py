"""Phase 7 moulds: the default design scaled to the final 24 kg (``run_scale``) gets two-part
moulds for the nose bay, the fuselage and the wing-root fairing; every tile fits the printer,
each half demoulds along its pull, the draft check flags zero-draft surfaces, keys, flanges
and bolt holes exist and every export is valid."""

from __future__ import annotations

import copy
import itertools
import json
from pathlib import Path
from typing import Any

import cadquery as cq
import numpy as np
import pytest
import trimesh

from app.cad import moulds as M
from app.cad import moulds_analysis as MA
from app.cad.export_3mf import validate_3mf
from app.cad.export_step import reimport_step
from app.cad.export_stl import read_binary_stl
from app.cad.model import build_model
from app.defaults import DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS

PARTS = ("nose", "fuselage", "wing_root_fairing")


@pytest.fixture(scope="session")
def scaled_24kg(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    from app.engine.scale import run_scale

    cache = tmp_path_factory.mktemp("polars-moulds")
    out = run_scale(
        copy.deepcopy(DEFAULT_DESIGN_PARAMETERS),
        copy.deepcopy(DEFAULT_MISSION),
        copy.deepcopy(DEFAULT_SETTINGS),
        24.0,
        mode="fast",
        cache_dir=str(cache),
    )
    assert out["valid"] and out["mission"]["scale"] == "final"
    return {"parameters": out["parameters"], "mission": out["mission"]}


@pytest.fixture(scope="session")
def moulds(
    scaled_24kg: dict[str, Any], tmp_path_factory: pytest.TempPathFactory
) -> tuple[dict[str, Any], Path, list[tuple[float, str]]]:
    out = tmp_path_factory.mktemp("moulds-24kg")
    calls: list[tuple[float, str]] = []
    manifest = M.generate_moulds(
        scaled_24kg["parameters"],
        scaled_24kg["mission"],
        copy.deepcopy(DEFAULT_SETTINGS),
        parts=PARTS,
        out_dir=out,
        progress=lambda f, m: calls.append((f, m)),
    )
    return manifest, out, calls


def _part(manifest: dict[str, Any], key: str) -> dict[str, Any]:
    return next(p for p in manifest["parts"] if p["key"] == key)


# ---------------------------------------------------------------------------
# Unit tests on simple solids
# ---------------------------------------------------------------------------


def test_draft_report_flags_zero_draft_surface() -> None:
    box = cq.Solid.makeBox(100, 60, 40, cq.Vector(-50, -30, -20))
    z = np.array([0.0, 0.0, 1.0])
    samples = MA.face_samples(box)
    pp = MA.parting_plane(samples, z)
    assert pp["offset_mm"] == pytest.approx(0.0, abs=1.0)
    rep = MA.draft_report(samples, z, 0.0, 2.0, 1.0, ("upper", "lower"))
    flagged = {f["face"] for f in rep["faces"] if f["flagged"]}
    sides = {f["face"] for f in rep["faces"] if abs(f["centroid_mm"][2]) < 1}
    caps = {f["face"] for f in rep["faces"] if abs(abs(f["centroid_mm"][2]) - 20) < 1e-6}
    assert len(sides) == 4 and sides == flagged
    assert not caps & flagged
    for f in rep["faces"]:
        if f["face"] in sides:
            assert f["min_draft_deg"] == pytest.approx(0.0, abs=1e-6)
            assert all(r["fraction_below_min_outside_band"] > 0.8 for r in f["halves"])
    assert rep["summary"]["flagged"] == 4


def test_sphere_has_draft_except_the_parting_band() -> None:
    sphere = cq.Solid.makeSphere(50.0)
    z = np.array([0.0, 0.0, 1.0])
    samples = MA.face_samples(sphere)
    pp = MA.parting_plane(samples, z)
    assert pp["offset_mm"] == pytest.approx(0.0, abs=3.0)
    band = MA.parting_band(50.0, 2.0)
    rep = MA.draft_report(samples, z, 0.0, 2.0, band)
    assert rep["flagged_faces"] == []
    assert rep["summary"]["fraction_below_min"] > 0  # the band itself is below 2 deg
    tris = MA.mesh_triangles(sphere, 0.05, 0.1)
    dm = MA.demould_check(tris, z, 0.0, band, 600)
    assert dm["A"]["undercut_fraction"] == 0.0 and dm["B"]["undercut_fraction"] == 0.0


def test_ray_cast_finds_an_undercut() -> None:
    """Two plates joined by a narrow column: pulled along z, the underside of the top plate
    traps the upper mould half."""
    body = (
        cq.Solid.makeBox(100, 60, 20, cq.Vector(0, 0, -20))
        .fuse(cq.Solid.makeBox(20, 60, 15, cq.Vector(40, 0, 0)))
        .fuse(cq.Solid.makeBox(100, 60, 10, cq.Vector(0, 0, 15)))
    )
    z = np.array([0.0, 0.0, 1.0])
    tris = MA.mesh_triangles(body, 0.05, 0.1)
    dm = MA.demould_check(tris, z, 0.0, 1.0, 1500)
    assert dm["A"]["undercut_fraction"] > 0.05
    assert dm["B"]["undercut_fraction"] == 0.0


def test_plan_cuts_avoid_kinks_and_fit() -> None:
    cuts = M.plan_cuts(0.0, 1000.0, 230.0, lambda u: 0.0, [400.0, 610.0], 60.0)
    bounds = [0.0, *cuts, 1000.0]
    assert all(b - a <= 230.0 + 1e-6 for a, b in itertools.pairwise(bounds))
    assert all(min(abs(c - k) for k in (400.0, 610.0)) >= 15.0 for c in cuts)
    assert M.max_tile_length((177, 90), (240, 240, 240), 6, 2) == pytest.approx(232)
    assert M.max_tile_length((250, 90), (240, 240, 240), 6, 2) == 0.0


# ---------------------------------------------------------------------------
# The 24 kg design
# ---------------------------------------------------------------------------


def test_moulds_generated_for_three_parts(moulds: tuple[dict[str, Any], Path, Any]) -> None:
    manifest, out, calls = moulds
    assert manifest["schema"] == M.MANIFEST_SCHEMA
    assert [p["key"] for p in manifest["parts"]] == list(PARTS)
    assert json.loads((out / "moulds_manifest.json").read_text())["schema"] == M.MANIFEST_SCHEMA
    for p in manifest["parts"]:
        assert len(p["halves"]) == 2
        assert all(h["tiles"] for h in p["halves"])
        assert p["parting_line"]["polyline_mm"] and p["parting_line"]["description"]
        assert p["mould"]["laminate_allowance"]["direction"].startswith("inward")
        assert p["assembly_order"]
    assert _part(manifest, "fuselage")["tiling"]["tiles_per_half"] >= 6  # 1.9 m long
    for f in manifest["files"]:
        path = out / f["path"]
        assert path.exists() and path.stat().st_size == f["size_bytes"] > 0
    fracs = [f for f, _ in calls]
    assert fracs == sorted(fracs) and fracs[-1] == 1.0
    assert manifest["peak_rss_mb"] < 1500


def test_every_tile_fits_the_envelope(moulds: tuple[dict[str, Any], Path, Any]) -> None:
    manifest, out, _ = moulds
    env = np.array(manifest["envelope_mm"])
    for p in manifest["parts"]:
        for h in p["halves"]:
            for t in h["tiles"]:
                tri, _n = read_binary_stl(out / t["files"]["stl"])
                v = tri.reshape(-1, 3)
                ext = v.max(axis=0) - v.min(axis=0)
                assert np.all(ext <= env + 1e-3), (t["label"], ext)
                assert v[:, 2].min() == pytest.approx(0.0, abs=1e-3)  # on the bed
                assert t["fits"] is True


def test_each_half_demoulds_along_its_pull(
    moulds: tuple[dict[str, Any], Path, Any], scaled_24kg: dict[str, Any]
) -> None:
    manifest, _, _ = moulds
    model = build_model(scaled_24kg["parameters"], scaled_24kg["mission"], DEFAULT_SETTINGS)
    sources = M.mould_sources(model, M.DEFAULT_OPTIONS)
    for p in manifest["parts"]:
        for h in p["halves"]:
            assert h["demould"]["demouldable"], (p["key"], h["demould"])
        # independent ray-cast check of the part surface with the manifest's pull and plane
        src = sources[p["key"]]
        net = (
            src.extra["surface"].net_solid()
            if p["key"] == "wing_root_fairing"
            else src.outer(0.0, *src.net_range)
        )
        pull = np.array(p["pull_direction"])
        h0 = float(np.dot(p["parting_line"]["plane"]["point_mm"], pull))
        tris = MA.mesh_triangles(net, 0.08, 0.15)
        dm = MA.demould_check(tris, pull, h0, p["draft"]["parting_band_mm"], 800, seed=3)
        for half in ("A", "B"):
            assert dm[half]["samples"] > 50
            assert dm[half]["undercut_fraction"] <= 0.001, (p["key"], half, dm)


def test_draft_report(moulds: tuple[dict[str, Any], Path, Any]) -> None:
    manifest, _, _ = moulds
    for p in manifest["parts"]:
        d = p["draft"]
        assert d["min_draft_deg"] == 2.0
        assert d["faces"] and d["summary"]["faces"] == len(d["faces"])
        for f in d["faces"]:
            assert f["halves"] and f["where"]
        assert d["map"]["points_uv_mm"] and len(d["map"]["draft_deg"]) == len(
            d["map"]["points_uv_mm"]
        )
        assert set(d["flagged_faces"]) == {f["face"] for f in d["faces"] if f["flagged"]}
    # The rounded-rectangle fuselage has flat sides parallel to any pull: reported, not fixed.
    fus = _part(manifest, "fuselage")
    assert fus["draft"]["flagged_faces"]
    assert any(f["min_draft_deg"] < 0.5 for f in fus["draft"]["flagged_detail"])


def test_keys_flanges_and_bolt_holes(moulds: tuple[dict[str, Any], Path, Any]) -> None:
    manifest, out, _ = moulds
    for p in manifest["parts"]:
        m = p["mould"]
        assert m["flange_width_mm"] == 25.0 and m["wall_mm"] == 6.0
        assert len(m["flange_bolts_mm"]) >= 4
        assert len(m["registration_keys"]["positions_mm"]) >= 2
        pull = np.array(p["pull_direction"])
        for b in m["flange_bolts_mm"]:
            assert np.dot(b, pull) == pytest.approx(
                np.dot(p["parting_line"]["plane"]["point_mm"], pull), abs=0.1
            )
        for h in p["halves"]:
            assert h["wall_thickness_mm"]["min_mm"] >= 0.85 * m["wall_mm"], h["key"]
        if p["tiling"]["tiles_per_half"] > 1:
            for j in p["tiling"]["joints"]:
                for v in j["halves"].values():
                    assert v["bolts"] >= 2 and v["keys"] >= 1
        # Geometry: cones (keys on one half, sockets in the other) and bolt holes in the STEP.
        n_keys = len(m["registration_keys"]["positions_mm"])
        for h in p["halves"]:
            shape = cq.importers.importStep(str(out / h["step_file"])).val()
            types = [f.geomType() for f in shape.Faces()]
            assert types.count("CONE") >= n_keys, (p["key"], h["key"])
            assert types.count("CYLINDER") >= len(m["flange_bolts_mm"])
            # flange face in the parting plane
            h0 = float(np.dot(p["parting_line"]["plane"]["point_mm"], pull))
            flat = 0.0
            for f in shape.Faces():  # planar faces, some stored as B-spline surfaces
                n = f.normalAt().toTuple()
                c = np.array(f.Center().toTuple())
                if abs(abs(np.dot(n, pull)) - 1) < 1e-3 and abs(np.dot(c, pull) - h0) < 0.5:
                    flat += f.Area()
            assert flat > 1500.0, (p["key"], h["key"], flat)


def test_exports_valid(moulds: tuple[dict[str, Any], Path, Any]) -> None:
    manifest, out, _ = moulds
    for p in manifest["parts"]:
        for h in p["halves"]:
            info = reimport_step(out / h["step_file"])
            assert info["valid"] and info["schema"] == "AP214"
            assert info["solids"] == len(h["tiles"])
            for t in h["tiles"]:
                v = validate_3mf(out / t["files"]["3mf"])
                assert v["objects"] == 1 and v["items"] == 1
                assert t["label"] in v["names"].values()
                mesh = trimesh.load(out / t["files"]["stl"], force="mesh")
                assert mesh.is_watertight and mesh.is_winding_consistent
                assert mesh.volume > 0
                assert t["print_orientation"]["name"] and t["notes"]
        pdf = (out / p["files"]["pdf"]).read_bytes()
        assert (
            pdf.startswith(b"%PDF")
            and pdf.count(b"/Type /Page\n")
            + pdf.count(b"/Type /Page ")
            + pdf.count(b"/Type /Page>")
            >= 2
        )


def test_fairing_part(moulds: tuple[dict[str, Any], Path, Any]) -> None:
    manifest, _, _ = moulds
    f = _part(manifest, "wing_root_fairing")
    assert "fillet" in f["description"]
    assert f["halves"][0]["key"] == "upper"
    assert abs(f["pull_direction"][2]) > 0.99
