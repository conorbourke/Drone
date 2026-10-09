"""Phase 5 CAD kernel: parametric solids, print splitting and file exports.

Entry point: :func:`generate_files`. Everything is built and exported part by part (the
OpenCascade solids of one part are meshed, written and released before the next part's meshes
are made) so memory stays bounded; the assembly STEP reuses the part solids.

Output layout under ``out_dir``::

    print/stl/<part>_<n>of<m>.stl       one binary STL per piece, in print orientation
    print/3mf/<part>.3mf                one 3MF per part (all its pieces x copies, on plates)
    print/all_pieces.3mf                every printed piece arranged on bed-sized plates
    cad/<part>.step, cad/assembly.step  STEP AP214 (aircraft coordinates, mm)
    drawings/drawings.pdf               dimensioned drawings (4 sheets, A3)
    drawings/dxf/*.dxf                  flat plates for CNC-cut carbon
    bom.csv                             bill of materials with totals
    notes/<part>.md, notes/printing_notes.pdf
    manifest.json                       the returned manifest
"""

from __future__ import annotations

import gc
import hashlib
import json
import math
import resource
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from app.cad.model import (
    FILAMENTS,
    PRINT_PROFILES,
    CadError,
    CadModel,
    EnvelopeError,
    build_model,
)

__all__ = ["MANIFEST_SCHEMA", "CadError", "EnvelopeError", "build_model", "generate_files"]

MANIFEST_SCHEMA = "vtol-files/1"

FILE_HELP = {
    "stl": "STL mesh of one printed piece, already in its print orientation. Opens in Bambu "
    "Studio, PrusaSlicer, Cura.",
    "3mf": "3MF print project (millimetres, named and coloured pieces). Opens in Bambu Studio, "
    "PrusaSlicer, Cura.",
    "step": "STEP AP214 CAD model in aircraft coordinates. Opens in FreeCAD or an online STEP "
    "viewer.",
    "pdf": "PDF document. Opens in any PDF reader.",
    "dxf": "DXF flat-part outline for CNC cutting. Opens in LibreCAD, QCAD, FreeCAD.",
    "csv": "Spreadsheet (CSV, UTF-8). Opens in LibreOffice Calc, Excel, Google Sheets.",
    "md": "Plain-text printing notes (Markdown). Opens in any text editor.",
    "json": "Machine-readable manifest of this export.",
}

HARDWARE_MASS = {  # g each, price € each (estimates)
    "M3 heat-set insert (M3 x 5.7)": (0.5, 0.10),
    "M3 nyloc nut": (0.35, 0.05),
    "M2 x 8 screw (servo)": (0.3, 0.05),
    "M3 grub screw x 6": (0.3, 0.08),
    "M2 pushrod with 2 ball links": (3.0, 2.5),
}
SCREW_LENGTHS = [6, 8, 10, 12, 16, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80]


def _screw(d: float, need: float, use: str, qty: int) -> dict[str, Any]:
    length = next((s for s in SCREW_LENGTHS if s >= need), SCREW_LENGTHS[-1])
    mass = 0.35 * d * d * length / 9 * 0.25 + 0.2
    return {
        "item": f"M{d:g} x {length} socket head screw",
        "quantity": qty,
        "mass_g": round(mass, 2),
        "price_eur": 0.12 if d <= 3 else 0.18,
        "use": use,
        "category": "fastener",
    }


def _hw(item: str, qty: int, use: str) -> dict[str, Any]:
    m, pr = HARDWARE_MASS[item]
    return {"item": item, "quantity": qty, "mass_g": m, "price_eur": pr, "use": use}


def _band(hours: float) -> str:
    lo = max(0.25, math.floor(hours * 0.8 * 4) / 4)
    hi = max(lo + 0.25, math.ceil(hours * 1.3 * 4) / 4)

    def f(h: float) -> str:
        return f"{h * 60:.0f} min" if h < 1 else f"{h:g} h"

    if hi < 1:
        return f"{lo * 60:.0f}-{hi * 60:.0f} min"
    return f"{f(lo)} - {f(hi)}"


def print_estimate(volume_mm3: float, area_mm2: float, profile: str) -> dict[str, Any]:
    """Mass and time from the CAD volume with a perimeter + infill model (estimates)."""
    prof = PRINT_PROFILES[profile]
    fil = FILAMENTS[prof["filament"]]
    wall = prof["walls"] * prof["line_mm"]
    shell = min(volume_mm3, area_mm2 * wall)
    extruded = shell + max(0.0, volume_mm3 - shell) * prof["infill"]
    mass = fil["density"] * extruded / 1000.0
    hours = extruded / fil["rate_mm3_s"] / 3600.0 * 1.25 + 0.1
    return {
        "mass_g": round(mass, 1),
        "print_time_h": round(hours, 2),
        "print_time_band": _band(hours),
        "extruded_cm3": round(extruded / 1000, 2),
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _section_points(solid: Any, y: float) -> np.ndarray:
    import cadquery as cq
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

    sec = BRepAlgoAPI_Section(solid.wrapped, gp_Pln(gp_Pnt(0, y, 0), gp_Dir(0, 1, 0)), False)
    sec.Build()
    edges = cq.Shape.cast(sec.Shape()).Edges()
    pts = [e.positionAt(t) for e in edges for t in np.linspace(0, 1, 801)]
    return np.array([(p.x, p.y, p.z) for p in pts])


def _exact_bounds(solid: Any) -> tuple[np.ndarray, np.ndarray]:
    """Bounding box from the exact geometry (no tolerance enlargement)."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(solid.wrapped, box, False, False)
    x0, y0, z0, x1, y1, z1 = box.Get()
    return np.array([x0, y0, z0]), np.array([x1, y1, z1])


def _chord_from_section(pts: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    from scipy.spatial import ConvexHull
    from scipy.spatial.distance import pdist, squareform

    xz = pts[:, [0, 2]]
    hull = pts[ConvexHull(xz).vertices]
    d = squareform(pdist(hull))
    i, j = np.unravel_index(np.argmax(d), d.shape)
    a, b = hull[i], hull[j]
    le, te = (a, b) if a[0] < b[0] else (b, a)
    return float(d[i, j]), le, te


def geometry_agreement(model: CadModel, wing_pieces: list[Any]) -> dict[str, Any]:
    """Measure the CAD wing (right panel) and compare with the geometry module (mm)."""
    g = model.geometry["wing"]
    w = model.params["wing"]
    semi = w["span_mm"] / 2
    sw = math.tan(math.radians(w["sweep_deg"]))
    dih = math.tan(math.radians(w["dihedral_deg"]))

    def measure(y: float) -> dict[str, float]:
        for pc in wing_pieces:
            if pc.span[0] - 1e-6 <= y <= pc.span[1] + 1e-6:
                pts = _section_points(pc.solid, y)
                chord, le, te = _chord_from_section(pts)
                qc = le + 0.25 * (te - le)
                return {"chord": chord, "qc_x": float(qc[0]), "qc_z": float(qc[2])}
        raise CadError(f"no wing piece at y = {y}")

    def expect(y: float) -> dict[str, float]:
        c = w["root_chord_mm"] - (w["root_chord_mm"] - w["tip_chord_mm"]) * y / semi
        return {"chord": c, "qc_x": w["x_le_mm"] + y * sw + c / 4, "qc_z": w["z_mm"] + y * dih}

    stations = {
        "root (fuselage side)": model.wing_root_y + 0.05,
        "MAC station": g["mac_y_mm"],
        "tip": semi - 0.05,
    }
    rows = {}
    max_diff = 0.0
    for name, y in stations.items():
        m, e = measure(y), expect(y)
        diffs = {k: abs(m[k] - e[k]) for k in m}
        max_diff = max(max_diff, *diffs.values())
        rows[name] = {
            "y_mm": round(y, 3),
            "cad": {k: round(v, 3) for k, v in m.items()},
            "geometry": {k: round(v, 3) for k, v in e.items()},
            "max_diff_mm": round(max(diffs.values()), 3),
        }
    # Planform from the measured chords (linear taper extrapolated to the centreline).
    r, t = rows["root (fuselage side)"], rows["tip"]
    y_r, y_t = r["y_mm"], t["y_mm"]
    slope = (t["cad"]["chord"] - r["cad"]["chord"]) / (y_t - y_r)
    c0 = r["cad"]["chord"] - slope * y_r
    ct = c0 + slope * semi
    span_cad = 2 * max(_exact_bounds(pc.solid)[1][1] for pc in wing_pieces)
    taper = ct / c0
    mac = 2 / 3 * c0 * (1 + taper + taper * taper) / (1 + taper)
    mac_y = semi / 3 * (1 + 2 * taper) / (1 + taper)
    area = span_cad * (c0 + ct) / 2
    m_mac = rows["MAC station"]["cad"]
    summary = {
        "span_mm": {"cad": round(span_cad, 3), "geometry": w["span_mm"]},
        "root_chord_mm": {"cad": round(c0, 3), "geometry": w["root_chord_mm"]},
        "tip_chord_mm": {"cad": round(ct, 3), "geometry": w["tip_chord_mm"]},
        "mac_mm": {"cad": round(mac, 3), "geometry": round(g["mac_mm"], 3)},
        "mac_y_mm": {"cad": round(mac_y, 3), "geometry": round(g["mac_y_mm"], 3)},
        "ac_x_mm": {"cad": round(m_mac["qc_x"], 3), "geometry": round(g["ac_x_mm"], 3)},
        "area_m2": {"cad": round(area / 1e6, 6), "geometry": round(g["area_m2"], 6)},
    }
    for k, v in summary.items():
        if k != "area_m2":
            d = abs(v["cad"] - v["geometry"])
            v["diff_mm"] = round(d, 3)
            max_diff = max(max_diff, d)
    # Area as an equivalent length: span x mean chord difference.
    summary["area_m2"]["mean_chord_diff_mm"] = round(
        abs(summary["area_m2"]["cad"] - summary["area_m2"]["geometry"]) * 1e6 / w["span_mm"], 3
    )
    max_diff = max(max_diff, summary["area_m2"]["mean_chord_diff_mm"])
    return {
        "stations": rows,
        "planform": summary,
        "max_diff_mm": round(max_diff, 3),
        "tolerance_mm": 0.5,
    }


def generate_files(
    parameters: dict[str, Any],
    mission: dict[str, Any],
    settings: dict[str, Any] | None,
    *,
    analysis: dict[str, Any] | None = None,
    parts_selection: list[dict[str, Any]] | None = None,
    out_dir: str | Path,
    progress: Callable[[float, str], None] | None = None,
    project: dict[str, str] | None = None,
    mesh_tolerance_mm: float = 0.05,
) -> dict[str, Any]:
    """Build the CAD model, split it for the printer and write every file; returns the manifest.

    ``project`` (optional) fills the drawing title block: {project, version, date}.
    Raises :class:`CadError` (plain message) when the design cannot be built and
    :class:`EnvelopeError` when a piece would not fit the printer.
    """
    from app.cad import bom as bom_mod
    from app.cad import drawings, dxf, export_3mf, export_step, notes
    from app.cad import parts as P
    from app.cad import split as S
    from app.cad.export_stl import write_binary_stl

    t_start = time.perf_counter()
    timings: dict[str, float] = {}

    def report(frac: float, msg: str) -> None:
        if progress is not None:
            progress(round(min(max(frac, 0.0), 1.0), 3), msg)

    out = Path(out_dir)
    for sub in ("print/stl", "print/3mf", "cad", "drawings/dxf", "notes"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    report(0.0, "Building the parametric model")
    model = build_model(
        parameters, mission, settings, analysis=analysis, parts_selection=parts_selection
    )
    specs = P.part_specs(model)
    env, bed = model.envelope, model.bed
    timings["model_s"] = round(time.perf_counter() - t_start, 3)

    parts_manifest: list[dict[str, Any]] = []
    step_parts: list[dict[str, Any]] = []
    combined: list[tuple[export_3mf.MeshObject, int]] = []
    wing_right_pieces: list[Any] = []
    joiner_rods: list[dict[str, Any]] = []
    watertight_all = True
    for i, spec in enumerate(specs):
        t0 = time.perf_counter()
        report(0.03 + 0.72 * i / len(specs), f"Building and splitting: {spec.label}")
        pieces, plan = S.make_pieces(model, spec)
        prof = PRINT_PROFILES[spec.profile]
        filament = prof["filament"]
        color = FILAMENTS[filament]["color"]
        piece_entries = []
        objects = []
        for pc in pieces:
            S.orient_piece(pc, env, bed, mesh_tolerance_mm)
            import trimesh

            tm = trimesh.Trimesh(pc.vertices, pc.faces, process=False)
            watertight = bool(tm.is_watertight and tm.is_winding_consistent and tm.volume > 0)
            watertight_all &= watertight
            name = f"{spec.key}_{pc.number:02d}of{pc.count:02d}"
            stl = out / "print" / "stl" / f"{name}.stl"
            write_binary_stl(stl, pc.vertices, pc.faces, pc.label)
            vol = float(pc.solid.Volume())
            area = float(pc.solid.Area())
            est = print_estimate(vol, area, spec.profile)
            ext = pc.vertices.max(axis=0) - pc.vertices.min(axis=0)
            o = pc.orientation or {}
            for j in pc.joints:
                if (
                    j.get("side") == "pins"
                    and (j.get("channel") or {}).get("kind") == "carbon joiner rod"
                ):
                    ch = j["channel"]
                    joiner_rods.append(
                        {
                            "label": "Joiner rod",
                            "od_mm": ch["diameter_mm"],
                            "length_mm": ch["length_mm"],
                            "quantity": spec.quantity,
                        }
                    )
            piece_entries.append(
                {
                    "number": pc.number,
                    "count": pc.count,
                    "label": pc.label,
                    "stl": str(stl.relative_to(out)),
                    "size_mm": [round(float(v), 2) for v in ext],
                    "envelope_mm": list(env),
                    "fits": True,
                    "span_mm": [round(v, 2) for v in pc.span] if pc.span else None,
                    "orientation": {
                        "policy": o.get("policy"),
                        "description": o.get("description"),
                        "reason": o.get("reason"),
                        "tilt_deg": o.get("tilt_deg", 0.0),
                        "lean_deg": o.get("lean_deg", 0.0),
                        "turn_deg": o.get("turn_deg", 0.0),
                        "rotation": np.round(o["rotation"], 6).tolist(),
                        "translation_mm": np.round(o["translation"], 4).tolist(),
                    },
                    "filament": filament,
                    "volume_cm3": round(vol / 1000, 2),
                    "surface_cm2": round(area / 100, 1),
                    **est,
                    "watertight": watertight,
                    "triangles": len(pc.faces),
                    "joints": pc.joints,
                }
            )
            obj = export_3mf.MeshObject(pc.label, color, filament, pc.vertices, pc.faces)
            objects.append(obj)
            combined.append((obj, spec.quantity))
        # One 3MF per part (all its pieces x copies).
        part_3mf = out / "print" / "3mf" / f"{spec.key}.3mf"
        _write_plated_3mf(part_3mf, [(o, spec.quantity) for o in objects], env, bed, spec.label)
        # STEP of the part (pieces in aircraft coordinates).
        step_path = out / "cad" / f"{spec.key}.step"
        export_step.write_part_step(
            step_path, spec.label, [(pc.label, pc.solid) for pc in pieces], color
        )
        step_parts.append(
            {
                "name": spec.key,
                "color": color,
                "pieces": [(pc.label, pc.solid) for pc in pieces],
                "instances": spec.instances,
            }
        )
        if spec.key == "wing_right":
            wing_right_pieces = pieces
        parts_manifest.append(
            {
                "key": spec.key,
                "label": spec.label,
                "description": spec.description,
                "profile": spec.profile,
                "filament": filament,
                "quantity": spec.quantity,
                "instances": [
                    {"rotation": np.round(r, 6).tolist(), "translation_mm": np.round(t, 4).tolist()}
                    for r, t in spec.instances
                ]
                or [{"rotation": np.eye(3).tolist(), "translation_mm": [0.0, 0.0, 0.0]}],
                "instances_note": "placement of each printed copy in aircraft coordinates: "
                "x_aircraft = R x_part + t, where the part frame is the piece STEP frame",
                "split": plan,
                "pieces": piece_entries,
                "mass_g_each": round(sum(p["mass_g"] for p in piece_entries), 1),
                "files": {
                    "3mf": str(part_3mf.relative_to(out)),
                    "step": str(step_path.relative_to(out)),
                    "notes": f"notes/{spec.key}.md",
                },
            }
        )
        for pc in pieces:  # release meshes held by the piece; the solids stay for the assembly
            pc.vertices = None
            pc.faces = None
        gc.collect()
        timings[f"part_{spec.key}_s"] = round(time.perf_counter() - t0, 3)

    report(0.76, "Checking the CAD against the geometry module")
    agreement = geometry_agreement(model, wing_right_pieces)

    report(0.78, "Combined 3MF on printer plates")
    t0 = time.perf_counter()
    all_3mf = out / "print" / "all_pieces.3mf"
    plates = _write_plated_3mf(all_3mf, combined, env, bed, "All printed pieces")
    check_3mf = export_3mf.validate_3mf(all_3mf)
    del combined
    gc.collect()
    timings["combined_3mf_s"] = round(time.perf_counter() - t0, 3)

    report(0.82, "Assembly STEP")
    t0 = time.perf_counter()
    tube_list = P.tubes(model)
    assy = export_step.build_assembly(step_parts, tube_list, P.assembly_extras(model))
    assy_path = out / "cad" / "assembly.step"
    export_step.write_assembly_step(assy_path, assy)
    del assy
    gc.collect()
    step_check = export_step.reimport_step(assy_path)
    timings["assembly_step_s"] = round(time.perf_counter() - t0, 3)

    report(0.9, "Drawings, DXF, bill of materials and notes")
    t0 = time.perf_counter()
    drawings_path = out / "drawings" / "drawings.pdf"
    drawings.write_drawings(drawings_path, model, parts_manifest, project)
    dxf_files = dxf.write_dxfs(out / "drawings" / "dxf", model)
    hardware = _hardware(model, specs)
    extra_rods = _extra_rods(model) + joiner_rods
    rows, totals = bom_mod.build_bom(
        model, parts_manifest, tube_list, extra_rods, hardware, parts_selection
    )
    bom_path = out / "bom.csv"
    bom_mod.write_bom(bom_path, rows, totals)
    notes_pdf = out / "notes" / "printing_notes.pdf"
    notes.write_notes(out / "notes", parts_manifest, notes_pdf)
    timings["documents_s"] = round(time.perf_counter() - t0, 3)

    # Release the solids before the file listing.
    step_parts.clear()
    wing_right_pieces = []
    gc.collect()

    files = []
    for path in sorted(p for p in out.rglob("*") if p.is_file() and p.name != "manifest.json"):
        ext = path.suffix.lstrip(".").lower()
        rel = str(path.relative_to(out))
        group = (
            "print"
            if rel.startswith("print/")
            else "cad"
            if rel.startswith("cad/")
            else "drawings"
            if rel.startswith("drawings/")
            else "notes"
            if rel.startswith("notes/")
            else "bom"
        )
        files.append(
            {
                "path": rel,
                "group": group,
                "kind": ext,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
                "opens_with": FILE_HELP.get(ext, ""),
            }
        )
    all_pieces = [pc for part in parts_manifest for pc in part["pieces"]]
    largest = max(all_pieces, key=lambda pc: max(pc["size_mm"]))
    timings["total_s"] = round(time.perf_counter() - t_start, 3)
    g = model.geometry
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "units": "mm",
        "coordinate_system": "origin at the nose tip, x aft, y starboard, z up (PHASE2.md s.1)",
        "printer": {
            "name": model.settings["printer"]["name"],
            "usable_envelope_mm": list(env),
            "bed_mm": list(bed),
        },
        "design": {
            "layout": model.layout,
            "tail": model.params["tail"]["type"],
            "span_mm": model.params["wing"]["span_mm"],
            "wing_area_m2": g["wing"]["area_m2"],
            "mac_mm": g["wing"]["mac_mm"],
            "fuselage_length_mm": model.params["fuselage"]["length_mm"],
        },
        "construction": {
            "shell_wall_mm": 0.8,
            "shell_note": "LW-PLA shells: two 0.4 mm perimeters (0.8 mm); wing and tail "
            "surfaces are solid CAD bodies printed with 2 perimeters and sparse gyroid infill, "
            "the fuselage and nose bay are 0.8 mm CAD shells.",
            "spar": {k: v for k, v in model.spar.items() if k not in ("axis_start", "axis_end")},
            "spar_position": "25 % chord on the local mid-thickness line; channel = tube + 0.3 mm",
            "tail_spar_mm": model.tail_spar_od,
            "boom": model.boom,
            "tail_boom": model.tail_boom,
            "lift_motor": {k: v for k, v in model.lift_motor.items() if k != "pattern"},
            "pusher_motor": None
            if model.pusher_motor is None
            else {k: v for k, v in model.pusher_motor.items() if k != "pattern"},
            "tilt_servo": model.tilt_servo,
            "control_servo": model.control_servo,
            "parts_selection": "Phase 4 selection" if parts_selection else PHASE4_NOTE,
        },
        "parts": parts_manifest,
        "tubes": [
            {
                "key": t["key"],
                "label": t["label"],
                "od_mm": t["od_mm"],
                "wall_mm": t["wall_mm"],
                "cut_length_mm": round(t["length_mm"], 1),
            }
            for t in tube_list
        ],
        "plates": plates,
        "dxf": [
            {
                **{k: v for k, v in d.items() if k != "path"},
                "path": str(Path(d["path"]).relative_to(out)),
            }
            for d in dxf_files
        ],
        "bom": {"path": "bom.csv", "columns": bom_mod.COLUMNS, "totals": totals},
        "files": files,
        "checks": {
            "all_pieces_fit": all(pc["fits"] for pc in all_pieces),
            "pieces": len(all_pieces),
            "largest_piece": {"label": largest["label"], "size_mm": largest["size_mm"]},
            "watertight_all": watertight_all,
            "geometry_agreement": agreement,
            "combined_3mf": {k: v for k, v in check_3mf.items() if k != "names"},
            "assembly_step": step_check,
            "drawing_pages": drawings.DRAWING_PAGES,
        },
        "warnings": model.warnings,
        "timings_s": timings,
        "peak_rss_mb": round(_peak_rss_mb(), 1),
    }
    (out / "manifest.json").write_text(json.dumps(_jsonable(manifest), indent=1), encoding="utf-8")
    report(1.0, "Done")
    return _jsonable(manifest)


PHASE4_NOTE = (
    "No Phase 4 parts selection: generic motor, servo and battery sizes from the engine's mass "
    "model are used for mounts, pockets and the BOM (to be selected in Phase 4)."
)


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_jsonable(x) for x in v]
    if isinstance(v, np.ndarray):
        return _jsonable(v.tolist())
    if isinstance(v, np.floating | float):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, np.integer):
        return int(v)
    return v


def _write_plated_3mf(
    path: Path,
    objects: list[tuple[Any, int]],
    env: tuple[float, float, float],
    bed: tuple[float, float],
    title: str,
) -> dict[str, Any]:
    from app.cad import export_3mf

    objs = [o for o, _ in objects]
    footprints, owners = [], []
    for k, (o, qty) in enumerate(objects):
        lo, hi = o.vertices.min(axis=0), o.vertices.max(axis=0)
        for _ in range(qty):
            footprints.append((float(hi[0] - lo[0]), float(hi[1] - lo[1])))
            owners.append(k)
    slots, n_plates = export_3mf.arrange_plates(footprints, bed, (env[0], env[1]))
    items = []
    plate_list: list[list[str]] = [[] for _ in range(n_plates)]
    for slot, k in zip(slots, owners, strict=True):
        o = objs[k]
        lo = o.vertices.min(axis=0)
        px, py = export_3mf.plate_origin(slot["plate"], n_plates, bed)
        t = np.eye(4)
        t[:3, 3] = [px + slot["x"] - lo[0], py + slot["y"] - lo[1], -lo[2]]
        items.append((k, t))
        plate_list[slot["plate"]].append(o.name)
    export_3mf.write_3mf(
        path,
        objs,
        items,
        title,
        {"Plates": str(n_plates), "PlateSpacingMM": "5"},
    )
    return {"count": n_plates, "bed_mm": list(bed), "spacing_mm": 5.0, "pieces": plate_list}


def _hardware(model: CadModel, specs: list[Any]) -> list[dict[str, Any]]:
    keys = {s.key: s for s in specs}
    hw: list[dict[str, Any]] = []
    hw.append(_screw(3, 10, "nose-bay flange", 4))
    hw.append(_hw("M3 heat-set insert (M3 x 5.7)", 8, "nose-bay flange and payload bosses"))
    hw.append(_screw(3, 8, "nose-bay payload base", 4))
    hw.append(_screw(3, model.spar["outer_mm"] + 14, "wing clamp cross bolts", 2))
    hw.append(_hw("M3 nyloc nut", 2, "wing clamp cross bolts"))
    from app.cad.parts import boom_clamp_geometry

    geo = boom_clamp_geometry(model, "right")
    rb, zb = model.boom["diameter_mm"] / 2, model.boom["z_mm"]
    clamp_h = max(geo["zmax"], zb + rb) + 2.5 - (zb - rb - 3.0) + 4.0
    hw.append(_screw(3, clamp_h, "boom-to-wing clamps", 8))
    hw.append(_hw("M3 nyloc nut", 8, "boom-to-wing clamps"))
    screw = model.lift_motor["pattern"]["screw_mm"]
    hw.append(_screw(screw, 8, "lift motors", 16))
    mounts = sum(s.quantity for k, s in keys.items() if k.startswith("motor_mount"))
    if mounts:
        hw.append(_hw("M3 grub screw x 6", mounts, "motor mount set screws"))
    if "tilt_hinge" in keys:
        pin = 4 if model.lift_motor["mass_g"] > 70 else 3
        hw.append(_screw(pin, 16 + 8 + 4, "tilt hinge pins", 2))
        hw.append(_hw("M3 nyloc nut", 2, "tilt hinge pins"))
        hw.append(_hw("M2 pushrod with 2 ball links", 2, "tilt servo linkage"))
    servos = 4 + (2 if model.tilt_servo else 0)
    hw.append(_hw("M2 x 8 screw (servo)", 2 * servos, "servo mounting"))
    if "tail_mount" in keys:
        n = keys["tail_mount"].quantity
        tube = model.tail_boom["diameter_mm"] if model.tail_boom else model.boom["diameter_mm"]
        hw.append(_screw(3, tube + 12, "tail mount clamp", 2 * n))
        hw.append(_hw("M3 nyloc nut", 2 * n, "tail mount clamp"))
    if model.pusher_motor is not None:
        hw.append(_screw(model.pusher_motor["pattern"]["screw_mm"], 10, "pusher motor", 4))
    return hw


def _extra_rods(model: CadModel) -> list[dict[str, Any]]:
    rods = []
    sleeve = model.spar.get("sleeve")
    if sleeve:
        rods.append(
            {
                "label": "Spar splice sleeve",
                "od_mm": sleeve["od_mm"],
                "length_mm": sleeve["length_mm"],
                "quantity": 2,
            }
        )
    s = model.surfaces["wing_right"]
    if s.incidence_pin:
        rods.append(
            {
                "label": "Incidence pin",
                "od_mm": s.incidence_pin["diameter_mm"],
                "length_mm": s.incidence_pin["depth"] + 8.0,
                "quantity": 2,
            }
        )
    return rods
