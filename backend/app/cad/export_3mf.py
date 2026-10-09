"""3MF writer and validator (3MF Core Specification 1.3, unit millimetre).

Package: ``[Content_Types].xml``, ``_rels/.rels`` (start part relationship to the model) and
``3D/3dmodel.model`` with one mesh object per piece (name and display colour through
``basematerials``) and build items carrying the placement transforms. Zip entries are written in
a fixed order with a fixed timestamp so the same input gives byte-identical files.

Plates: pieces are packed in rows onto plates of the usable bed area with 5 mm spacing; plates
are laid out on a grid whose pitch matches Bambu Studio's plate grid (bed size x 1.2, plate 1
at the origin, columns to +X, rows to -Y), so the slicer finds each piece on its plate.
"""

from __future__ import annotations

import math
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

import numpy as np

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
MODEL_REL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
CONTENT_MODEL = "application/vnd.ms-package.3dmanufacturing-3dmodel+xml"
CONTENT_RELS = "application/vnd.openxmlformats-package.relationships+xml"
ZIP_DATE = (1980, 1, 1, 0, 0, 0)
PLATE_PITCH_FACTOR = 1.2
SPACING_MM = 5.0


@dataclass
class MeshObject:
    name: str
    color: str  # "#RRGGBB"
    material: str
    vertices: np.ndarray  # print orientation, z >= 0
    faces: np.ndarray


def _fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def arrange_plates(
    footprints: list[tuple[float, float]],
    bed: tuple[float, float],
    usable: tuple[float, float],
    spacing: float = SPACING_MM,
) -> tuple[list[dict[str, Any]], int]:
    """Shelf packing. Returns per item {plate, x, y} (lower-left corner on the plate, mm, in
    bed coordinates) and the number of plates. Items are placed in order of decreasing depth."""
    ox = (bed[0] - usable[0]) / 2
    oy = (bed[1] - usable[1]) / 2
    order = sorted(range(len(footprints)), key=lambda i: (-footprints[i][1], -footprints[i][0], i))
    slots: list[dict[str, Any]] = [{} for _ in footprints]
    plate, x, y, row_h = 0, 0.0, 0.0, 0.0
    for i in order:
        w, d = footprints[i]
        if w > usable[0] + 1e-6 or d > usable[1] + 1e-6:
            raise ValueError(f"item {i} ({w:.1f} x {d:.1f} mm) is larger than the bed")
        if x > 0 and x + w > usable[0] + 1e-6:
            x, y, row_h = 0.0, y + row_h + spacing, 0.0
        if y > 0 and y + d > usable[1] + 1e-6:
            plate, x, y, row_h = plate + 1, 0.0, 0.0, 0.0
        slots[i] = {"plate": plate, "x": ox + x, "y": oy + y}
        x += w + spacing
        row_h = max(row_h, d)
    return slots, (plate + 1 if footprints else 0)


def plate_origin(plate: int, n_plates: int, bed: tuple[float, float]) -> tuple[float, float]:
    cols = max(1, math.ceil(math.sqrt(n_plates)))
    col, row = plate % cols, plate // cols
    return col * bed[0] * PLATE_PITCH_FACTOR, -row * bed[1] * PLATE_PITCH_FACTOR


def write_3mf(
    path: Path,
    objects: list[MeshObject],
    items: list[tuple[int, np.ndarray]],
    title: str,
    metadata: dict[str, str] | None = None,
) -> int:
    """``items``: (object index, 4x4 transform). Returns the file size."""
    meta = {"Title": title, "Application": "VTOL designer", **(metadata or {})}
    mats: list[tuple[str, str]] = []
    for o in objects:
        if (o.material, o.color) not in mats:
            mats.append((o.material, o.color))

    def model_chunks():
        yield (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE_NS}">\n'
        )
        for k in sorted(meta):
            yield f" <metadata name={quoteattr(k)}>{escape(meta[k])}</metadata>\n"
        yield ' <resources>\n  <basematerials id="1">\n'
        for name, color in mats:
            yield (f'   <base name={quoteattr(name)} displaycolor="{color.upper()}FF"/>\n')
        yield "  </basematerials>\n"
        for i, o in enumerate(objects):
            pindex = mats.index((o.material, o.color))
            yield (
                f'  <object id="{i + 2}" type="model" name={quoteattr(o.name)} pid="1" '
                f'pindex="{pindex}">\n   <mesh>\n    <vertices>\n'
            )
            yield "".join(
                f'     <vertex x="{_fmt(p[0])}" y="{_fmt(p[1])}" z="{_fmt(p[2])}"/>\n'
                for p in o.vertices
            )
            yield "    </vertices>\n    <triangles>\n"
            yield "".join(f'     <triangle v1="{a}" v2="{b}" v3="{c}"/>\n' for a, b, c in o.faces)
            yield "    </triangles>\n   </mesh>\n  </object>\n"
        yield " </resources>\n <build>\n"
        for idx, tr in items:
            t = np.asarray(tr, float)
            vals = [t[0, 0], t[1, 0], t[2, 0], t[0, 1], t[1, 1], t[2, 1], t[0, 2], t[1, 2]]
            vals += [t[2, 2], t[0, 3], t[1, 3], t[2, 3]]
            yield (
                f'  <item objectid="{idx + 2}" transform="{" ".join(_fmt(v) for v in vals)}"/>\n'
            )
        yield " </build>\n</model>\n"

    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        f'<Default Extension="rels" ContentType="{CONTENT_RELS}"/>'
        f'<Default Extension="model" ContentType="{CONTENT_MODEL}"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="{MODEL_REL}"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name, data in (
            ("[Content_Types].xml", content_types.encode()),
            ("_rels/.rels", rels.encode()),
        ):
            z.writestr(_info(name), data)
        with z.open(_info("3D/3dmodel.model"), "w") as fh:
            for chunk in model_chunks():
                fh.write(chunk.encode("utf-8"))
    return path.stat().st_size


def _info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def validate_3mf(path: Path) -> dict[str, Any]:
    """Check the package structure and the model XML; raise ValueError on any problem."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        for required in ("[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"):
            if required not in names:
                raise ValueError(f"3MF: missing {required}")
        ct = ET.fromstring(z.read("[Content_Types].xml"))
        defaults = {
            e.get("Extension"): e.get("ContentType") for e in ct if e.tag.endswith("Default")
        }
        if defaults.get("rels") != CONTENT_RELS or defaults.get("model") != CONTENT_MODEL:
            raise ValueError("3MF: [Content_Types].xml lacks the rels/model content types")
        rels = ET.fromstring(z.read("_rels/.rels"))
        targets = [r.get("Target") for r in rels if r.get("Type") == MODEL_REL]
        if targets != ["/3D/3dmodel.model"]:
            raise ValueError("3MF: _rels/.rels does not point at /3D/3dmodel.model")
        with z.open("3D/3dmodel.model") as fh:
            return _validate_model(fh)


def _validate_model(fh: Any) -> dict[str, Any]:
    """Streaming check of the model XML (iterparse keeps memory small for large plates)."""
    q = f"{{{CORE_NS}}}"
    objects: dict[str, str] = {}
    triangles = 0
    items = 0
    base_ok = False
    root_seen = False
    n_verts = 0
    oid = None
    obj_tris = 0
    for event, el in ET.iterparse(fh, events=("start", "end")):
        if event == "start":
            if not root_seen:
                root_seen = True
                if el.tag != q + "model":
                    raise ValueError("3MF: root element is not a core-namespace <model>")
                if el.get("unit") != "millimeter":
                    raise ValueError("3MF: unit is not millimeter")
            elif el.tag == q + "object":
                oid = el.get("id")
                if el.get("type") != "model" or not el.get("name"):
                    raise ValueError(f"3MF: object {oid} has no type=model or no name")
                objects[str(oid)] = str(el.get("name"))
                n_verts = 0
                obj_tris = 0
            continue
        tag = el.tag
        if tag == q + "vertex":
            n_verts += 1
        elif tag == q + "triangle":
            idx = [int(el.get(k, "-1")) for k in ("v1", "v2", "v3")]
            if min(idx) < 0 or max(idx) >= n_verts or len(set(idx)) != 3:
                raise ValueError(f"3MF: object {oid} has an invalid triangle")
            obj_tris += 1
            triangles += 1
        elif tag == q + "object":
            if n_verts < 4 or obj_tris < 4:
                raise ValueError(f"3MF: object {oid} has an empty mesh")
        elif tag == q + "base":
            base_ok = str(el.get("displaycolor", "")).startswith("#")
        elif tag == q + "item":
            items += 1
            if el.get("objectid") not in objects:
                raise ValueError("3MF: build item refers to a missing object")
            tr = el.get("transform")
            if tr is not None and len(tr.split()) != 12:
                raise ValueError("3MF: malformed build item transform")
        el.clear()
    if not base_ok:
        raise ValueError("3MF: missing base materials / display colours")
    if not items:
        raise ValueError("3MF: no build items")
    return {"objects": len(objects), "items": items, "triangles": triangles, "names": objects}
