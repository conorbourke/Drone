"""STEP (AP214) export of parts and the assembly, and a re-import check with OpenCascade."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cadquery as cq
import numpy as np

from app.cad import parts as P


def _set_ap214() -> None:
    from OCP.Interface import Interface_Static

    Interface_Static.SetCVal_s("write.step.schema", "AP214IS")
    Interface_Static.SetCVal_s("write.step.unit", "MM")


def _color(hex_color: str) -> cq.Color:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return cq.Color(r, g, b, 1.0)


def _location(rot: np.ndarray, trans: np.ndarray) -> cq.Location:
    from OCP.gp import gp_Trsf

    u, _, vt = np.linalg.svd(np.asarray(rot, float))
    r = u @ vt
    t = np.asarray(trans, float)
    tr = gp_Trsf()
    tr.SetValues(
        *(float(v) for v in (r[0, 0], r[0, 1], r[0, 2], t[0])),
        *(float(v) for v in (r[1, 0], r[1, 1], r[1, 2], t[1])),
        *(float(v) for v in (r[2, 0], r[2, 1], r[2, 2], t[2])),
    )
    return cq.Location(tr)


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in name)


def write_part_step(path: Path, name: str, pieces: list[tuple[str, cq.Shape]], color: str) -> int:
    """One part: its pieces (aircraft coordinates) as named, coloured assembly children."""
    _set_ap214()
    assy = cq.Assembly(name=_safe(name))
    for label, solid in pieces:
        assy.add(solid, name="piece_" + _safe(label), color=_color(color))
    assy.export(str(path), "STEP")
    return path.stat().st_size


def build_assembly(
    parts: list[dict[str, Any]], tubes: list[dict[str, Any]], extras: list[tuple[str, Any, str]]
) -> cq.Assembly:
    """``parts``: {name, color, pieces: [(label, solid)], instances: [(rot, trans)]}."""
    root = cq.Assembly(name="aircraft")
    for part in parts:
        sub = cq.Assembly(name=_safe(part["name"]))
        for label, solid in part["pieces"]:
            sub.add(solid, name="piece_" + _safe(label), color=_color(part["color"]))
        instances = part.get("instances") or [(np.eye(3), np.zeros(3))]
        for i, (rot, trans) in enumerate(instances):
            root.add(sub, name=f"{_safe(part['name'])}_{i + 1}", loc=_location(rot, trans))
    for t in tubes:
        root.add(P.tube_solid(t), name=_safe(t["key"]), color=_color("#1C1C1C"))
    for name, solid, color in extras:
        root.add(solid, name=_safe(name), color=_color(color))
    return root


def write_assembly_step(path: Path, assy: cq.Assembly) -> int:
    _set_ap214()
    assy.export(str(path), "STEP")
    return path.stat().st_size


def reimport_step(path: Path) -> dict[str, Any]:
    """Read the file back with OpenCascade; count solids and run the shape checker."""
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    reader = STEPControl_Reader()
    status = reader.ReadFile(str(path))
    if status != IFSelect_RetDone:
        raise ValueError(f"STEP: {path.name} could not be read (status {status})")
    reader.TransferRoots()
    shape = reader.OneShape()
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    solids = 0
    while exp.More():
        solids += 1
        exp.Next()
    valid = BRepCheck_Analyzer(shape).IsValid()
    header = path.read_bytes()[:4000].decode("latin-1")
    schema = "AP214" if "AUTOMOTIVE_DESIGN" in header.upper() else "other"
    return {"solids": solids, "valid": bool(valid), "schema": schema}
