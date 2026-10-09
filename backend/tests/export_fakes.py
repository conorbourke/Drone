"""A fast stand-in for :func:`app.cad.generate_files`, used through the ``EXPORT_FAKE_GENERATOR``
test seam (it runs in the export child process, so it cannot be monkeypatched).

The behaviour is chosen by the project name in the title block (``project["project"]``):

* ``"fail-cad"`` / ``"fail-envelope"``: raise :class:`CadError` / :class:`EnvelopeError`;
* ``"crash"``: raise a ``RuntimeError`` (an internal error);
* ``"die"``: exit the process without an answer;
* ``"slow"``: report progress for up to 60 s (for the timeout and cancel tests);
* ``"hog"``: hold about 400 MB of memory for up to 30 s (for the memory guard);
* anything else: write a small but complete file set and its manifest at once.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import struct
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.cad.model import CadError, EnvelopeError

FILE_GROUPS = {"print": "print", "cad": "cad", "drawings": "drawings", "notes": "notes"}


def _cube_stl(path: Path, size: float, name: str) -> None:
    s = size
    v = [(0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0), (0, 0, s), (s, 0, s), (s, s, s), (0, s, s)]
    faces = [
        (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
        (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
    ]  # fmt: skip
    data = bytearray(f"fake piece {name}".encode().ljust(80, b" "))
    data += struct.pack("<I", len(faces))
    for f in faces:
        data += struct.pack("<3f", 0.0, 0.0, 0.0)
        for i in f:
            data += struct.pack("<3f", *v[i])
        data += struct.pack("<H", 0)
    path.write_bytes(bytes(data))


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
    mode = (project or {}).get("project", "")
    report = progress or (lambda f, m: None)
    report(0.0, "Building the parametric model")
    if mode == "fail-cad":
        raise CadError("The wing is too thin at the tip for the 10 mm spar tube.")
    if mode == "fail-envelope":
        raise EnvelopeError("Piece Wing R 1/2 (250 x 120 x 30 mm) does not fit the printer.")
    if mode == "crash":
        raise RuntimeError("boom")
    if mode == "die":
        os._exit(3)
    if mode == "slow":
        for i in range(600):
            report(i / 600, f"Slow step {i}")
            time.sleep(0.1)
    if mode == "hog":
        hog = bytearray(400 * 1024 * 1024)
        for i in range(0, len(hog), 4096):
            hog[i] = 1
        for i in range(300):
            report(0.5, f"Holding memory {i}")
            time.sleep(0.1)
    out = Path(out_dir)
    for sub in ("print/stl", "print/3mf", "cad", "drawings/dxf", "notes"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    report(0.3, "Building and splitting: Wing panel, right")
    pieces = []
    for n in (1, 2):
        rel = f"print/stl/wing_right_{n:02d}of02.stl"
        _cube_stl(out / rel, 50.0 * n, f"Wing R {n}/2")
        pieces.append(
            {
                "number": n,
                "count": 2,
                "label": f"Wing R {n}/2",
                "stl": rel,
                "size_mm": [50.0 * n] * 3,
                "envelope_mm": [240.0, 240.0, 240.0],
                "fits": True,
                "orientation": {"policy": "le_down", "description": "leading edge down"},
                "filament": "LW-PLA",
                "mass_g": 10.0 * n,
                "triangles": 12,
            }
        )
    (out / "print/3mf/wing_right.3mf").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    (out / "print/all_pieces.3mf").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    (out / "cad/wing_right.step").write_text("ISO-10303-21;\nEND-ISO-10303-21;\n")
    (out / "cad/assembly.step").write_text("ISO-10303-21;\nEND-ISO-10303-21;\n")
    (out / "drawings/drawings.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (out / "drawings/dxf/servo_plate.dxf").write_text("0\nSECTION\n0\nENDSEC\n0\nEOF\n")
    report(0.9, "Drawings, DXF, bill of materials and notes")
    with (out / "bom.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["item", "role", "manufacturer", "model", "quantity", "unit mass g"])
        for item in parts_selection or []:
            w.writerow(
                [
                    item.get("label"),
                    item.get("role"),
                    item.get("manufacturer"),
                    item.get("model"),
                    item.get("quantity"),
                    item.get("mass_g"),
                ]
            )
    (out / "notes/wing_right.md").write_text("# Wing panel, right\n", encoding="utf-8")
    (out / "notes/printing_notes.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    files = []
    for path in sorted(p for p in out.rglob("*") if p.is_file() and p.name != "manifest.json"):
        rel = str(path.relative_to(out))
        files.append(
            {
                "path": rel,
                "group": FILE_GROUPS.get(rel.split("/")[0], "bom"),
                "kind": path.suffix.lstrip("."),
                "size_bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "opens_with": "",
            }
        )
    manifest = {
        "schema": "vtol-files/1",
        "printer": {
            "name": "Fake printer",
            "usable_envelope_mm": [240.0, 240.0, 240.0],
            "bed_mm": [256.0, 256.0],
        },
        "parts": [
            {
                "key": "wing_right",
                "label": "Wing panel, right",
                "quantity": 1,
                "pieces": pieces,
                "files": {"3mf": "print/3mf/wing_right.3mf", "step": "cad/wing_right.step"},
            }
        ],
        "plates": {"count": 1},
        "bom": {"path": "bom.csv", "totals": {"rows": len(parts_selection or [])}},
        "files": files,
        "checks": {"all_pieces_fit": True, "pieces": 2, "watertight_all": True},
        "warnings": [],
        "fake": {
            "analysis": analysis,
            "parts_roles": [i.get("role") for i in parts_selection or []],
            "project": project,
            "mesh_tolerance_mm": mesh_tolerance_mm,
            "layout": parameters.get("layout"),
            "settings_schema": (settings or {}).get("schema_version"),
            "pid": os.getpid(),
        },
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    report(1.0, "Done")
    return manifest


# ---------------------------------------------------------------------------
# Phase 7 mould sets
# ---------------------------------------------------------------------------

_MOULD_LABELS = {
    "nose": ("Nose bay shell", ("right", "left"), "NOSE"),
    "fuselage": ("Fuselage shell", ("right", "left"), "FUS"),
    "wing_root_fairing": (
        "Wing-root fairing (right; the left is its mirror image)",
        ("upper", "lower"),
        "WRF",
    ),
}


def _fake_mould_part(key: str, out: Path, min_draft: float) -> tuple[dict[str, Any], list]:
    label, halves, prefix = _MOULD_LABELS[key]
    tiles_per_half = 2 if key == "fuselage" else 1
    (out / key / "tiles").mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []

    def add(rel: str, kind: str, **extra: Any) -> None:
        files.append(
            {
                "path": rel,
                "kind": kind,
                "size_bytes": (out / rel).stat().st_size,
                "part": key,
                **extra,
            }
        )

    halves_out = []
    for half in halves:
        letter = half[0].upper()
        tiles = []
        for i in range(1, tiles_per_half + 1):
            stem = f"{prefix}-{letter}-{i:02d}of{tiles_per_half:02d}"
            tile_label = f"{prefix}-{letter} {i}/{tiles_per_half}"
            stl = f"{key}/tiles/{stem}.stl"
            tmf = f"{key}/tiles/{stem}.3mf"
            _cube_stl(out / stl, 60.0 + 10 * i, tile_label)
            (out / tmf).write_bytes(b"PK\x05\x06" + b"\0" * 18)
            add(stl, "stl", tile=tile_label)
            add(tmf, "3mf", tile=tile_label)
            tiles.append(
                {
                    "label": tile_label,
                    "index": i,
                    "count": tiles_per_half,
                    "size_mm": [60.0 + 10 * i, 60.0 + 10 * i, 60.0 + 10 * i],
                    "fits": True,
                    "watertight": True,
                    "envelope_mm": [240.0, 240.0, 240.0],
                    "estimated_mass_g": 120.0 * i,
                    "estimated_print_time": "3.5-6 h",
                    "filament": "PETG",
                    "print_orientation": {
                        "name": "back down",
                        "reason": "Lying on its back: the mould face points up and is built "
                        "from perimeters; no supports on it.",
                        "supports": "none",
                    },
                    "notes": [
                        "PETG: 8 perimeters (about 3.5 mm): 100 % perimeters at the mould face.",
                        "Write the label on the back with a marker after printing.",
                    ],
                    "files": {"stl": stl, "3mf": tmf},
                    "half": half,
                }
            )
        step = f"{key}/{key}_mould_{half}.step"
        (out / step).write_text("ISO-10303-21;\nEND-ISO-10303-21;\n")
        add(step, "step", half=half)
        halves_out.append(
            {
                "key": half,
                "label": f"{label}: {half} half",
                "letter": letter,
                "pull_note": f"Lift this half off along its pull direction ({half}).",
                "tiles": tiles,
                "step_file": step,
                "demould": {"undercut_fraction": 0.0, "demouldable": True},
                "draft_min_outside_band_deg": 0.0 if key != "wing_root_fairing" else 2.5,
            }
        )
    pdf = f"{key}/{key}_mould_sheet.pdf"
    (out / pdf).write_bytes(b"%PDF-1.4\n%%EOF\n")
    add(pdf, "pdf")
    flagged = [] if key == "wing_root_fairing" else [2]
    n = 12
    part = {
        "key": key,
        "label": label,
        "description": f"Fake {label.lower()} for tests.",
        "net_part": "x = 0 to 180 mm",
        "open_ends": ["rear (x = 180 mm)"],
        "notes": [],
        "halves": halves_out,
        "parting_line": {
            "description": f"Plane through the maximum silhouette: {halves[0]} and {halves[1]} "
            "halves.",
            "silhouette_deviation_mm": 0.2,
            "length_mm": 520.0,
        },
        "draft": {
            "min_draft_deg": min_draft,
            "parting_band_mm": 3.0,
            "summary": {
                "faces": 3,
                "flagged": len(flagged),
                "area_mm2": 60000.0,
                "fraction_below_min": 0.25,
                "fraction_below_min_outside_band": 0.2 if flagged else 0.0,
                "undercut_fraction": 0.0,
                "min_draft_outside_band_deg": {h: 0.0 for h in halves},
            },
            "faces": [],
            "flagged_faces": flagged,
            "flagged_detail": [
                {
                    "face": f,
                    "where": "x 0-148, y -27-27, z 3-62 mm (centre 66, 0, 45)",
                    "min_draft_deg": 0.0,
                    "area_below_min_mm2": 6200.0,
                }
                for f in flagged
            ],
            "note": "Flagged faces are left as designed (the part surface is not altered).",
            "map": {
                "points_uv_mm": [[10.0 * i, 5.0 * (i % 3)] for i in range(n)],
                "h_mm": [2.0] * n,
                "draft_deg": [0.5 * i for i in range(n)],
                "half": [halves[i % 2] for i in range(n)],
                "in_band": [i == 0 for i in range(n)],
            },
        },
        "mould": {
            "wall_mm": 6.0,
            "flange_width_mm": 25.0,
            "bolt": "M5",
            "bolt_pitch_mm": 60.0,
            "registration_keys": {"type": "tapered cones", "positions_mm": [[0, 0, 0]]},
            "trim_line": {"offset_mm": 5.0},
            "laminate_allowance": {
                "direction": "inward from the mould face",
                "laminate_mm": 0.37,
                "layup": "2 x carbon 160 g/m² plain weave (3K)",
            },
        },
        "tiling": {"tiles_per_half": tiles_per_half, "cuts_mm": [], "joints": []},
        "print_notes": {"filament": "PETG", "finishing": ["Sand the mould face."]},
        "assembly_order": [
            f"Print all {tiles_per_half * 2} tiles in PETG.",
            "Bolt and bond the tiles of each half.",
        ],
        "files": {"pdf": pdf},
    }
    return part, files


def generate_moulds(
    parameters: dict[str, Any],
    mission: dict[str, Any],
    settings: dict[str, Any] | None,
    *,
    parts: tuple[str, ...] = ("nose", "fuselage", "wing_root_fairing"),
    out_dir: str | Path,
    progress: Callable[[float, str], None] | None = None,
    options: dict[str, Any] | None = None,
    project: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Stand-in for :func:`app.cad.moulds.generate_moulds` (``MOULD_FAKE_GENERATOR``); the same
    project-name modes as :func:`generate_files`."""
    mode = (project or {}).get("project", "")
    report = progress or (lambda f, m: None)
    report(0.0, "Building the model")
    if mode == "fail-cad":
        raise CadError("The fuselage is too short for a nose bay mould.")
    if mode == "crash":
        raise RuntimeError("boom")
    if mode == "die":
        os._exit(3)
    if mode == "slow":
        for i in range(600):
            report(i / 600, f"Slow step {i}")
            time.sleep(0.1)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    opts = {"min_draft_deg": 2.0, "vent_channels": False, **(options or {})}
    parts_out, files = [], []
    for k, key in enumerate(parts):
        report(0.1 + 0.8 * k / max(1, len(parts)), f"{_MOULD_LABELS[key][0]}: tiles")
        part, part_files = _fake_mould_part(key, out, float(opts["min_draft_deg"]))
        parts_out.append(part)
        files += part_files
    manifest = {
        "schema": "vtol-moulds/1",
        "parts": parts_out,
        "summary": [
            {
                "part": p["key"],
                "label": p["label"],
                "halves": [h["key"] for h in p["halves"]],
                "tiles_per_half": p["tiling"]["tiles_per_half"],
                "tiles": sum(len(h["tiles"]) for h in p["halves"]),
                "all_tiles_fit": True,
                "demouldable": True,
                "flagged_faces": len(p["draft"]["flagged_faces"]),
                "min_draft_outside_band_deg": p["draft"]["summary"]["min_draft_outside_band_deg"],
            }
            for p in parts_out
        ],
        "options": opts,
        "envelope_mm": [240.0, 240.0, 240.0],
        "printer": "Fake printer",
        "files": files,
        "total_bytes": sum(f["size_bytes"] for f in files),
        "duration_s": 0.1,
        "peak_rss_mb": 50.0,
        "notes": ["Moulds take the outer mould line (OML); the laminate grows inward."],
        "fake": {
            "parts": list(parts),
            "options": options,
            "project": project,
            "scale": mission.get("scale"),
            "layout": parameters.get("layout"),
            "pid": os.getpid(),
        },
    }
    (out / "moulds_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    report(1.0, "Done")
    return manifest
