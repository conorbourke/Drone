"""Splitting printed parts into pieces that fit the printer, and choosing print orientations.

Rules (docs/phases/PHASE5.md section 2):

* A part is split only when it does not fit the usable envelope in any orientation (rotation
  about all axes is allowed; the check is an oriented bounding box).
* Lifting surfaces are split spanwise at section planes. A joint is never placed within 15 % of
  the panel length from the structural root (highest bending moment), never in a keep-out zone
  (boom-to-wing clamp, aileron hinge ends, fin attachment, the end of a spar tube: so a printed
  joint never coincides with a tube end and one continuous member always crosses it), and the
  stations are balanced so pieces are of similar length.
* Fuselage shells are split transversely, away from the wing clamp and spar holes, the nose-bay
  flange, the tail-boom socket and the pusher firewall.
* Orientation: lifting surfaces leading edge down (layer lines run spanwise, along the bending
  stress; the leading edge is self-supporting), tilted about the span axis only when the chord
  is taller than the envelope; fuselage shells stand on their larger cut face (hoop perimeters,
  no internal supports); solid mounts lie on their largest flat face.
* ``check_fit`` verifies every exported mesh in its print orientation and raises
  :class:`EnvelopeError` otherwise.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from functools import cache
from typing import Any

import cadquery as cq
import numpy as np

from app.cad import joints as jn
from app.cad import parts as P
from app.cad.model import CadError, CadModel, EnvelopeError, Surface

MIN_PIECE_MM = 40.0
FIT_TOLERANCE_MM = 1e-6
MESH_TOLERANCE_MM = 0.05
MESH_ANGULAR_RAD = 0.1


# ---------------------------------------------------------------------------
# Rotations
# ---------------------------------------------------------------------------


def rot_x(t: float) -> np.ndarray:
    c, s = math.cos(t), math.sin(t)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], float)


def rot_y(t: float) -> np.ndarray:
    c, s = math.cos(t), math.sin(t)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], float)


def rot_z(t: float) -> np.ndarray:
    c, s = math.cos(t), math.sin(t)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], float)


@cache
def _candidates(family: str) -> tuple[np.ndarray, np.ndarray]:
    """Rotation matrices (K, 3, 3) applied after the base frame, ordered by preference, and the
    (tilt, lean, turn) angles in degrees."""
    if family == "turn":
        tilts, leans = [0], [0]
    elif family == "tilt0":
        tilts = [0, *itertools.chain.from_iterable((t, -t) for t in range(5, 65, 5))]
        leans = [0]
    elif family == "tilt":
        tilts = [0, *itertools.chain.from_iterable((t, -t) for t in range(5, 65, 5))]
        leans = [0, 10, -10, 20, -20, 30, -30]
    else:
        tilts = [0, *itertools.chain.from_iterable((t, -t) for t in range(5, 95, 5))]
        leans = [0, *itertools.chain.from_iterable((t, -t) for t in range(5, 95, 5))]
    turns = [0, *itertools.chain.from_iterable((t, -t) for t in range(5, 50, 5)), 90]
    combos = sorted(
        itertools.product(tilts, leans, turns),
        key=lambda c: (abs(c[0]) + 2 * abs(c[1]) + 0.5 * abs(c[2] if c[2] != 90 else 1), c),
    )
    mats = np.array(
        [
            rot_z(math.radians(c)) @ rot_y(math.radians(b)) @ rot_x(math.radians(a))
            for a, b, c in combos
        ]
    )
    return mats, np.array(combos, float)


def base_frame(rows: list[np.ndarray]) -> np.ndarray:
    e1 = rows[0] / np.linalg.norm(rows[0])
    e3 = rows[2] - e1 * float(rows[2] @ e1)
    e3 /= np.linalg.norm(e3)
    e2 = np.cross(e3, e1)
    return np.array([e1, e2, e3])


def _hull_points(points: np.ndarray) -> np.ndarray:
    try:
        from scipy.spatial import ConvexHull

        h = ConvexHull(points)
        return points[h.vertices]
    except Exception:
        return points


def search_orientation(
    points: np.ndarray, base: np.ndarray, envelope: tuple[float, float, float], family: str
) -> dict[str, Any] | None:
    pts = _hull_points(np.asarray(points, float)) @ base.T
    mats, angles = _candidates(family)
    env = np.asarray(envelope) + FIT_TOLERANCE_MM
    chunk = max(1, int(2_000_000 // max(len(pts), 1)))
    for start in range(0, len(mats), chunk):
        block = mats[start : start + chunk]
        full = np.einsum("kij,nj->kni", block, pts)
        ext = full.max(axis=1) - full.min(axis=1)
        idx = np.flatnonzero(np.all(ext <= env, axis=1))
        if idx.size:
            k = int(idx[0])
            kk = start + k
            return {
                "rotation": mats[kk] @ base,
                "tilt_deg": float(angles[kk, 0]),
                "lean_deg": float(angles[kk, 1]),
                "turn_deg": float(angles[kk, 2]),
                "extents": ext[k],
            }
    return None


def obb_orientation(
    points: np.ndarray, envelope: tuple[float, float, float]
) -> dict[str, Any] | None:
    """Minimum-volume oriented bounding box (trimesh), with the axes permuted to fit."""
    import trimesh

    pts = _hull_points(np.asarray(points, float))
    to_origin, _ = trimesh.bounds.oriented_bounds(pts)
    r0 = np.asarray(to_origin)[:3, :3]
    for perm in itertools.permutations(range(3)):
        r = r0[list(perm)]
        if np.linalg.det(r) < 0:
            r = r * np.array([[1], [1], [-1]])
        q = pts @ r.T
        ext = q.max(axis=0) - q.min(axis=0)
        if np.all(ext <= np.asarray(envelope) + FIT_TOLERANCE_MM):
            return {
                "rotation": r,
                "tilt_deg": 0.0,
                "lean_deg": 0.0,
                "turn_deg": 0.0,
                "extents": ext,
            }
    return None


# ---------------------------------------------------------------------------
# Orientation policies
# ---------------------------------------------------------------------------


def surface_base(surf: Surface, s: float) -> np.ndarray:
    x2, _ = surf.axes(s)
    if surf.mirror_y:
        x2 = x2 * np.array([1, -1, 1])
    return base_frame([surf.axis_world(), np.zeros(3), x2])


def upright_base(larger_front: bool) -> np.ndarray:
    ex = np.array([1.0, 0, 0])
    return base_frame([np.array([0, 1.0, 0]), np.zeros(3), ex if larger_front else -ex])


def flat_face_bases(solid: cq.Shape, limit: int = 4) -> list[tuple[np.ndarray, float]]:
    faces = []
    for f in solid.Faces():
        if f.geomType() != "PLANE":
            continue
        n = f.normalAt(f.Center())
        faces.append((round(f.Area(), 3), (n.x, n.y, n.z)))
    faces.sort(key=lambda t: (-t[0], t[1]))
    out = []
    seen: list[np.ndarray] = []
    for area, n in faces:
        nv = np.array(n, float)
        if any(float(nv @ s) > 0.999 for s in seen):
            continue
        seen.append(nv)
        ref = np.array([1.0, 0, 0]) if abs(nv[0]) < 0.9 else np.array([0, 1.0, 0])
        e1 = ref - nv * float(ref @ nv)
        out.append((base_frame([e1, np.zeros(3), -nv]), area))
        if len(out) >= limit:
            break
    return out


ORIENTATION_REASONS = {
    "le_down": "Leading edge down: the layer lines run along the span, so wing bending is carried "
    "along the extruded lines instead of across layer bonds, and the rounded leading edge is "
    "self-supporting (no supports touch the aerodynamic surface).",
    "upright_x": "Standing on its larger cut face: the 0.8 mm shell prints as continuous hoops "
    "with no supports inside, and the joint face is flat on the bed.",
    "flat_face": "Largest flat face down: most bed contact and no supports under the flat face; "
    "holes perpendicular to the bed print round.",
    "obb": "Turned to the smallest oriented bounding box so it fits the printer.",
}


def orient(
    points: np.ndarray,
    policy: str,
    envelope: tuple[float, float, float],
    *,
    surf: Surface | None = None,
    s_mid: float = 0.0,
    larger_front: bool = True,
    solid: cq.Shape | None = None,
    strict: bool = False,
) -> dict[str, Any] | None:
    """Preferred orientation that fits, with its description; None when nothing fits.

    ``strict`` (used when planning split stations) only allows the preferred orientation with
    the small adjustments that keep its purpose: tilt about the span for wing pieces (needed
    when the chord is taller than the envelope), turns on the bed for fuselage pieces."""
    tries: list[tuple[str, np.ndarray, str]] = []
    if policy == "le_down" and surf is not None:
        tries.append(("le_down", surface_base(surf, s_mid), "tilt0"))
        if not strict:
            tries.append(("le_down", surface_base(surf, s_mid), "tilt"))
    elif policy == "upright_x":
        tries.append(("upright_x", upright_base(larger_front), "turn"))
        if not strict:
            tries.append(("upright_x", upright_base(larger_front), "tilt"))
    if strict:
        res = None
        for name, base, fam in tries:
            res = search_orientation(points, base, envelope, fam)
            if res is not None:
                res["policy"] = name
                break
        return res
    if solid is not None:
        for base, _ in flat_face_bases(solid):
            tries.append(("flat_face", base, "turn"))
    for name, base, _fam in list(tries):
        tries.append((name, base, "all"))
    for name, base, fam in tries:
        res = search_orientation(points, base, envelope, fam)
        if res is not None:
            res["policy"] = name
            break
    else:
        res = obb_orientation(points, envelope)
        if res is None:
            return None
        res["policy"] = "obb"
    res["reason"] = ORIENTATION_REASONS[res["policy"]]
    desc = {
        "le_down": "leading edge down, span along X",
        "upright_x": "standing on the cut face, fuselage axis vertical",
        "flat_face": "largest flat face on the bed",
        "obb": "oriented-bounding-box fit",
    }[res["policy"]]
    extra = []
    if res["tilt_deg"]:
        extra.append(f"tilted {abs(res['tilt_deg']):g}°")
    if res["lean_deg"]:
        extra.append(f"leaned {abs(res['lean_deg']):g}°")
    if res["turn_deg"]:
        extra.append(f"turned {abs(res['turn_deg']):g}° on the bed")
    if extra:
        desc += " (" + ", ".join(extra) + ")"
        if res["tilt_deg"] or res["lean_deg"]:
            res["reason"] += (
                " Tilted to fit the envelope height: use tree supports touching the build plate "
                "only."
            )
    res["description"] = desc
    return res


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def _surface_points(surf: Surface, sa: float, sb: float, with_keys: bool) -> np.ndarray:
    stations = sorted({sa, sb, *(s for s in surf.stations if sa < s < sb)})
    pts = [surf.section(s) for s in stations]
    if with_keys:
        pts.append(np.array(jn.surface_key_tips(surf, sb)))
    return np.vstack(pts)


def surface_forbidden(surf: Surface, s: float) -> str | None:
    if s - surf.s0 < MIN_PIECE_MM or surf.s1 - s < MIN_PIECE_MM:
        return "too close to the end"
    if surf.root_exclusion_mm > 0:
        if surf.root_s is None:
            if s - surf.s0 < surf.root_exclusion_mm:
                return "root exclusion zone"
        elif abs(s - surf.root_s) < surf.root_exclusion_mm:
            return "root exclusion zone"
    for a, b, why in surf.keepouts:
        if a <= s <= b:
            return why
    return None


def plan_surface(surf: Surface, envelope: tuple[float, float, float]) -> list[float]:
    """Interior split stations (span coordinate) for a lifting surface."""

    def fits(sa: float, sb: float) -> bool:
        pts = _surface_points(surf, sa, sb, sb < surf.s1 - 1e-6)
        res = orient(pts, "le_down", envelope, surf=surf, s_mid=0.5 * (sa + sb), strict=True)
        return res is not None

    if fits(surf.s0, surf.s1):
        return []
    greedy: list[float] = []
    sa = surf.s0
    while not fits(sa, surf.s1):
        lo, hi = sa + MIN_PIECE_MM, surf.s1 - MIN_PIECE_MM
        if hi <= lo or not fits(sa, lo):
            raise CadError(
                f"{surf.label}: a {MIN_PIECE_MM:g} mm long piece starting at "
                f"{sa:.0f} mm does not fit the {envelope[0]:g} x {envelope[1]:g} x "
                f"{envelope[2]:g} mm envelope in any orientation (chord too large). "
                "Reduce the chord or use a larger printer."
            )
        while hi - lo > 1.0:
            mid = 0.5 * (lo + hi)
            if fits(sa, mid):
                lo = mid
            else:
                hi = mid
        sb = math.floor(lo)
        while sb > sa + MIN_PIECE_MM and surface_forbidden(surf, sb):
            sb -= 1.0
        if sb <= sa + MIN_PIECE_MM or surface_forbidden(surf, sb):
            zone = surface_forbidden(surf, math.floor(lo)) or "a keep-out zone"
            raise CadError(
                f"{surf.label}: cannot place a joint between {sa:.0f} and {lo:.0f} mm "
                f"(the longest piece that fits ends inside the {zone}). Reduce the chord or "
                "use a larger printer."
            )
        greedy.append(sb)
        sa = sb
    # Balance: similar piece lengths, snapped out of the keep-out zones.
    n = len(greedy) + 1
    length = surf.s1 - surf.s0
    balanced = []
    for i in range(1, n):
        target = surf.s0 + i * length / n
        cand = None
        for k in range(0, 400):
            for s in (target + k, target - k):
                if not surface_forbidden(surf, s):
                    cand = float(round(s))
                    break
            if cand is not None:
                break
        if cand is None:
            balanced = []
            break
        balanced.append(cand)
    ok = bool(balanced) and all(b > a + MIN_PIECE_MM for a, b in itertools.pairwise(balanced))
    if ok:
        edges = [surf.s0, *balanced, surf.s1]
        ok = all(fits(a, b) for a, b in itertools.pairwise(edges))
    return balanced if ok else greedy


def fuselage_keepouts(model: CadModel, part_key: str) -> list[tuple[float, float, str]]:
    fm = model.fuselage
    out: list[tuple[float, float, str]] = []
    if part_key == "fuselage":
        x0 = fm.nose_bay_length
        out.append((x0 - 1, x0 + 20.0, "nose-bay flange"))
        xs = model.spar_x
        out.append((xs - 40.0, xs + 40.0, "wing clamp and spar holes"))
        for key in ("wing_right",):
            s = model.surfaces[key]
            if s.incidence_pin:
                xp = float(s.mid_point(s.s0, s.incidence_pin["xc"])[0])
                out.append((xp - 15.0, xp + 15.0, "incidence pin hole"))
        if model.tail_boom is not None:
            out.append((fm.length - 75.0, fm.length + 1, "tail boom socket"))
        if model.pusher_motor is not None:
            xp = min(model.params["pusher"]["x_mm"], fm.length - 6.0)
            out.append((xp - 20.0, xp + 10.0, "pusher firewall"))
    else:
        out.append((fm.nose_bay_length - 20.0, fm.nose_bay_length + 1, "mating flange"))
    return out


def fuselage_range(model: CadModel, part_key: str) -> tuple[float, float]:
    fm = model.fuselage
    if part_key == "fuselage":
        return fm.nose_bay_length, fm.length
    return 0.0, fm.nose_bay_length


def _fus_points(model: CadModel, xa: float, xb: float, with_keys: bool) -> np.ndarray:
    fm = model.fuselage
    xs = sorted({xa, xb, *(x for x in P._fuselage_stations(fm, xa, xb))})
    pts = [fm.section_points(max(x, 0.05)) for x in xs]
    if with_keys:
        pts.append(np.array([[xb + 6.0, y, z] for y, z in jn.fuselage_key_points(fm, xb)]))
    return np.vstack(pts)


def plan_fuselage(model: CadModel, part_key: str) -> list[float]:
    env = model.envelope
    fm = model.fuselage
    x0, x1 = fuselage_range(model, part_key)
    keep = fuselage_keepouts(model, part_key)

    def forbidden(x: float) -> str | None:
        if x - x0 < MIN_PIECE_MM or x1 - x < MIN_PIECE_MM:
            return "too close to the end"
        for a, b, why in keep:
            if a <= x <= b:
                return why
        return None

    strict = True

    def fits(xa: float, xb: float) -> bool:
        larger_front = fm.scale(xa) >= fm.scale(xb)
        pts = _fus_points(model, xa, xb, xb < x1 - 1e-6)
        return orient(pts, "upright_x", env, larger_front=larger_front, strict=strict) is not None

    if fits(x0, x1):
        return []
    if not fits(x0, x0 + MIN_PIECE_MM):
        strict = False  # cross-section too large to stand upright: allow tilted pieces
    out: list[float] = []
    xa = x0
    while not fits(xa, x1):
        lo, hi = xa + MIN_PIECE_MM, x1 - MIN_PIECE_MM
        if hi <= lo or not fits(xa, lo):
            raise CadError(
                "The fuselage cross-section does not fit the printer envelope in any "
                "orientation; reduce the fuselage width and height."
            )
        while hi - lo > 1.0:
            mid = 0.5 * (lo + hi)
            if fits(xa, mid):
                lo = mid
            else:
                hi = mid
        xb = math.floor(lo)
        while xb > xa + MIN_PIECE_MM and forbidden(xb):
            xb -= 1.0
        if forbidden(xb):
            raise CadError(
                f"Fuselage: no allowed transverse joint between {xa:.0f} and {lo:.0f} mm "
                f"({forbidden(math.floor(lo))}). Move the wing or change the fuselage length."
            )
        out.append(xb)
        xa = xb
    # balance
    n = len(out) + 1
    bal = []
    for i in range(1, n):
        target = x0 + i * (x1 - x0) / n
        cand = next(
            (
                float(round(s))
                for k in range(300)
                for s in (target + k, target - k)
                if not forbidden(s)
            ),
            None,
        )
        if cand is None:
            return out
        bal.append(cand)
    edges = [x0, *bal, x1]
    if all(b > a + MIN_PIECE_MM for a, b in itertools.pairwise(edges)) and all(
        fits(a, b) for a, b in itertools.pairwise(edges)
    ):
        return bal
    return out


# ---------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------


@dataclass
class Piece:
    part_key: str
    number: int
    count: int
    label: str
    solid: cq.Shape
    span: tuple[float, float] | None = None
    joints: list[dict[str, Any]] = field(default_factory=list)
    policy: str = "flat_face"
    surface: Surface | None = None
    larger_front: bool = True
    orientation: dict[str, Any] | None = None
    vertices: np.ndarray | None = None  # print orientation, on the bed
    faces: np.ndarray | None = None


def make_pieces(model: CadModel, spec: P.PartSpec) -> tuple[list[Piece], dict[str, Any]]:
    """Build the pieces of one part (aircraft coordinates) with their joints."""
    env = model.envelope
    plan: dict[str, Any] = {"stations_mm": [], "rules": []}
    if spec.split == "surface":
        surf = model.surfaces[spec.surface or spec.key]
        stations = plan_surface(surf, env)
        edges = [surf.s0, *stations, surf.s1]
        vertical = spec.key in model.tail_layout.get("vertical", [])
        solids = [
            (P.vertical_member if vertical else P.surface_piece)(model, surf, a, b)
            for a, b in itertools.pairwise(edges)
        ]
        recs: list[list[dict[str, Any]]] = [[] for _ in solids]
        for i, s in enumerate(stations):
            a, b, rec = jn.surface_joint(surf, s, solids[i], solids[i + 1])
            solids[i], solids[i + 1] = a, b
            recs[i].append({**rec, "side": "pins"})
            recs[i + 1].append({**rec, "side": "sockets"})
        pieces = [
            Piece(
                spec.key,
                i + 1,
                len(solids),
                f"{spec.short} {i + 1}/{len(solids)}",
                sol,
                span=(edges[i], edges[i + 1]),
                joints=recs[i],
                policy="le_down",
                surface=surf,
            )
            for i, sol in enumerate(solids)
        ]
        plan["stations_mm"] = stations
        plan["axis"] = "span coordinate (y for wings and the stabiliser, along the panel "
        "for V-tails, z for fins)"
        plan["root_exclusion_mm"] = surf.root_exclusion_mm
        plan["root_s_mm"] = surf.s0 if surf.root_s is None else surf.root_s
        plan["keepouts"] = [{"from_mm": a, "to_mm": b, "why": w} for a, b, w in surf.keepouts]
        plan["rules"] = [
            f"no joint within {surf.root_exclusion_mm:.0f} mm "
            f"({100 * 0.15:.0f} % of the panel) of the root",
            "no joint in a keep-out zone (attachments, hinge ends, tube ends)",
            f"pieces at least {MIN_PIECE_MM:g} mm long, balanced in length",
        ]
        return pieces, plan
    if spec.split == "fuselage":
        assert spec.build is not None
        whole = spec.build()
        stations = plan_fuselage(model, spec.key)
        x0, x1 = fuselage_range(model, spec.key)
        fm = model.fuselage
        edges = [x0, *stations, x1]
        if not stations:
            solids = [whole]
        else:
            hull = P.fuselage_loft(fm, P._fuselage_stations(fm, max(x0, 0.05), x1), 0.0)
            solids = [
                P.one_solid(
                    whole.intersect(jn._slab(a - (1 if i == 0 else 0), b + (1 if b == x1 else 0))),
                    spec.label,
                )
                for i, (a, b) in enumerate(itertools.pairwise(edges))
            ]
        recs = [[] for _ in solids]
        for i, x in enumerate(stations):
            a, b, rec = jn.fuselage_joint(model, fm, hull, x, solids[i], solids[i + 1])
            solids[i], solids[i + 1] = a, b
            recs[i].append({**rec, "side": "pins"})
            recs[i + 1].append({**rec, "side": "sockets"})
        pieces = [
            Piece(
                spec.key,
                i + 1,
                len(solids),
                f"{spec.short} {i + 1}/{len(solids)}",
                sol,
                span=(edges[i], edges[i + 1]),
                joints=recs[i],
                policy="upright_x",
                larger_front=fm.scale(edges[i]) >= fm.scale(edges[i + 1]),
            )
            for i, sol in enumerate(solids)
        ]
        plan["stations_mm"] = stations
        plan["axis"] = "x (from the nose)"
        plan["keepouts"] = [
            {"from_mm": a, "to_mm": b, "why": w} for a, b, w in fuselage_keepouts(model, spec.key)
        ]
        plan["rules"] = [
            "transverse joints only",
            "away from the wing clamp, spar and incidence-pin holes, the nose-bay flange, the "
            "tail-boom socket and the pusher firewall",
        ]
        return pieces, plan
    if spec.split == "fixed":
        assert spec.build_fixed is not None
        halves = spec.build_fixed()
        pieces = [
            Piece(spec.key, i + 1, len(halves), f"{spec.short} {name}", sol)
            for i, (name, sol) in enumerate(halves)
        ]
        plan["rules"] = ["assembly halves (bolted around the boom and the wing)"]
        return pieces, plan
    assert spec.build is not None
    return [Piece(spec.key, 1, 1, spec.short, spec.build())], plan


# ---------------------------------------------------------------------------
# Meshing, orientation and the fit check
# ---------------------------------------------------------------------------


def tessellate(solid: cq.Shape, tol: float = MESH_TOLERANCE_MM) -> tuple[np.ndarray, np.ndarray]:
    """Triangle mesh of a solid; retries other deflections if the first is not watertight."""
    import trimesh

    first = None
    for k in (1.0, 0.6, 1.6, 0.4):
        verts, tris = solid.tessellate(tol * k, MESH_ANGULAR_RAD)
        v = np.array([(p.x, p.y, p.z) for p in verts], dtype=np.float64)
        f = np.array(tris, dtype=np.int64)
        mesh = trimesh.Trimesh(v, f, process=True, validate=False)
        mesh.merge_vertices(digits_vertex=6)
        mesh.remove_unreferenced_vertices()
        out = (np.asarray(mesh.vertices), np.asarray(mesh.faces))
        if first is None:
            first = out
        if mesh.is_watertight and mesh.is_winding_consistent:
            return out
    assert first is not None
    return first


def orient_piece(
    piece: Piece, envelope: tuple[float, float, float], bed: tuple[float, float], tol: float
) -> None:
    v, f = tessellate(piece.solid, tol)
    s_mid = 0.5 * (piece.span[0] + piece.span[1]) if piece.span else 0.0
    res = orient(
        v,
        piece.policy,
        envelope,
        surf=piece.surface,
        s_mid=s_mid,
        larger_front=piece.larger_front,
        solid=piece.solid,
    )
    if res is None:
        ext = np.ptp(v, axis=0)
        raise EnvelopeError(
            f"{piece.label} does not fit the {envelope[0]:g} x {envelope[1]:g} x {envelope[2]:g} "
            f"mm envelope in any orientation (axis-aligned size {ext[0]:.0f} x {ext[1]:.0f} x "
            f"{ext[2]:.0f} mm)."
        )
    r = res["rotation"]
    q = v @ r.T
    lo, hi = q.min(axis=0), q.max(axis=0)
    shift = np.array(
        [bed[0] / 2 - 0.5 * (lo[0] + hi[0]), bed[1] / 2 - 0.5 * (lo[1] + hi[1]), -lo[2]]
    )
    q = q + shift
    piece.vertices = q
    piece.faces = f
    res["translation"] = shift
    piece.orientation = res
    check_fit(piece, envelope)


def check_fit(piece: Piece, envelope: tuple[float, float, float]) -> dict[str, Any]:
    """The exported mesh, in its print orientation, must fit the usable envelope."""
    assert piece.vertices is not None
    ext = piece.vertices.max(axis=0) - piece.vertices.min(axis=0)
    fits = bool(np.all(ext <= np.asarray(envelope) + FIT_TOLERANCE_MM))
    if not fits:
        raise EnvelopeError(
            f"{piece.label} is {ext[0]:.1f} x {ext[1]:.1f} x {ext[2]:.1f} mm in its print "
            f"orientation, larger than the usable envelope {envelope[0]:g} x {envelope[1]:g} x "
            f"{envelope[2]:g} mm."
        )
    return {"fits": True, "size_mm": [float(e) for e in ext]}
