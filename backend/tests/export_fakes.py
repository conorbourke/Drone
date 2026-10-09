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
