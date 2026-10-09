"""Phase 7 moulds: two-part female moulds for the curved carbon parts, tiled for the printer.

Public entry point::

    generate_moulds(parameters, mission, settings, *,
                    parts=("nose", "fuselage", "wing_root_fairing"),
                    out_dir, progress=None, options=None) -> manifest dict

For each part (docs/phases/PHASE7.md section 1):

1. **Outer mould line.** The part's outer surface from the CAD kernel's model
   (``app.cad.moulds_geometry``): fuselage and nose bay from ``FuselageModel``, the wing-root
   fairing as a fillet between the wing root and the fuselage side. Moulds are for the outer
   mould line; the laminate grows inward (stated in the manifest with the layup thickness).
2. **Pull direction and parting line.** Each candidate pull direction (fuselage family: +y
   for left/right halves, +z for upper/lower; fairing: the wing chord-plane normal) is scored
   by the area below the minimum draft outside the parting band and by ray-cast undercuts;
   the parting plane sits at the maximum silhouette in that direction (computed from the
   surface samples, its deviation reported).
3. **Draft check.** Every face of the part is sampled; per face and half: minimum draft, area
   below the minimum (default 2 deg, ``options["min_draft_deg"]``), undercut area; faces with
   area below the minimum outside the parting band are flagged. The part surface is never
   altered.
4. **Mould halves.** Wall = the OML offset outward by 6 mm (true profile offsets), a flange in
   the parting plane 25 mm wide (10 mm thick) beyond the wall, M5 clearance holes at 60 mm
   pitch along the flange midline, tapered registration cones on the first half with matching
   sockets (0.2 mm clearance) in the second, a scribed trim line 5 mm outside the net edge
   (on the flange and as a ring groove across the cavity at open ends), optional vent/resin
   channel.
5. **Tiling.** Halves larger than the printer envelope are cut across the long axis into
   tiles that fit (checked on the exported mesh in the print orientation); cuts are placed
   where the surface curvature is lowest (never at a profile kink). At each cut both tiles
   get a bolting rib on the back (15 mm high, 8 mm thick) with M5 holes, lugs over the flange
   and tapered alignment keys/sockets on the joint face.
6. **Print orientation** per tile chosen from four candidates (either joint face, parting face
   or back down) by the area of mould face that would overhang more than 45 deg (it must stay
   clean), then other overhangs, then bed contact; notes for PETG/ASA, 100 % perimeters at the
   mould face, sanding, sealing/primer and release agent.
7. **Exports.** Binary STL and 3MF per tile (print orientation, on the bed), STEP (AP214) per
   mould half with its tiles in aircraft coordinates, and one PDF sheet per part (parting line,
   draft report, tile layout and assembly order). ``moulds_manifest.json`` in ``out_dir``.

Memory: one part, half and tile at a time; solids of a half are released after its STEP.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import math
import resource
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import cadquery as cq
import numpy as np

from app.cad import moulds_analysis as MA
from app.cad import parts as P
from app.cad.export_3mf import MeshObject, write_3mf
from app.cad.export_step import write_part_step
from app.cad.export_stl import write_binary_stl
from app.cad.model import FILAMENTS, CadError, CadModel, build_model
from app.cad.moulds_geometry import MouldSource, fairing_source, fuselage_family_sources

MANIFEST_SCHEMA = "vtol-moulds/1"
PART_KEYS = ("nose", "fuselage", "wing_root_fairing")
BIG = 4000.0

DEFAULT_OPTIONS: dict[str, Any] = {
    "min_draft_deg": 2.0,
    "wall_mm": 6.0,
    "flange_width_mm": 25.0,
    "flange_thickness_mm": 10.0,
    "bolt": "M5",
    "bolt_hole_mm": 5.5,
    "bolt_pitch_mm": 60.0,
    "key_base_mm": 10.0,
    "key_tip_mm": 7.0,
    "key_height_mm": 6.0,
    "key_clearance_mm": 0.2,
    "joint_key_base_mm": 8.0,
    "joint_key_tip_mm": 5.6,
    "joint_key_height_mm": 6.0,
    "rib_height_mm": 15.0,
    "rib_thickness_mm": 8.0,
    "trim_offset_mm": 5.0,
    "trim_groove_width_mm": 0.8,
    "trim_groove_depth_mm": 0.5,
    "extension_mm": 15.0,
    "vent_channels": False,
    "vent_offset_mm": 13.0,
    "vent_width_mm": 3.0,
    "vent_depth_mm": 1.5,
    "filament": "PETG",
    "mesh_tolerance_mm": 0.1,
    "demould_samples": 1000,
    "tile_margin_mm": 2.0,
    "min_tile_mm": 60.0,
    "draft_samples_per_face": 14,
}

PRINT_SETTINGS = {
    "PETG": {
        "nozzle_mm": 0.4,
        "layer_mm": "0.12-0.16 (fine layers reduce stair-stepping on shallow mould faces)",
        "walls": "8 perimeters (about 3.5 mm): 100 % perimeters at the mould face",
        "infill": "25 % gyroid behind the perimeters",
        "temperature_note": "PETG softens near 70 °C: room-temperature-cure epoxy only, no "
        "oven post-cure in the mould.",
    },
    "ASA": {
        "nozzle_mm": 0.4,
        "layer_mm": "0.12-0.16",
        "walls": "8 perimeters (about 3.5 mm): 100 % perimeters at the mould face",
        "infill": "25 % gyroid behind the perimeters",
        "temperature_note": "ASA (enclosed printer) keeps its shape to about 90 °C: allows a "
        "warm post-cure at 50-60 °C.",
    },
}

FINISHING_NOTES = [
    "Sand the mould face P120 -> P240 -> P400 (dry), fill layer lines with epoxy primer or "
    "2K polyester primer, sand P600 -> P1000 -> P1500 wet until no layer lines show.",
    "Seal the face (2-3 coats of mould sealer or epoxy surface coat) so resin cannot key into "
    "the plastic; let it cure fully.",
    "Release: 5-6 coats of mould-release wax (buff each), then a PVA film release for the "
    "first parts; reapply wax every few pulls.",
    "Moulds are for the outer mould line: the laminate grows inward from the mould face, so "
    "the part's outside matches the design and its wall eats into the inside.",
]

ProgressFn = Callable[[float, str], None]


# ---------------------------------------------------------------------------
# Frames and boxes
# ---------------------------------------------------------------------------


class Frame:
    """Mould frame: e1 long axis, d pull (towards half A), e2 = d x e1; h measured from the
    parting plane. Local (u, v, h) = (p . e1, p . e2, p . d - h0)."""

    def __init__(self, e1: np.ndarray, d: np.ndarray, h0: float):
        self.d = d / np.linalg.norm(d)
        e1 = e1 - self.d * float(e1 @ self.d)
        self.e1 = e1 / np.linalg.norm(e1)
        self.e2 = np.cross(self.d, self.e1)
        self.h0 = h0

    def local(self, p: np.ndarray) -> np.ndarray:
        p = np.atleast_2d(np.asarray(p, float))
        return np.column_stack([p @ self.e1, p @ self.e2, p @ self.d - self.h0])

    def world(self, u: float, v: float, h: float) -> np.ndarray:
        return self.e1 * u + self.e2 * v + self.d * (h + self.h0)

    def box(self, u0: float, u1: float, v0: float, v1: float, h0: float, h1: float) -> cq.Solid:
        (u0, u1), (v0, v1), (h0, h1) = sorted((u0, u1)), sorted((v0, v1)), sorted((h0, h1))
        c = self.world((u0 + u1) / 2, (v0 + v1) / 2, (h0 + h1) / 2)
        dims = (max(u1 - u0, 1e-3), max(v1 - v0, 1e-3), max(h1 - h0, 1e-3))
        return P.oriented_box(c, self.e1, self.e2, self.d, dims)

    def cylinder(self, a: Sequence[float], b: Sequence[float], dia: float) -> cq.Solid:
        return P.cylinder_between(self.world(*a), self.world(*b), dia)

    def cone(self, a: Sequence[float], b: Sequence[float], d0: float, d1: float) -> cq.Solid:
        return P.cone_between(self.world(*a), self.world(*b), d0, d1)


def _half_names(d: np.ndarray) -> tuple[str, str]:
    k = int(np.argmax(np.abs(d)))
    pos = d[k] > 0
    names = {0: ("aft", "forward"), 1: ("right", "left"), 2: ("upper", "lower")}[k]
    return names if pos else (names[1], names[0])


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


# ---------------------------------------------------------------------------
# Plane sections and 2D helpers
# ---------------------------------------------------------------------------


def plane_section(solid: cq.Shape, point: np.ndarray, normal: np.ndarray) -> cq.Face | None:
    """Largest face of the solid's section by a plane (None if the plane misses it)."""
    plane = cq.Face.makePlane(2 * BIG, 2 * BIG, P.V(point), P.V(normal))
    res = plane.intersect(solid)
    faces = res.Faces()
    if not faces:
        return None
    return max(faces, key=lambda f: f.Area())


def wire_points(wire: cq.Wire, step: float = 2.0) -> np.ndarray:
    n = max(16, int(wire.Length() / step))
    return np.array([wire.positionAt(i / n, "length").toTuple() for i in range(n)])


def _nurbs(face: cq.Face) -> cq.Face:
    """Convert a face's geometry to B-splines. Faces built on ``offset2D`` wires carry offset
    curves; extruded, they become surfaces that STEP readers drop, so they are converted first."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert

    conv = BRepBuilderAPI_NurbsConvert(face.wrapped, True)
    return cq.Face(cq.Shape.cast(conv.Shape()).wrapped)


def _ring_face(wire: cq.Wire, r_in: float, r_out: float) -> cq.Face | None:
    try:
        outer = wire.offset2D(r_out, "arc")[0]
        inner = wire.offset2D(r_in, "arc")[0] if r_in > 0 else wire
        return _nurbs(cq.Face.makeFromWires(outer, [inner]))
    except Exception:
        return None


def _runs(mask: np.ndarray) -> list[np.ndarray]:
    """Index runs of True in a closed (cyclic) boolean mask."""
    n = len(mask)
    if mask.all():
        return [np.arange(n)]
    if not mask.any():
        return []
    start = int(np.flatnonzero(~mask)[0])
    order = (np.arange(n) + start) % n
    m = mask[order]
    out, cur = [], []
    for i, ok in zip(order, m, strict=True):
        if ok:
            cur.append(i)
        elif cur:
            out.append(np.array(cur))
            cur = []
    if cur:
        out.append(np.array(cur))
    return out


def _spaced(points: np.ndarray, pitch: float, end_gap: float) -> list[int]:
    """Indices along an ordered polyline: first and last ``end_gap`` in, ~``pitch`` apart."""
    if len(points) < 2:
        return []
    seg = np.linalg.norm(np.diff(points, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    length = s[-1]
    if length < 2 * end_gap:
        return [int(np.argmin(np.abs(s - length / 2)))] if length > 6 else []
    n = max(1, math.ceil((length - 2 * end_gap) / pitch))
    targets = np.linspace(end_gap, length - end_gap, n + 1)
    return sorted({int(np.argmin(np.abs(s - t))) for t in targets})


# ---------------------------------------------------------------------------
# Tile planning
# ---------------------------------------------------------------------------


def max_tile_length(
    cross: tuple[float, float], envelope: Sequence[float], protrusion: float, margin: float
) -> float:
    """Longest tile (along the long axis) whose box fits the envelope in some orientation."""
    best = 0.0
    env = list(envelope)
    for k in range(3):
        others = [env[i] for i in range(3) if i != k]
        a, b = sorted(cross)
        o1, o2 = sorted(others)
        if a <= o1 and b <= o2:
            best = max(best, env[k] - protrusion - margin)
    return best


def plan_cuts(
    lo: float,
    hi: float,
    l_max: float,
    curvature: Callable[[float], float],
    kinks: Sequence[float],
    min_tile: float,
) -> list[float]:
    """Cut positions: as few tiles as fit, near equal lengths, each cut at the lowest
    curvature within +-20 % of the equal-length target and 15 mm clear of profile kinks."""
    length = hi - lo
    if length <= l_max:
        return []
    n = math.ceil(length / l_max)
    while n < 200:
        cuts: list[float] = []
        prev = lo
        ok = True
        for i in range(1, n):
            target = lo + i * length / n
            win = 0.2 * length / n
            a = max(prev + min_tile, target - win)
            b = min(prev + l_max, target + win, hi - min_tile)
            if b < a:
                ok = False
                break
            cand = np.arange(a, b + 1e-9, 2.0)
            if not len(cand):
                cand = np.array([a])
            pen = []
            for x in cand:
                k = curvature(float(x))
                if any(abs(x - kk) < 15.0 for kk in kinks):
                    k += 1e3
                pen.append(k + 1e-6 * abs(x - target))
            x = float(cand[int(np.argmin(pen))])
            cuts.append(x)
            prev = x
        if ok and hi - prev <= l_max + 1e-6:
            return cuts
        n += 1
    raise CadError("Could not tile the mould within the printer envelope.")


# ---------------------------------------------------------------------------
# Print orientation
# ---------------------------------------------------------------------------


def _rotation_down(down: np.ndarray, ref: np.ndarray) -> np.ndarray:
    z = -down / np.linalg.norm(down)
    x = ref - z * float(ref @ z)
    if np.linalg.norm(x) < 1e-6:
        x = np.array([1.0, 0, 0]) - z * z[0]
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    return np.vstack([x, y, z])


def choose_orientation(
    verts: np.ndarray,
    tris_by_face: list[tuple[np.ndarray, bool]],
    candidates: list[tuple[str, np.ndarray, str]],
    ref: np.ndarray,
    envelope: Sequence[float],
) -> dict[str, Any]:
    """Score each candidate (name, down vector, reason): mould-face overhang (> 45 deg) first,
    other overhang, then bed contact; only candidates that fit the envelope."""
    env = np.asarray(envelope, float)
    rows = []
    for name, down, reason in candidates:
        r = _rotation_down(down, ref)
        q = verts @ r.T
        ext = q.max(axis=0) - q.min(axis=0)
        zmin = float(q[:, 2].min())
        a_mf = a_ot = a_base = 0.0
        for tri, is_mould in tris_by_face:
            t = tri @ r.T
            n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
            area = 0.5 * np.linalg.norm(n, axis=1)
            ok = area > 1e-12
            nz = np.zeros(len(t))
            nz[ok] = n[ok, 2] / (2 * area[ok])
            on_bed = t[:, :, 2].max(axis=1) < zmin + 0.2
            over = (nz < -math.sin(math.radians(45))) & ~on_bed
            if is_mould:
                a_mf += float(area[over].sum())
            else:
                a_ot += float(area[over].sum())
            a_base += float(area[on_bed & (nz < -0.999)].sum())
        fits = bool(np.all(ext <= env + 1e-6))
        score = 5 * a_mf + a_ot - 0.2 * a_base + (1e7 if not fits else 0.0)
        rows.append(
            {
                "name": name,
                "reason": reason,
                "rotation": r,
                "size_mm": [round(float(e), 1) for e in ext],
                "fits": fits,
                "mould_face_overhang_mm2": round(a_mf, 0),
                "other_overhang_mm2": round(a_ot, 0),
                "bed_contact_mm2": round(a_base, 0),
                "score": score,
            }
        )
    best = min(rows, key=lambda r: r["score"])
    return {"best": best, "candidates": rows}


def tessellate_abs(solid: cq.Shape, tol: float) -> tuple[np.ndarray, np.ndarray]:
    """Watertight triangle mesh with an absolute chordal tolerance (mould faces must be
    accurate to a tenth of a millimetre). Retries finer meshes and a looser vertex weld if
    not watertight, then hole filling; the last resort is the kernel's relative mesher."""
    import trimesh

    from app.cad.split import tessellate

    def ok(m: trimesh.Trimesh) -> bool:
        return bool(m.is_watertight and m.is_winding_consistent)

    first = None
    for k in (1.0, 0.7, 0.5):
        MA.mesh_absolute(solid, tol * k, 0.15)
        vs, ts = solid.tessellate(tol * k, 0.15)
        v = np.array([(p.x, p.y, p.z) for p in vs], dtype=np.float64)
        f = np.array(ts, dtype=np.int64)
        for digits in (6, 4, 3):
            mesh = trimesh.Trimesh(v, f, process=True, validate=False)
            mesh.merge_vertices(digits_vertex=digits)
            mesh.remove_unreferenced_vertices()
            mesh.update_faces(mesh.nondegenerate_faces())
            if not ok(mesh):
                trimesh.repair.fill_holes(mesh)
                trimesh.repair.fix_winding(mesh)
            out = (np.asarray(mesh.vertices), np.asarray(mesh.faces))
            if first is None:
                first = out
            if ok(mesh):
                return out
    v, f = tessellate(solid, tol)
    mesh = trimesh.Trimesh(v, f, process=False)
    if ok(mesh):
        return v, f
    assert first is not None
    return first


def _band_time(hours: float) -> str:
    lo = max(0.25, math.floor(hours * 0.8 * 4) / 4)
    hi = max(lo + 0.25, math.ceil(hours * 1.3 * 4) / 4)
    return f"{lo:g}-{hi:g} h"


# ---------------------------------------------------------------------------
# One part
# ---------------------------------------------------------------------------


class PartMoulds:
    def __init__(
        self,
        src: MouldSource,
        model: CadModel,
        opts: dict[str, Any],
        out_dir: Path,
        report: Callable[[float, str], None],
        info: dict[str, Any],
    ):
        self.src = src
        self.model = model
        self.o = opts
        self.out = out_dir / src.key
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "tiles").mkdir(exist_ok=True)
        self.report = report
        self.info = info
        self.files: list[dict[str, Any]] = []
        self.timings: dict[str, float] = {}
        self.clip_solid: cq.Shape | None = None
        self.warnings: list[str] = []
        if src.key == "wing_root_fairing":
            ya, yb = src.extra["span_range"]
            self.clip_solid = P.box(-BIG, BIG, ya, yb, -BIG, BIG)

    # -- helpers -------------------------------------------------------------------------

    def _tick(self, name: str, t0: float) -> float:
        now = time.time()
        self.timings[name] = round(self.timings.get(name, 0.0) + now - t0, 2)
        return now

    def _file(self, path: Path, kind: str, **extra: Any) -> dict[str, Any]:
        row = {
            "path": str(path.relative_to(self.out.parent)),
            "kind": kind,
            "size_bytes": path.stat().st_size,
            "part": self.src.key,
            **extra,
        }
        self.files.append(row)
        return row

    def _net_solid(self) -> cq.Shape:
        src = self.src
        if src.key == "wing_root_fairing":
            return src.extra["surface"].net_solid()
        return src.outer(0.0, *src.net_range)

    def _caps(self) -> list[tuple[np.ndarray, float]]:
        src = self.src
        if src.key == "wing_root_fairing":
            y = src.extra["span_axis"]
            a, b = src.extra["net_span_range"]
            return [(y, a), (y, b)]
        caps = [(src.long_axis, src.net_range[1])]
        if src.key == "fuselage":
            caps.append((src.long_axis, src.net_range[0]))
        return caps

    def _describe(self, pts: np.ndarray) -> str:
        c = pts.mean(axis=0)
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        return (
            f"x {lo[0]:.0f}-{hi[0]:.0f}, y {lo[1]:.0f}-{hi[1]:.0f}, z {lo[2]:.0f}-{hi[2]:.0f} mm"
            f" (centre {c[0]:.0f}, {c[1]:.0f}, {c[2]:.0f})"
        )

    # -- main ------------------------------------------------------------------------------

    def run(self, frac0: float, frac1: float) -> dict[str, Any]:
        o, src = self.o, self.src
        t = time.time()
        span = frac1 - frac0

        def rep(f: float, msg: str) -> None:
            self.report(frac0 + span * f, f"{src.label}: {msg}")

        rep(0.0, "outer surface and draft")
        net = self._net_solid()
        caps = self._caps()
        samples, _ = MA.drop_caps(MA.face_samples(net, int(o["draft_samples_per_face"])), caps)
        net_tris = MA.mesh_triangles(net, 0.08, 0.15)
        t = self._tick("surface_s", t)

        # Pull direction and parting plane.
        choices = []
        for d in src.pull_candidates:
            pp = MA.parting_plane(samples, d, caps=caps)
            depth = max(pp["depth_positive_mm"], pp["depth_negative_mm"])
            band = MA.parting_band(depth, float(o["min_draft_deg"]))
            names = _half_names(d)
            dr = MA.draft_report(
                samples, d, pp["offset_mm"], o["min_draft_deg"], band, names, self._describe
            )
            dm = MA.demould_check(
                net_tris, d, pp["offset_mm"], band, int(o["demould_samples"]), caps=caps
            )
            s = dr["summary"]
            score = (
                s["fraction_below_min_outside_band"]
                + 10 * (dm["A"]["undercut_fraction"] + dm["B"]["undercut_fraction"])
                + 1e-4 * depth
            )
            choices.append(
                {"pull": d, "pp": pp, "band": band, "draft": dr, "demould": dm, "score": score}
            )
        best = min(choices, key=lambda c: c["score"])
        d = best["pull"]
        h0 = best["pp"]["offset_mm"]
        names = _half_names(d)
        frame = Frame(src.long_axis, d, h0)
        self.frame = frame
        t = self._tick("draft_s", t)

        # Solids.
        rep(0.12, "mould wall and flange")
        w = float(o["wall_mm"])
        mlo, mhi = src.mould_range
        s_ext = src.outer(0.0, mlo - 2.0, mhi + 2.0)
        outer = src.outer(w, mlo, mhi)
        plane_pt = frame.world(0.0, 0.0, 0.0)
        o_face = plane_section(outer, plane_pt, d)
        s_face = plane_section(net, plane_pt, d)
        if o_face is None or s_face is None:
            raise CadError(f"{src.label}: the parting plane misses the part.")
        o_wire = o_face.outerWire()
        s_wire = s_face.outerWire()
        parting_pts = wire_points(s_wire, 4.0)
        f_w = float(o["flange_width_mm"])
        f_t = float(o["flange_thickness_mm"])
        flange_face = _nurbs(cq.Face.makeFromWires(o_wire.offset2D(f_w, "arc")[0]))
        fl_loc = frame.local(np.array([v.toTuple() for v in flange_face.Vertices()]))
        fb = flange_face.BoundingBox()
        corners = np.array(
            [
                [x, y, z]
                for x in (fb.xmin, fb.xmax)
                for y in (fb.ymin, fb.ymax)
                for z in (fb.zmin, fb.zmax)
            ]
        )
        cl = frame.local(corners)
        u_clip = (mlo, mhi)
        if src.key == "wing_root_fairing":
            ya, yb = src.extra["span_range"]
            vv = sorted(
                [
                    float(frame.local(np.array([[0.0, ya, 0.0]]))[0, 1]),
                    float(frame.local(np.array([[0.0, yb, 0.0]]))[0, 1]),
                ]
            )
            v_clip = (vv[0], vv[1])
        else:
            v_clip = (float(cl[:, 1].min()) - 1, float(cl[:, 1].max()) + 1)
        del fl_loc

        # Tile plan.
        depth_a = best["pp"]["depth_positive_mm"]
        depth_b = best["pp"]["depth_negative_mm"]
        rib_h = float(o["rib_height_mm"])
        cross_w = min(v_clip[1], float(cl[:, 1].max())) - max(v_clip[0], float(cl[:, 1].min()))
        cross_d = max(depth_a, depth_b) + w + rib_h + float(o["key_height_mm"]) + 2.0
        l_max = max_tile_length(
            (cross_w, cross_d),
            self.model.envelope,
            float(o["joint_key_height_mm"]),
            float(o["tile_margin_mm"]),
        )
        if l_max <= float(o["min_tile_mm"]):
            raise CadError(
                f"{src.label}: the mould section ({cross_w:.0f} x {cross_d:.0f} mm) does not fit "
                f"the {self.model.envelope[0]:g} mm printer envelope; tiling across the section "
                "is not supported yet. Use a larger printer or a smaller part."
            )
        cuts = plan_cuts(mlo, mhi, l_max, src.curvature, src.kinks, float(o["min_tile_mm"]))
        bounds = [mlo, *cuts, mhi]
        t = self._tick("plan_s", t)

        # Flange bolts and keys (shared by both halves).
        mid = o_wire.offset2D(f_w / 2, "arc")[0]
        mid_pts = wire_points(mid, 1.0)
        ml = frame.local(mid_pts)
        valid = (
            (ml[:, 0] > u_clip[0] + 12)
            & (ml[:, 0] < u_clip[1] - 12)
            & (ml[:, 1] > v_clip[0] + 12)
            & (ml[:, 1] < v_clip[1] - 12)
        )
        for c in cuts:
            valid &= np.abs(ml[:, 0] - c) > 14.0
        bolts: list[np.ndarray] = []
        key_cands: list[np.ndarray] = []
        pitch = float(o["bolt_pitch_mm"])
        for run in _runs(valid):
            pts = mid_pts[run]
            idx = _spaced(pts, pitch, 8.0)
            bolts += [pts[i] for i in idx]
            for a, b in itertools.pairwise(idx):
                key_cands.append(pts[(a + b) // 2])
        keys: list[np.ndarray] = []
        for ua, ub in itertools.pairwise(bounds):
            cand = [k for k in key_cands if ua + 12 < float(frame.local(k)[0, 0]) < ub - 12]
            if not cand:
                continue
            loc = frame.local(np.array(cand))
            vmid = 0.5 * (loc[:, 1].min() + loc[:, 1].max())
            chosen = []
            for side in (loc[:, 1] >= vmid, loc[:, 1] < vmid):
                if side.any():
                    ii = np.flatnonzero(side)
                    target = ua + (0.3 if not chosen else 0.7) * (ub - ua)
                    j = ii[int(np.argmin(np.abs(loc[ii, 0] - target)))]
                    chosen.append(j)
            if len(chosen) == 1 and len(cand) > 1:
                far = int(np.argmax(np.linalg.norm(loc[:, :2] - loc[chosen[0], :2], axis=1)))
                if far != chosen[0]:
                    chosen.append(far)
            keys += [cand[j] for j in chosen]

        # Joints: rib ring bolts/keys and flange lugs.
        joints = []
        mid_off = w + rib_h / 2 if cuts else 0.0
        o_mid = src.outer(mid_off, mlo, mhi) if cuts else None
        o_rib = src.outer(w + rib_h, mlo, mhi) if cuts else None
        for c in cuts:
            jp = frame.world(c, 0.0, 0.0)
            ring = plane_section(o_mid, jp, frame.e1) if o_mid is not None else None
            osec = plane_section(outer, jp, frame.e1)
            jdata: dict[str, Any] = {"u_mm": c, "halves": {}}
            ring_pts = frame.local(wire_points(ring.outerWire(), 1.0)) if ring else np.zeros((0, 3))
            o_pts = frame.local(wire_points(osec.outerWire(), 1.0)) if osec else np.zeros((0, 3))
            for sgn, name in ((1.0, names[0]), (-1.0, names[1])):
                hb, kb, lugs = [], [], []
                if len(ring_pts):
                    m = (
                        (ring_pts[:, 2] * sgn >= f_t + 5.0)
                        & (ring_pts[:, 1] > v_clip[0] + 8.0)
                        & (ring_pts[:, 1] < v_clip[1] - 8.0)
                    )
                    for run in _runs(m):
                        pts = ring_pts[run][:, 1:]
                        idx = _spaced(pts, 70.0, 6.0)
                        hb += [(float(pts[i][0]), float(pts[i][1])) for i in idx]
                        if len(idx) >= 2:
                            for a, b in ((idx[0], idx[1]), (idx[-2], idx[-1])):
                                q = pts[(a + b) // 2]
                                kb.append((float(q[0]), float(q[1])))
                        elif len(pts) > 20:
                            q = pts[len(pts) // 4]
                            kb.append((float(q[0]), float(q[1])))
                if len(o_pts):
                    near = o_pts[np.abs(o_pts[:, 2]) < 2.0]
                    if len(near):
                        for v_edge, sv in ((near[:, 1].max(), 1.0), (near[:, 1].min(), -1.0)):
                            v_out = v_edge + sv * f_w
                            if v_clip[0] + 1 < v_out < v_clip[1] - 1:
                                lugs.append((float(v_edge), float(v_out)))
                                hb.append((float(v_edge + sv * f_w / 2), sgn * (f_t + rib_h) / 2))
                jdata["halves"][name] = {
                    "bolts_vh": hb,
                    "keys_vh": list(dict.fromkeys(kb)),
                    "lugs_v": lugs,
                }
            joints.append(jdata)
        t = self._tick("features_s", t)

        # Trim grooves and vent channel (flange), ring grooves (cavity at open ends).
        tr = float(o["trim_offset_mm"])
        gw = float(o["trim_groove_width_mm"])
        gd = float(o["trim_groove_depth_mm"])
        trim_ring = _ring_face(s_wire, tr - gw / 2, tr + gw / 2)
        vent_ring = (
            _ring_face(
                s_wire,
                float(o["vent_offset_mm"]) - float(o["vent_width_mm"]) / 2,
                float(o["vent_offset_mm"]) + float(o["vent_width_mm"]) / 2,
            )
            if o["vent_channels"]
            else None
        )
        ring_grooves = []
        for n_vec, off, _desc in src.trim_planes:
            if src.key == "wing_root_fairing":
                g_solid = src.outer(gd, mlo, mhi)
            else:
                g_solid = src.outer(gd, off - 4.0, off + 4.0)
            ex, ey, ez = P.frame_from_axis(n_vec)
            slab = P.oriented_box(n_vec * off, ex, ey, ez, (2 * BIG, 2 * BIG, gw))
            ring_grooves.append(g_solid.intersect(slab))

        rep(0.2, "tiles")
        halves_out = []
        n_tiles = len(bounds) - 1
        filament = o["filament"] if o["filament"] in FILAMENTS else "PETG"
        fil = FILAMENTS[filament]
        all_mould_tris: dict[str, list[np.ndarray]] = {names[0]: [], names[1]: []}
        for hi_idx, (sgn, hname) in enumerate(((1.0, names[0]), (-1.0, names[1]))):
            hletter = hname[0].upper()
            flange_slab = cq.Solid.extrudeLinear(flange_face, P.V(d * sgn * f_t))
            tiles = []
            step_pieces = []
            for i in range(n_tiles):
                ua, ub = bounds[i], bounds[i + 1]
                rep(
                    0.2 + 0.7 * (hi_idx * n_tiles + i) / (2 * n_tiles),
                    f"{hname} half, tile {i + 1}/{n_tiles}",
                )
                t = time.time()
                tile = self._build_tile(
                    frame,
                    sgn,
                    ua,
                    ub,
                    i,
                    n_tiles,
                    outer,
                    o_rib,
                    s_ext,
                    flange_slab,
                    v_clip,
                    bolts,
                    keys,
                    joints,
                    hname,
                    trim_ring,
                    vent_ring,
                    ring_grooves,
                )
                t = self._tick("tiles_build_s", t)
                label = f"{src.short}-{hletter} {i + 1}/{n_tiles}"
                safe = f"{src.short}-{hletter}-{i + 1:02d}of{n_tiles:02d}"
                tdata = self._export_tile(
                    tile, s_ext, frame, sgn, ua, ub, i, n_tiles, label, safe, filament, fil
                )
                all_mould_tris[hname].append(tdata.pop("_tris"))
                tdata["half"] = hname
                tiles.append(tdata)
                step_pieces.append((label, tile))
                self._tick("tiles_export_s", t)
            t = time.time()
            step_path = self.out / f"{src.key}_mould_{hname}.step"
            write_part_step(step_path, f"{src.key} mould {hname}", step_pieces, fil["color"])
            step_file = self._file(step_path, "step", half=hname)
            del step_pieces
            self._tick("step_s", t)
            halves_out.append(
                {
                    "key": hname,
                    "label": f"{src.label}: {hname} half",
                    "letter": hletter,
                    "pull_direction": [round(float(x), 5) for x in d * sgn],
                    "pull_note": f"Lift this half off along {self._dir_words(d * sgn)}.",
                    "tiles": tiles,
                    "step_file": step_file["path"],
                }
            )
            del flange_slab

        # Wall thickness (sampled) and demould per half.
        t = time.time()
        for half, sgn in zip(halves_out, (1.0, -1.0), strict=True):
            mt = np.vstack(all_mould_tris[half["key"]])
            half["wall_thickness_mm"] = MA.wall_thickness(
                net_tris, mt, d, h0, sgn, best["band"], caps=caps
            )
            dm = best["demould"]["A" if sgn > 0 else "B"]
            tol = 0.001
            rows = [
                r for f in best["draft"]["faces"] for r in f["halves"] if r["half"] == half["key"]
            ]
            a_half = sum(r["area_mm2"] for r in rows) or 1.0
            exact_under = sum(r["undercut_area_mm2"] for r in rows) / a_half
            half["demould"] = {
                **dm,
                "surface_undercut_fraction": round(exact_under, 5),
                "tolerance_fraction": tol,
                "demouldable": bool(dm["undercut_fraction"] <= tol and exact_under <= tol),
            }
            half["draft_min_outside_band_deg"] = best["draft"]["summary"][
                "min_draft_outside_band_deg"
            ][half["key"]]
        del all_mould_tris
        self._tick("checks_s", t)

        manifest = self._manifest(
            best, choices, frame, parting_pts, halves_out, bounds, bolts, keys, joints, v_clip
        )
        t = time.time()
        from app.cad.moulds_sheet import write_mould_sheet

        pdf = self.out / f"{src.key}_mould_sheet.pdf"
        write_mould_sheet(pdf, manifest, best["draft"]["_colours"], frame, self.info)
        manifest["files"]["pdf"] = self._file(pdf, "pdf")["path"]
        self._tick("pdf_s", t)
        manifest["notes"] += self.warnings
        manifest["timings"] = self.timings
        manifest["peak_rss_mb"] = round(_peak_rss_mb(), 1)
        manifest["files"]["all"] = self.files
        rep(1.0, "done")
        return manifest

    @staticmethod
    def _dir_words(v: np.ndarray) -> str:
        names = ("+x (aft)", "+y (right)", "+z (up)")
        neg = ("-x (forward)", "-y (left)", "-z (down)")
        k = int(np.argmax(np.abs(v)))
        main = names[k] if v[k] > 0 else neg[k]
        return f"{main} [{v[0]:.3f}, {v[1]:.3f}, {v[2]:.3f}]"

    # -- tile ------------------------------------------------------------------------------

    def _robust(self, body: cq.Shape, tools: list[cq.Shape], op: str) -> cq.Shape:
        """Boolean with all tools at once; if OpenCascade fails, one tool at a time, and a
        tool that still fails is skipped and reported in the manifest notes."""

        def run(b: cq.Shape, ts: list[cq.Shape]) -> cq.Shape | None:
            try:
                r = b.fuse(*ts) if op == "fuse" else b.cut(*ts)
                return r if r.Solids() else None
            except Exception:
                return None

        res = run(body, tools)
        if res is not None:
            return res
        for t in tools:
            r = run(body, [t])
            if r is None:
                bb = t.BoundingBox()
                self.warnings.append(
                    f"A {op} of a small feature near ({bb.center.x:.0f}, {bb.center.y:.0f}, "
                    f"{bb.center.z:.0f}) mm failed in OpenCascade and was left out; add it by "
                    "hand (drill or glue)."
                )
            else:
                body = r
        return body

    def _build_tile(
        self,
        fr: Frame,
        sgn: float,
        ua: float,
        ub: float,
        i: int,
        n: int,
        outer: cq.Shape,
        o_rib: cq.Shape | None,
        s_ext: cq.Shape,
        flange_slab: cq.Shape,
        v_clip: tuple[float, float],
        bolts: list[np.ndarray],
        keys: list[np.ndarray],
        joints: list[dict[str, Any]],
        hname: str,
        trim_ring: cq.Face | None,
        vent_ring: cq.Face | None,
        ring_grooves: list[cq.Shape],
    ) -> cq.Solid:
        o = self.o
        f_t = float(o["flange_thickness_mm"])
        rib_t = float(o["rib_thickness_mm"])
        rib_h = float(o["rib_height_mm"])
        f_w = float(o["flange_width_mm"])
        side = fr.box(ua, ub, v_clip[0], v_clip[1], 0.0, sgn * BIG)
        adds: list[cq.Shape] = []
        cuts: list[cq.Shape] = []
        _t = time.time()
        shell = outer.intersect(side)
        _t = self._tick("t_intersect", _t)
        adds.append(flange_slab.intersect(fr.box(ua, ub, v_clip[0], v_clip[1], 0.0, sgn * f_t)))
        jk_b, jk_t, jk_h = (
            float(o["joint_key_base_mm"]),
            float(o["joint_key_tip_mm"]),
            float(o["joint_key_height_mm"]),
        )
        cl = float(o["key_clearance_mm"])
        for j in joints:
            uj = j["u_mm"]
            if not (abs(uj - ua) < 1e-6 or abs(uj - ub) < 1e-6):
                continue
            at_start = abs(uj - ua) < 1e-6
            r0, r1 = (ua, ua + rib_t) if at_start else (ub - rib_t, ub)
            if o_rib is not None:
                adds.append(o_rib.intersect(fr.box(r0, r1, v_clip[0], v_clip[1], 0.0, sgn * BIG)))
            hd = j["halves"][hname]
            for v_edge, v_out in hd["lugs_v"]:
                # overlap the wall by 3 mm and stop 2 mm under the rib top so no face or edge of
                # the lug merely touches the rib (that meshes as a non-manifold edge)
                v_in = v_edge - 3.0 * np.sign(v_out - v_edge)
                adds.append(fr.box(r0, r1, v_in, v_out, 0.0, sgn * (f_t + rib_h - 2.0)))
            for v, h in hd["bolts_vh"]:
                cuts.append(fr.cylinder((r0 - 1, v, h), (r1 + 1, v, h), float(o["bolt_hole_mm"])))
            for v, h in hd["keys_vh"]:
                if at_start:  # socket
                    cuts.append(
                        fr.cone(
                            (ua - 0.01, v, h),
                            (ua + jk_h + 0.5, v, h),
                            jk_b + 2 * cl,
                            jk_t + 2 * cl - 0.3,
                        )
                    )
                else:  # key cone standing on the joint face
                    adds.append(fr.cone((ub - 0.5, v, h), (ub + jk_h, v, h), jk_b, jk_t))
        for b in bolts:
            u, v, _ = fr.local(b)[0]
            if ua < u < ub:
                cuts.append(
                    fr.cylinder(
                        (u, v, -sgn * 1.0), (u, v, sgn * (f_t + 1)), float(o["bolt_hole_mm"])
                    )
                )
        kb, kt, kh = float(o["key_base_mm"]), float(o["key_tip_mm"]), float(o["key_height_mm"])
        for k in keys:
            u, v, _ = fr.local(k)[0]
            if not ua < u < ub:
                continue
            if sgn > 0:
                adds.append(fr.cone((u, v, 0.5), (u, v, -kh), kb, kt))
            else:
                cuts.append(
                    fr.cone((u, v, 0.01), (u, v, -(kh + 0.5)), kb + 2 * cl, kt + 2 * cl - 0.2)
                )
        _t = time.time()
        body = self._robust(shell, adds, "fuse") if adds else shell
        if self.clip_solid is not None:
            body = body.intersect(self.clip_solid)
        _t = self._tick("t_fuse", _t)
        tile_box = fr.box(ua - 1, ub + 1, v_clip[0] - 1, v_clip[1] + 1, -BIG, BIG)
        groove_cuts: list[cq.Shape] = []
        gd = float(o["trim_groove_depth_mm"])
        for ring in (trim_ring, vent_ring):
            if ring is None:
                continue
            depth = gd if ring is trim_ring else float(o["vent_depth_mm"])
            g = cq.Solid.extrudeLinear(ring, P.V(fr.d * sgn * depth))
            if ring is trim_ring:
                g = g.translate(P.V(fr.d * (-sgn * 0.01)))
            cuts.append(g.intersect(tile_box))
        for g in ring_grooves:
            gb = g.intersect(tile_box)
            if gb.Volume() > 1e-6:
                cuts.append(gb)
        _t = time.time()
        body = body.cut(s_ext)
        _t = self._tick("t_cut_cavity", _t)
        if cuts:
            body = self._robust(body, cuts, "cut")
        _t = self._tick("t_cut_features", _t)
        if groove_cuts:
            body = self._robust(body, groove_cuts, "cut")
        _t = self._tick("t_cut_grooves", _t)
        with contextlib.suppress(Exception):  # unifying coplanar faces is cosmetic
            body = body.clean()
        _t = self._tick("t_clean", _t)
        del f_w
        solids = sorted(body.Solids(), key=lambda x: -x.Volume())
        if not solids:
            raise CadError(f"{self.src.label}: mould tile {i + 1} came out empty.")
        main = solids[0]
        rest = sum(x.Volume() for x in solids[1:])
        if rest > 0.02 * main.Volume():
            raise CadError(
                f"{self.src.label}: mould tile {i + 1} split into {len(solids)} bodies "
                f"({rest:.0f} mm³ outside the main body)."
            )
        if rest > 1.0:
            self.warnings.append(
                f"Tile {i + 1} ({hname} half): {len(solids) - 1} sliver(s) of {rest:.0f} mm³ "
                "beyond the trim line were dropped."
            )
        if not main.isValid():
            fixed = main.fix()
            if fixed.isValid():
                main = fixed
        return main

    # -- export ----------------------------------------------------------------------------

    def _export_tile(
        self,
        tile: cq.Solid,
        s_ext: cq.Shape,
        fr: Frame,
        sgn: float,
        ua: float,
        ub: float,
        i: int,
        n: int,
        label: str,
        safe: str,
        filament: str,
        fil: dict[str, Any],
    ) -> dict[str, Any]:
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.gp import gp_Pnt
        from OCP.TopAbs import TopAbs_ON

        tol = float(self.o["mesh_tolerance_mm"])
        verts, faces = tessellate_abs(tile, tol)
        import trimesh

        _m = trimesh.Trimesh(verts, faces, process=False)
        watertight = bool(_m.is_watertight and _m.is_winding_consistent)
        del _m
        tris_by_face = []
        clsf = BRepClass3d_SolidClassifier(s_ext.wrapped)
        a_mould = 0.0
        a_total = 0.0
        for f in tile.Faces():
            fv, ft = f.tessellate(tol, 0.1)
            if not ft:
                continue
            arr = np.array([(p.x, p.y, p.z) for p in fv])[np.asarray(ft, int)]
            c = arr[0].mean(axis=0)
            clsf.Perform(gp_Pnt(*c), 0.02)
            is_mould = clsf.State() == TopAbs_ON
            area = float(f.Area())
            a_total += area
            if is_mould:
                a_mould += area
            tris_by_face.append((arr, bool(is_mould)))
        cands = [
            (
                "joint face down (rear/outboard end)" if i < n - 1 else "end face down",
                fr.e1,
                "Standing on its end face: the mould face is built from perimeters and the "
                "section widens upward, so the mould face needs no support.",
            ),
            (
                "joint face down (front end)" if i > 0 else "front end down",
                -fr.e1,
                "Standing on its front end face: perimeters form the mould face; no supports "
                "on it.",
            ),
            (
                "parting face down",
                -fr.d * sgn,
                "Flange (parting face) flat on the bed: largest flat base; the cavity is a "
                "ceiling and its crown overhangs.",
            ),
            (
                "back down (cavity up)",
                fr.d * sgn,
                "Cavity facing up: the mould face is never an overhang; supports go under the "
                "curved back only.",
            ),
        ]
        ori = choose_orientation(verts, tris_by_face, cands, fr.e2, self.model.envelope)
        best = ori["best"]
        r = best["rotation"]
        q = verts @ r.T
        lo, hi = q.min(axis=0), q.max(axis=0)
        bed = self.model.bed
        shift = np.array(
            [bed[0] / 2 - 0.5 * (lo[0] + hi[0]), bed[1] / 2 - 0.5 * (lo[1] + hi[1]), -lo[2]]
        )
        q = q + shift
        ext = q.max(axis=0) - q.min(axis=0)
        env = np.asarray(self.model.envelope)
        fits = bool(np.all(ext <= env + 1e-6))
        stl = self.out / "tiles" / f"{safe}.stl"
        write_binary_stl(stl, q, faces, f"{self.src.key} mould {label}")
        f_stl = self._file(stl, "stl", tile=label)
        tmf = self.out / "tiles" / f"{safe}.3mf"
        write_3mf(
            tmf,
            [MeshObject(label, fil["color"], filament, q, faces)],
            [(0, np.eye(4))],
            f"{self.src.label} mould {label}",
            {"Designer": "VTOL designer", "Description": f"Mould tile {label}"},
        )
        f_3mf = self._file(tmf, "3mf", tile=label)
        vol = float(tile.Volume())
        shell = min(vol, a_mould * 3.5 + (a_total - a_mould) * 1.6)
        extruded = shell + max(0.0, vol - shell) * 0.25
        mass = fil["density"] * extruded / 1000.0
        hours = extruded / fil["rate_mm3_s"] / 3600.0 * 1.25 + 0.2
        settings = PRINT_SETTINGS.get(filament, PRINT_SETTINGS["PETG"])
        supports = (
            "none on the mould face"
            if best["mould_face_overhang_mm2"] < 1
            else f"{best['mould_face_overhang_mm2']:.0f} mm² of mould face overhangs: use "
            "tree supports with a support-interface gap and sand those spots"
        )
        if best["other_overhang_mm2"] > 1:
            supports += (
                f"; {best['other_overhang_mm2']:.0f} mm² elsewhere (back, ribs, keys): "
                "supports from the build plate only"
            )
        rot4 = np.eye(4)
        rot4[:3, :3] = r
        rot4[:3, 3] = shift
        return {
            "label": label,
            "index": i + 1,
            "count": n,
            "u_range_mm": [round(ua, 1), round(ub, 1)],
            "size_mm": [round(float(e), 1) for e in ext],
            "fits": fits,
            "watertight": watertight,
            "envelope_mm": list(self.model.envelope),
            "volume_cm3": round(vol / 1000, 1),
            "mould_face_area_cm2": round(a_mould / 100, 1),
            "estimated_mass_g": round(mass, 0),
            "estimated_print_time": _band_time(hours),
            "filament": filament,
            "print_orientation": {
                "name": best["name"],
                "reason": best["reason"],
                "mould_face_overhang_mm2": best["mould_face_overhang_mm2"],
                "other_overhang_mm2": best["other_overhang_mm2"],
                "bed_contact_mm2": best["bed_contact_mm2"],
                "supports": supports,
                "transform": [[round(float(x), 6) for x in row] for row in rot4],
                "candidates": [
                    {k: v for k, v in c.items() if k not in ("rotation", "score")}
                    for c in ori["candidates"]
                ],
            },
            "print_settings": settings,
            "notes": [
                f"{filament}: {settings['walls']}, {settings['infill']}, layers "
                f"{settings['layer_mm']} mm.",
                settings["temperature_note"],
                "Write the label on the back with a marker after printing.",
            ],
            "files": {"stl": f_stl["path"], "3mf": f_3mf["path"]},
            "_tris": verts[faces],
        }

    # -- manifest ------------------------------------------------------------------------

    def _manifest(
        self,
        best: dict[str, Any],
        choices: list[dict[str, Any]],
        fr: Frame,
        parting_pts: np.ndarray,
        halves: list[dict[str, Any]],
        bounds: list[float],
        bolts: list[np.ndarray],
        keys: list[np.ndarray],
        joints: list[dict[str, Any]],
        v_clip: tuple[float, float],
    ) -> dict[str, Any]:
        o, src = self.o, self.src
        dr = best["draft"]
        step = max(1, len(parting_pts) // 400)
        poly = [[round(float(c), 2) for c in p] for p in parting_pts[::step]]
        cols = dr["_colours"]
        if len(cols) > 1500:
            cols = cols[:: math.ceil(len(cols) / 1500)]
        loc = fr.local(cols[:, :3]) if len(cols) else np.zeros((0, 3))
        names = _half_names(best["pull"])
        lam = _laminate(src.key)
        flagged_faces = [f for f in dr["faces"] if f["flagged"]]
        n_tiles = len(bounds) - 1
        assembly = [
            f"Print all {2 * n_tiles} tiles ({n_tiles} per half) in {o['filament']}; check each "
            "against its label and the tile layout on this sheet.",
        ]
        if n_tiles > 1:
            assembly += [
                f"Per half, join the tiles in order 1 to {n_tiles}: lay them parting face down on "
                "a flat reference table, engage the joint keys, bolt the ribs with "
                f"{o['bolt']} x 25 bolts, nuts and washers, and bond the joint faces with "
                "thickened epoxy (wipe the squeeze-out off the mould face).",
                "Fill and fair the tile seams on the mould face with epoxy filler before "
                "sanding the whole face.",
            ]
        assembly += [
            "Check each half's flange is flat (straight edge across the joints); sand high spots.",
            "Finish the mould faces (sanding, sealing, primer) and apply the release system: "
            + " ".join(FINISHING_NOTES[:3]),
            f"Close the mould: engage the registration cones in their sockets and bolt the "
            f"flanges with {o['bolt']} x 30 bolts at every hole ({len(bolts)} per mould) for "
            "trial fits; lay up each half open, then join.",
            "Laminate to the scribed trim line (5 mm outside the net edge), cure, demould "
            "along each half's pull direction and trim back to the net edge.",
        ]
        return {
            "key": src.key,
            "label": src.label,
            "description": src.description,
            "net_part": src.net_description,
            "open_ends": src.open_ends,
            "notes": list(src.notes),
            "frame": {
                "long_axis": [round(float(x), 5) for x in fr.e1],
                "e2": [round(float(x), 5) for x in fr.e2],
                "pull": [round(float(x), 5) for x in fr.d],
                "plane_offset_mm": round(fr.h0, 3),
            },
            "pull_direction": [round(float(x), 5) for x in fr.d],
            "pull_choice": {
                "candidates": [
                    {
                        "pull": [round(float(x), 4) for x in c["pull"]],
                        "halves": list(_half_names(c["pull"])),
                        "fraction_below_min_outside_band": c["draft"]["summary"][
                            "fraction_below_min_outside_band"
                        ],
                        "undercut_fraction": round(
                            c["demould"]["A"]["undercut_fraction"]
                            + c["demould"]["B"]["undercut_fraction"],
                            5,
                        ),
                        "depth_mm": round(
                            max(c["pp"]["depth_positive_mm"], c["pp"]["depth_negative_mm"]), 1
                        ),
                        "score": round(c["score"], 5),
                    }
                    for c in choices
                ],
                "reason": (
                    "Least area below the minimum draft outside the parting band, then the "
                    "fewest undercuts, then the shallower halves."
                    if len(choices) > 1
                    else "Only candidate for this part (normal to the wing chord plane)."
                ),
            },
            "halves": halves,
            "parting_line": {
                "description": (
                    f"Plane through the maximum silhouette seen along the pull direction "
                    f"{self._dir_words(fr.d)}: {names[0]} and {names[1]} halves. The "
                    f"silhouette lies within {best['pp']['deviation_mm']:.1f} mm of the plane"
                    + (
                        " (it spans the flat sides that run parallel to the pull)."
                        if best["pp"]["deviation_mm"] > 2.0
                        else "."
                    )
                ),
                "plane": {
                    "point_mm": [round(float(x), 3) for x in fr.world(0, 0, 0)],
                    "normal": [round(float(x), 5) for x in fr.d],
                },
                "silhouette_deviation_mm": round(best["pp"]["deviation_mm"], 2),
                "polyline_mm": poly,
                "length_mm": round(
                    float(np.sum(np.linalg.norm(np.diff(parting_pts, axis=0), axis=1))), 0
                ),
            },
            "draft": {
                "min_draft_deg": o["min_draft_deg"],
                "parting_band_mm": round(best["band"], 2),
                "summary": dr["summary"],
                "faces": dr["faces"],
                "flagged_faces": [f["face"] for f in flagged_faces],
                "flagged_detail": [
                    {
                        "face": f["face"],
                        "where": f["where"],
                        "min_draft_deg": f["min_draft_deg"],
                        "area_below_min_mm2": sum(
                            r["area_below_min_outside_band_mm2"] for r in f["halves"]
                        ),
                    }
                    for f in flagged_faces
                ],
                "note": "Draft is measured against each half's own pull direction; the band "
                "next to the parting line is always near 0° on a smooth part and is reported "
                "but not flagged. Flagged faces are left as designed (the part surface is not "
                "altered): add release film there, flex the laminate out, or change the "
                "design's cross-section if they stick.",
                "map": {
                    "points_uv_mm": [
                        [round(float(a), 1), round(float(b), 1)] for a, b in loc[:, :2]
                    ],
                    "h_mm": [round(float(x), 1) for x in loc[:, 2]],
                    "draft_deg": [round(float(x), 2) for x in cols[:, 3]] if len(cols) else [],
                    "half": [names[0] if x > 0 else names[1] for x in cols[:, 4]]
                    if len(cols)
                    else [],
                    "in_band": [bool(x) for x in cols[:, 5]] if len(cols) else [],
                },
            },
            "demould": {
                "method": "Ray casting: area-weighted samples of the part surface outside the "
                "parting band; a ray from each sample along its half's pull direction must not "
                "hit the part again and the local draft must not be below "
                f"-{MA.UNDERCUT_TOLERANCE_DEG}° (tolerance: undercut area at most 0.1 % of the "
                "half).",
                "halves": {h["key"]: h["demould"] for h in halves},
            },
            "mould": {
                "wall_mm": o["wall_mm"],
                "wall_note": "Mould face to back, measured along the surface normal (sampled per "
                "half below).",
                "flange_width_mm": o["flange_width_mm"],
                "flange_thickness_mm": o["flange_thickness_mm"],
                "bolt": o["bolt"],
                "bolt_hole_mm": o["bolt_hole_mm"],
                "bolt_pitch_mm": o["bolt_pitch_mm"],
                "flange_bolts_mm": [[round(float(c), 1) for c in b] for b in bolts],
                "registration_keys": {
                    "type": f"tapered cones Ø{o['key_base_mm']:g} -> Ø{o['key_tip_mm']:g} mm, "
                    f"{o['key_height_mm']:g} mm high on the {names[0]} half; sockets "
                    f"{o['key_clearance_mm']:g} mm larger in the {names[1]} half",
                    "positions_mm": [[round(float(c), 1) for c in k] for k in keys],
                },
                "trim_line": {
                    "offset_mm": o["trim_offset_mm"],
                    "groove_mm": [o["trim_groove_width_mm"], o["trim_groove_depth_mm"]],
                    "where": ["flange face, 5 mm outside the net edge (parting line)"]
                    + [desc for _, _, desc in src.trim_planes],
                },
                "vent_channels": bool(o["vent_channels"]),
                "laminate_allowance": lam,
            },
            "tiling": {
                "cuts_mm": [round(c, 1) for c in bounds[1:-1]],
                "tiles_per_half": n_tiles,
                "joint_rib": {
                    "height_mm": o["rib_height_mm"],
                    "thickness_mm": o["rib_thickness_mm"],
                    "bolts": f"{o['bolt']} x 25 through both ribs",
                    "keys": f"tapered cones Ø{o['joint_key_base_mm']:g} -> "
                    f"Ø{o['joint_key_tip_mm']:g} mm, {o['joint_key_height_mm']:g} mm long",
                },
                "joints": [
                    {
                        "u_mm": round(j["u_mm"], 1),
                        "curvature_per_mm": round(src.curvature(j["u_mm"]), 6),
                        "halves": {
                            k: {
                                "bolts": len(v["bolts_vh"]),
                                "keys": len(v["keys_vh"]),
                                "lugs": len(v["lugs_v"]),
                            }
                            for k, v in j["halves"].items()
                        },
                    }
                    for j in joints
                ],
                "rule": "Fewest tiles that fit, near equal lengths; each cut at the lowest "
                "surface curvature within +-20 % of the equal-length position and at least "
                "15 mm from a profile kink.",
            },
            "print_notes": {
                "filament": o["filament"],
                "settings": PRINT_SETTINGS.get(o["filament"], PRINT_SETTINGS["PETG"]),
                "finishing": FINISHING_NOTES,
            },
            "assembly_order": assembly,
            "files": {},
            "_v_clip": v_clip,
        }


def _laminate(key: str) -> dict[str, Any]:
    from app.engine.fullscale import CORES, FABRICS, FIBRE_DENSITY, FIBRE_VOLUME_FRACTION

    layups = {
        "nose": ([("cf160", 2)], None),
        "fuselage": ([("cf200", 1), ("cf160", 2)], ("rohacell31", 2.0)),
        "wing_root_fairing": ([("cf93", 1), ("cf160", 1)], None),
    }
    plies, core = layups.get(key, ([("cf160", 2)], None))
    t = sum(FABRICS[f]["g_m2"] / (FIBRE_DENSITY * FIBRE_VOLUME_FRACTION) * n for f, n in plies)
    desc = " + ".join(f"{n} x {FABRICS[f]['label']}" for f, n in plies)
    out = {
        "direction": "inward from the mould face (the mould is the outer mould line)",
        "laminate_mm": round(t, 2),
        "layup": desc,
        "source": "Layup suggestion of the full-scale checks (app.engine.fullscale).",
    }
    if core:
        out["core"] = f"{core[1]:g} mm {CORES[core[0]]['label']} in the large panels"
        out["sandwich_mm"] = round(t + core[1], 2)
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def mould_sources(model: CadModel, opts: dict[str, Any]) -> dict[str, MouldSource]:
    ext, wall = float(opts["extension_mm"]), float(opts["wall_mm"])
    flange = float(opts["flange_width_mm"])
    out = fuselage_family_sources(model, ext, wall, flange)
    out["wing_root_fairing"] = fairing_source(model, ext, wall, flange)
    return out


def generate_moulds(
    parameters: dict[str, Any],
    mission: dict[str, Any],
    settings: dict[str, Any] | None,
    *,
    parts: Sequence[str] = PART_KEYS,
    out_dir: Path | str,
    progress: ProgressFn | None = None,
    options: dict[str, Any] | None = None,
    project: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Generate the moulds of ``parts`` into ``out_dir`` and return the manifest."""
    t0 = time.time()
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    unknown = [p for p in parts if p not in PART_KEYS]
    if unknown:
        raise CadError(f"Unknown mould part(s): {', '.join(unknown)} (known: {PART_KEYS}).")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    def report(frac: float, msg: str) -> None:
        if progress:
            progress(min(1.0, max(0.0, frac)), msg)

    report(0.0, "Building the model")
    model = build_model(parameters, mission, settings)
    sources = mould_sources(model, opts)
    info = {"project": "VTOL drone", "version": "draft", **(project or {})}
    parts_out = []
    files = []
    n = len(parts)
    for k, key in enumerate(parts):
        pm = PartMoulds(sources[key], model, opts, out, report, info)
        m = pm.run(0.02 + 0.96 * k / n, 0.02 + 0.96 * (k + 1) / n)
        m.pop("_v_clip", None)
        parts_out.append(m)
        files += pm.files
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "parts": parts_out,
        "summary": [
            {
                "part": p["key"],
                "label": p["label"],
                "halves": [h["key"] for h in p["halves"]],
                "tiles_per_half": p["tiling"]["tiles_per_half"],
                "tiles": sum(len(h["tiles"]) for h in p["halves"]),
                "all_tiles_fit": all(t["fits"] for h in p["halves"] for t in h["tiles"]),
                "demouldable": all(h["demould"]["demouldable"] for h in p["halves"]),
                "flagged_faces": len(p["draft"]["flagged_faces"]),
                "min_draft_outside_band_deg": p["draft"]["summary"]["min_draft_outside_band_deg"],
            }
            for p in parts_out
        ],
        "options": opts,
        "envelope_mm": list(model.envelope),
        "printer": model.settings["printer"]["name"],
        "files": files,
        "total_bytes": sum(f["size_bytes"] for f in files),
        "duration_s": round(time.time() - t0, 1),
        "peak_rss_mb": round(_peak_rss_mb(), 1),
        "notes": [
            "Moulds take the outer mould line (OML); the laminate grows inward.",
            "The left wing-root fairing is the mirror image of the right one: mirror its tile "
            "files in the slicer (keys and sockets stay compatible).",
        ],
    }
    path = out / "moulds_manifest.json"
    path.write_text(json.dumps(manifest, indent=1, default=_json_default), encoding="utf-8")
    report(1.0, "Done")
    return manifest


def _json_default(v: Any) -> Any:
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    if isinstance(v, tuple):
        return list(v)
    raise TypeError(type(v))
