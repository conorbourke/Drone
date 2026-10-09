"""File exports of the default design (Phase 5 contract section 3)."""

from __future__ import annotations

import csv
import re
import zipfile

import ezdxf
import numpy as np
import pytest
import trimesh

from app.cad import bom as bom_mod
from app.cad import parts as P
from app.cad import split as S
from app.cad.drawings import DRAWING_PAGES
from app.cad.export_3mf import CORE_NS, MeshObject, arrange_plates, validate_3mf, write_3mf
from app.cad.export_step import reimport_step
from app.cad.export_stl import read_binary_stl, write_binary_stl
from app.cad.model import build_model
from app.defaults import DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS


def test_manifest_shape_and_progress(default_export):
    m, out = default_export
    assert m["schema"] == "vtol-files/1"
    assert m["checks"]["all_pieces_fit"] is True
    assert m["checks"]["watertight_all"] is True
    keys = {p["key"] for p in m["parts"]}
    assert {"wing_right", "wing_left", "fuselage", "nose_bay", "wing_clamp"} <= keys
    assert {"tilt_hinge", "tilt_motor_mount", "tail_apex", "landing_gear"} <= keys
    for part in m["parts"]:
        for pc in part["pieces"]:
            assert pc["fits"] and max(pc["size_mm"]) <= 240.0 + 1e-6
            assert pc["filament"] in ("LW-PLA", "PETG", "PA-CF", "ASA")
            assert pc["mass_g"] > 0 and pc["print_time_band"]
            assert pc["orientation"]["reason"]
            assert pc["label"] and pc["stl"].endswith(".stl")
    wing = next(p for p in m["parts"] if p["key"] == "wing_left")
    assert wing["pieces"][1]["label"] == f"Wing L 2/{len(wing['pieces'])}"
    fracs = [f for f, _ in m["_progress"]]
    assert fracs[0] == 0.0 and fracs[-1] == 1.0 and fracs == sorted(fracs)
    for f in m["files"]:
        assert (out / f["path"]).stat().st_size == f["size_bytes"]


def test_stl_files_are_watertight_and_fit(default_export):
    m, out = default_export
    stls = sorted((out / "print" / "stl").glob("*.stl"))
    assert len(stls) == m["checks"]["pieces"]
    for path in stls:
        mesh = trimesh.load(path)
        assert mesh.is_watertight, path.name
        assert mesh.is_winding_consistent, path.name
        assert mesh.volume > 0, path.name
        ext = mesh.bounds[1] - mesh.bounds[0]
        assert np.all(ext <= 240.0 + 1e-3), (path.name, ext)
        assert mesh.bounds[0][2] == pytest.approx(0.0, abs=1e-3)  # on the bed


def test_3mf_packages_are_valid(default_export):
    m, out = default_export
    files = sorted((out / "print").rglob("*.3mf"))
    assert len(files) == len(m["parts"]) + 1
    for path in files:
        info = validate_3mf(path)
        assert info["objects"] >= 1 and info["items"] >= info["objects"]
    with zipfile.ZipFile(out / "print" / "all_pieces.3mf") as z:
        assert z.namelist() == ["[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"]
        head = z.read("3D/3dmodel.model")[:400].decode()
        assert 'unit="millimeter"' in head and CORE_NS in head
    copies = sum(p["quantity"] * len(p["pieces"]) for p in m["parts"])
    assert m["plates"]["count"] >= 1
    assert sum(len(x) for x in m["plates"]["pieces"]) == copies


def test_plate_packing_respects_bed_and_spacing():
    fps = [(200.0, 30.0), (100.0, 100.0), (120.0, 50.0), (230.0, 230.0), (20.0, 20.0)]
    slots, n = arrange_plates(fps, (256.0, 256.0), (240.0, 240.0), 5.0)
    assert n >= 2
    for i, (s, (w, d)) in enumerate(zip(slots, fps, strict=True)):
        assert s["x"] >= 8.0 - 1e-9 and s["x"] + w <= 248.0 + 1e-9
        assert s["y"] >= 8.0 - 1e-9 and s["y"] + d <= 248.0 + 1e-9
        for j, (t, (w2, d2)) in enumerate(zip(slots, fps, strict=True)):
            if j <= i or t["plate"] != s["plate"]:
                continue
            sep_x = s["x"] + w + 5 <= t["x"] + 1e-9 or t["x"] + w2 + 5 <= s["x"] + 1e-9
            sep_y = s["y"] + d + 5 <= t["y"] + 1e-9 or t["y"] + d2 + 5 <= s["y"] + 1e-9
            assert sep_x or sep_y, (i, j)


def test_step_files_reimport(default_export):
    m, out = default_export
    assy = reimport_step(out / "cad" / "assembly.step")
    assert assy["valid"] and assy["schema"] == "AP214"
    printed = sum(p["quantity"] * len(p["pieces"]) for p in m["parts"])
    assert assy["solids"] >= printed + len(m["tubes"])
    for key in ("wing_right", "fuselage", "tilt_hinge"):
        part = reimport_step(out / "cad" / f"{key}.step")
        n = len(next(p for p in m["parts"] if p["key"] == key)["pieces"])
        assert part["valid"] and part["solids"] == n


def test_dxf_files_open(default_export):
    m, out = default_export
    assert {d["part"] for d in m["dxf"]} >= {
        "Lift motor plate",
        "Tilt servo plate",
        "Nose-bay payload base",
    }
    for d in m["dxf"]:
        doc = ezdxf.readfile(out / d["path"])
        assert doc.header["$INSUNITS"] == 4
        circles = doc.modelspace().query("CIRCLE[layer=='HOLES']")
        assert len(circles) == len(d["holes"]) >= 2
    base = next(d for d in m["dxf"] if d["part"] == "Nose-bay payload base")
    assert len(base["holes"]) == 4


def _pdf_pages(path) -> int:
    return len(re.findall(rb"/Type\s*/Page[^s]", path.read_bytes()))


def test_pdfs_have_expected_pages(default_export):
    m, out = default_export
    assert _pdf_pages(out / "drawings" / "drawings.pdf") == DRAWING_PAGES == 4
    assert _pdf_pages(out / "notes" / "printing_notes.pdf") >= len(m["parts"])
    for part in m["parts"]:
        text = (out / "notes" / f"{part['key']}.md").read_text()
        for word in (
            "Filament",
            "Nozzle",
            "Layer height",
            "Walls",
            "Infill",
            "Orientation",
            "Temperatures",
            "Post-processing",
        ):
            assert word in text, (part["key"], word)


def _read_bom(path):
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_bom_columns_and_totals(default_export):
    m, out = default_export
    rows = _read_bom(out / "bom.csv")
    assert list(rows[0].keys()) == bom_mod.COLUMNS
    body, total = rows[:-1], rows[-1]
    assert total["item"] == "TOTAL"
    mass = sum(float(r["line mass g"]) for r in body if r["line mass g"])
    price = sum(float(r["line price €"]) for r in body if r["line price €"])
    assert float(total["line mass g"]) == pytest.approx(mass, abs=0.05)
    assert float(total["line price €"]) == pytest.approx(price, abs=0.05)
    generic = [r for r in body if r["model"] == bom_mod.PHASE4]
    assert {r["role"] for r in generic} >= {"lift_motor", "lift_esc", "battery", "tilt_servo"}
    assert any(r["category"] == "carbon tube" and "cut to" in r["item"] for r in body)
    printed = {r["role"] for r in body if r["category"] == "printed part"}
    assert printed == {p["key"] for p in m["parts"]}


def test_bom_totals_equal_parts_list_totals():
    selection = [
        {
            "role": "lift_motor",
            "category": "motor",
            "manufacturer": "T-Motor",
            "model": "MN4014",
            "quantity": 4,
            "mass_g": 150.0,
            "listings": [
                {
                    "supplier_name": "A",
                    "country": "IE",
                    "url": "https://a.example",
                    "price_eur": 60.0,
                },
                {
                    "supplier_name": "B",
                    "country": "UK",
                    "url": "https://b.example",
                    "price_eur": 55.0,
                },
            ],
            "spec": {"mount_pattern": "25x25 M3", "stator_size": "4014"},
        },
        {
            "role": "battery",
            "category": "battery",
            "manufacturer": "X",
            "model": "6S 5000",
            "quantity": 1,
            "mass_g": 780.0,
            "price_eur": 95.5,
        },
    ]
    model = build_model(
        DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS, parts_selection=selection
    )
    assert model.lift_motor["generic"] is False
    rows, totals = bom_mod.build_bom(model, [], [], [], [], selection)
    assert totals["selection_mass_g"] == pytest.approx(4 * 150 + 780)
    assert totals["selection_price_eur"] == pytest.approx(4 * 55 + 95.5)
    best = next(r for r in rows if r["role"] == "lift_motor")
    assert best["best supplier"] == "B" and best["country"] == "UK"
    assert not any(r["role"] == "lift_motor" and r["model"] == bom_mod.PHASE4 for r in rows)


def test_outputs_are_deterministic(tmp_path):
    """Same input, same bytes: STL and 3MF of a split part built twice."""
    blobs = []
    for run in range(2):
        model = build_model(DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS)
        spec = next(s for s in P.part_specs(model) if s.key == "tail_right")
        pieces, _ = S.make_pieces(model, spec)
        objs = []
        for pc in pieces:
            S.orient_piece(pc, model.envelope, model.bed, 0.05)
            write_binary_stl(tmp_path / f"{run}.stl", pc.vertices, pc.faces, pc.label)
            objs.append(MeshObject(pc.label, "#E9E4D4", "LW-PLA", pc.vertices, pc.faces))
        write_3mf(tmp_path / f"{run}.3mf", objs, [(0, np.eye(4))], "t")
        blobs.append(
            ((tmp_path / f"{run}.stl").read_bytes(), (tmp_path / f"{run}.3mf").read_bytes())
        )
    assert blobs[0] == blobs[1]
    tris, _ = read_binary_stl(tmp_path / "0.stl")
    assert len(tris) > 100
