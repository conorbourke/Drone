"""CadQuery solids for every printed part, in aircraft coordinates (mm).

Each printed part is described by a :class:`PartSpec`. Lifting surfaces are lofted piece by piece
(``surface_piece``) between the split stations chosen by ``split.py`` so the tube channels, the
boom bore and the hinge groove are cut from every piece with the same straight cutter (the
channel is continuous across every joint by construction). Other parts are built whole and split
by ``split.py`` when they exceed the printer envelope.

Purchased items (carbon tubes, motors, propellers, battery, payload) are built as simple solids
for the assembly STEP only (``assembly_extras``).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import cadquery as cq
import numpy as np

from app.cad.model import (
    FIN_SPAR_CHORD_FRACTION,
    HINGE_CHORD_FRACTION,
    SPAR_CLEARANCE_MM,
    TAIL_MOUNT_TOP_MM,
    CadError,
    CadModel,
    FuselageModel,
    Surface,
)

# ---------------------------------------------------------------------------
# OCC helpers
# ---------------------------------------------------------------------------


def V(p: Any) -> cq.Vector:
    return cq.Vector(float(p[0]), float(p[1]), float(p[2]))


def one_solid(shape: cq.Shape, what: str) -> cq.Solid:
    """The single solid of a boolean result; a split into several bodies is an error."""
    solids = shape.Solids()
    if not solids:
        raise CadError(f"CAD operation produced no solid for {what}.")
    if len(solids) > 1:
        solids = sorted(solids, key=lambda s: -s.Volume())
        main = solids[0]
        rest = sum(s.Volume() for s in solids[1:])
        if rest > 1e-3 * main.Volume() and rest > 5.0:
            raise CadError(
                f"CAD operation split {what} into {len(solids)} bodies "
                f"({rest:.0f} mm³ outside the main body)."
            )
        return main
    return solids[0]


def airfoil_wire(pts: np.ndarray) -> cq.Wire:
    """Spline through the section points (TE upper -> LE -> TE lower) closed by a TE line."""
    vs = [V(p) for p in pts]
    spline = cq.Edge.makeSpline(vs)
    edges = [spline]
    if (vs[0] - vs[-1]).Length > 1e-6:
        edges.append(cq.Edge.makeLine(vs[-1], vs[0]))
    return cq.Wire.assembleEdges(edges)


def polygon_wire(pts: np.ndarray) -> cq.Wire:
    return cq.Wire.makePolygon([V(p) for p in pts], close=True)


def loft(wires: list[cq.Wire], ruled: bool = True) -> cq.Solid:
    return cq.Solid.makeLoft(wires, ruled)


def cylinder_between(p0: Any, p1: Any, d: float) -> cq.Solid:
    a, b = np.asarray(p0, float), np.asarray(p1, float)
    axis = b - a
    h = float(np.linalg.norm(axis))
    return cq.Solid.makeCylinder(d / 2, h, V(a), V(axis / h))


def cone_between(p0: Any, p1: Any, d0: float, d1: float) -> cq.Solid:
    a, b = np.asarray(p0, float), np.asarray(p1, float)
    axis = b - a
    h = float(np.linalg.norm(axis))
    return cq.Solid.makeCone(d0 / 2, d1 / 2, h, V(a), V(axis / h))


def box(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> cq.Solid:
    return cq.Solid.makeBox(x1 - x0, y1 - y0, z1 - z0, cq.Vector(x0, y0, z0))


def oriented_box(center: Any, ex: Any, ey: Any, ez: Any, dims: tuple[float, float, float]):
    """Box of size ``dims`` along the orthonormal axes (ex, ey, ez), centred at ``center``."""
    lx, ly, lz = dims
    b = cq.Solid.makeBox(lx, ly, lz, cq.Vector(-lx / 2, -ly / 2, -lz / 2))
    return transform(b, np.column_stack([ex, ey, ez]), np.asarray(center, float))


def transform(shape: cq.Shape, rot: np.ndarray, trans: np.ndarray) -> cq.Shape:
    """Rigid transform (rotation matrix and translation) of a shape."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf

    u, _, vt = np.linalg.svd(np.asarray(rot, float))
    r = u @ vt  # exactly orthonormal (OCC refuses near-orthogonal matrices)
    if np.linalg.det(r) < 0:
        raise ValueError("transform: rotation must be proper (det = +1)")
    t = np.asarray(trans, float)
    tr = gp_Trsf()
    tr.SetValues(
        *(float(v) for v in (r[0, 0], r[0, 1], r[0, 2], t[0])),
        *(float(v) for v in (r[1, 0], r[1, 1], r[1, 2], t[1])),
        *(float(v) for v in (r[2, 0], r[2, 1], r[2, 2], t[2])),
    )
    return cq.Shape.cast(BRepBuilderAPI_Transform(shape.wrapped, tr, True).Shape())


def frame_from_axis(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Orthonormal (u, v, axis) with u as close to +x as possible."""
    a = axis / np.linalg.norm(axis)
    ref = np.array([1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.array([0.0, 0.0, 1.0])
    u = ref - a * float(ref @ a)
    u /= np.linalg.norm(u)
    v = np.cross(a, u)
    return u, v, a


def section_wire(fm: FuselageModel, x: float, inset: float = 0.0, a_b: Any = None) -> cq.Wire:
    """Exact ellipse or rounded-rectangle wire in the plane x = const."""
    a, b = a_b if a_b is not None else fm.half_size(x, inset)
    a, b = max(a, 0.3), max(b, 0.3)
    if fm.cross_section == "ellipse":
        if abs(a - b) < 1e-6:
            return cq.Wire.makeCircle(a, cq.Vector(x, 0, 0), cq.Vector(1, 0, 0))
        if a >= b:
            return cq.Wire.makeEllipse(
                a, b, cq.Vector(x, 0, 0), cq.Vector(1, 0, 0), cq.Vector(0, 1, 0)
            )
        return cq.Wire.makeEllipse(b, a, cq.Vector(x, 0, 0), cq.Vector(1, 0, 0), cq.Vector(0, 0, 1))
    r = 0.25 * min(2 * a, 2 * b)
    cy, cz = a - r, b - r
    k = math.sqrt(0.5)

    def p(y: float, z: float) -> cq.Vector:
        return cq.Vector(x, y, z)

    edges = []
    corners = [(1, 1), (-1, 1), (-1, -1), (1, -1)]
    # walk counter-clockwise: right side, top-right corner, top, ...
    pts_seq = []
    for sy, sz in corners:
        start = p(sy * a, sz * cz) if sy * sz > 0 else p(sy * cy, sz * b)
        mid = p(sy * (cy + r * k), sz * (cz + r * k))
        end = p(sy * cy, sz * b) if sy * sz > 0 else p(sy * a, sz * cz)
        pts_seq.append((start, mid, end))
    for i, (s, m, e) in enumerate(pts_seq):
        edges.append(cq.Edge.makeThreePointArc(s, m, e))
        nxt = pts_seq[(i + 1) % 4][0]
        if (e - nxt).Length > 1e-6:
            edges.append(cq.Edge.makeLine(e, nxt))
    return cq.Wire.assembleEdges(edges)


def fuselage_loft(fm: FuselageModel, xs: list[float], inset: float, ruled: bool = True):
    wires = [section_wire(fm, x, inset) for x in xs]
    return loft(wires, ruled)


def prism_x(fm: FuselageModel, x0: float, x1: float, a_b: tuple[float, float]) -> cq.Solid:
    w = section_wire(fm, x0, a_b=a_b)
    return cq.Solid.extrudeLinear(cq.Face.makeFromWires(w), cq.Vector(x1 - x0, 0, 0))


# ---------------------------------------------------------------------------
# Part description
# ---------------------------------------------------------------------------


@dataclass
class PartSpec:
    key: str
    label: str
    short: str
    profile: str  # key of model.PRINT_PROFILES
    split: str  # "surface" | "fuselage" | "none" | "fixed"
    orientation: str  # "le_down" | "upright_x" | "flat_face"
    description: str
    quantity: int = 1
    surface: str | None = None
    build: Callable[[], cq.Shape] | None = None
    build_fixed: Callable[[], list[tuple[str, cq.Shape]]] | None = None
    instances: list[tuple[np.ndarray, np.ndarray]] = field(default_factory=list)
    post: list[str] = field(default_factory=list)
    hardware: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Lifting-surface pieces
# ---------------------------------------------------------------------------


def surface_piece(model: CadModel, surf: Surface, sa: float, sb: float) -> cq.Solid:
    """One spanwise piece [sa, sb] of a surface with its continuous channels cut."""
    stations = sorted({sa, sb, *(s for s in surf.stations if sa + 0.5 < s < sb - 0.5)})
    solid: cq.Shape = loft([airfoil_wire(surf.section(s)) for s in stations], ruled=True)
    cutters: list[cq.Shape] = []
    line = surf.spar_line()
    if line is not None:
        lo = max(sa, surf.spar_s_start)
        hi = min(sb, surf.spar_s_end)
        if hi > lo:
            p0 = surf.spar_point_at(lo - (5.0 if lo <= surf.s0 + 1e-6 else 0.5))
            p1 = surf.spar_point_at(hi + (0.5 if hi < sb else 0.0))
            if lo <= surf.s0 + 1e-6 and not surf.spar_open_root:
                p0 = surf.spar_point_at(lo)
            cutters.append(cylinder_between(p0, p1, surf.spar_od_mm + SPAR_CLEARANCE_MM))
    if surf.key.startswith("wing"):
        bm = model.boom
        y = -bm["y_mm"] if surf.mirror_y else bm["y_mm"]
        x0 = bm["x0_mm"]
        cutters.append(
            cylinder_between(
                (x0 - 5, y, bm["z_mm"]),
                (x0 + bm["length_mm"] + 5, y, bm["z_mm"]),
                bm["diameter_mm"] + SPAR_CLEARANCE_MM,
            )
        )
        if surf.hinge:
            h0, h1 = max(surf.hinge[0], sa), min(surf.hinge[1], sb)
            if h1 - h0 > 2:
                cutters.append(hinge_groove(surf, h0, h1))
        side = "left" if surf.mirror_y else "right"
        bm_y = bm["y_mm"]
        if sa - 1 <= bm_y <= sb + 1:
            for xb, yb_ in boom_clamp_geometry(model, side)["bolts"]:
                cutters.append(
                    cylinder_between((xb, yb_, bm["z_mm"] - 60), (xb, yb_, bm["z_mm"] + 120), 3.4)
                )
        if surf.incidence_pin and sa <= surf.s0 + 1e-6:
            ip = surf.incidence_pin
            p0 = surf.mid_point(surf.s0 - 1.0, ip["xc"])
            p1 = surf.mid_point(surf.s0 + ip["depth"], ip["xc"])
            cutters.append(cylinder_between(p0, p1, ip["diameter_mm"] + SPAR_CLEARANCE_MM))
    for sock in model.tail_layout.get("sockets", []):
        if sock["surface"] == surf.key and sa - 1 <= sock["y"] <= sb + 1:
            z = surf.mid_point(sock["y"], FIN_SPAR_CHORD_FRACTION)[2]
            x = sock["x"]
            cutters.append(
                cylinder_between(
                    (x, sock["y"], z - 30), (x, sock["y"], z + 1.0), model.tail_spar_od + 0.3
                )
            )
    if cutters:
        solid = solid.cut(*cutters)
    return one_solid(solid, f"{surf.label} piece {sa:.0f}-{sb:.0f}")


def hinge_groove(surf: Surface, s0: float, s1: float) -> cq.Solid:
    """0.8 mm wide, 0.6 mm deep groove on the upper surface along the hinge line."""
    stations = sorted({s0, s1, *(s for s in surf.stations if s0 < s < s1)})
    wires = []
    for s in stations:
        x2, n2 = surf.axes(s)
        if surf.mirror_y:
            x2 = x2 * np.array([1, -1, 1])
            n2 = n2 * np.array([1, -1, 1])
        p = surf.upper_point(s, HINGE_CHORD_FRACTION)
        quad = [
            p - 0.4 * x2 - 0.6 * n2,
            p + 0.4 * x2 - 0.6 * n2,
            p + 0.4 * x2 + 2.0 * n2,
            p - 0.4 * x2 + 2.0 * n2,
        ]
        wires.append(polygon_wire(np.array(quad)))
    return loft(wires, ruled=True)


# ---------------------------------------------------------------------------
# Fuselage and nose bay
# ---------------------------------------------------------------------------


def _fuselage_stations(fm: FuselageModel, x0: float, x1: float) -> list[float]:
    xs = {x0, x1}
    for i in range(1, 13):
        x = fm.nose_length * i / 12
        if x0 < x < x1:
            xs.add(x)
    xt = fm.length - fm.tail_length
    if x0 < xt < x1:
        xs.add(xt)
    return sorted(xs)


def flange_holes(fm: FuselageModel, x: float) -> list[tuple[float, float]]:
    a, b = fm.half_size(x, fm.wall + 4.0)
    return [(a, 0.0), (-a, 0.0), (0.0, b), (0.0, -b)]


def build_fuselage(model: CadModel) -> cq.Solid:
    fm = model.fuselage
    x0, x1 = fm.nose_bay_length, fm.length
    xs = _fuselage_stations(fm, x0, x1)
    outer = fuselage_loft(fm, xs, 0.0)
    inner_xs = [x0 - 1.0, *xs[1:-1], x1 - 2.0]
    inner = loft(
        [section_wire(fm, x, a_b=fm.half_size(max(x, x0), fm.wall)) for x in inner_xs[:1]]
        + [section_wire(fm, x, fm.wall) for x in inner_xs[1:]],
        True,
    )
    shell: cq.Shape = outer.cut(inner)
    adds: list[cq.Shape] = []
    cuts: list[cq.Shape] = []
    # Nose-bay mating flange (heat-set insert holes) at the front opening.
    a_in, b_in = fm.half_size(x0, fm.wall - 0.01)
    ring = prism_x(fm, x0, x0 + 3.0, (a_in, b_in)).cut(
        prism_x(fm, x0 - 1, x0 + 4.0, fm.half_size(x0, fm.wall + 8.0))
    )
    adds.append(ring)
    for y, z in flange_holes(fm, x0):
        cuts.append(cylinder_between((x0 - 1, y, z), (x0 + 6, y, z), 4.0))
    # Spar and incidence-pin holes through the side walls.
    for key in ("wing_right", "wing_left"):
        s = model.surfaces[key]
        cuts.append(
            cylinder_between(
                s.spar_point_at(0.5), s.spar_point_at(s.s0 + 3.0), s.spar_od_mm + SPAR_CLEARANCE_MM
            )
        )
        ip = s.incidence_pin or {}
        if ip:
            cuts.append(
                cylinder_between(
                    s.mid_point(s.s0 - 8.0, ip["xc"]),
                    s.mid_point(s.s0 + 1.0, ip["xc"]),
                    ip["diameter_mm"] + SPAR_CLEARANCE_MM,
                )
            )
    # Tail end: closed cap with a socket sleeve for the tail boom.
    tb = model.tail_boom
    if tb is not None:
        d = tb["diameter_mm"] + SPAR_CLEARANCE_MM
        sleeve = cylinder_between((x1 - 60, 0, 0), (x1, 0, 0), d + 3.2)
        adds.append(sleeve.intersect(outer))
        cuts.append(cylinder_between((x1 - 61, 0, 0), (x1 + 1, 0, 0), d))
    else:
        for name in model.tail_layout.get("vertical", []):
            surf = model.surfaces[name]
            x = surf.origin[0] + FIN_SPAR_CHORD_FRACTION * surf.chord(surf.s0)
            if x < x1 - 5:
                cuts.append(
                    cylinder_between((x, 0, 0), (x, 0, fm.height), model.tail_spar_od + 0.3)
                )
    if model.pusher_motor is not None:
        adds.append(pusher_plate(model, with_holes=False).intersect(outer))
    if adds:
        shell = shell.fuse(*adds)
    if cuts:
        shell = shell.cut(*cuts)
    return one_solid(shell, "fuselage")


def build_nose_bay(model: CadModel) -> cq.Solid:
    fm = model.fuselage
    L = fm.nose_bay_length
    xs = [0.05] + [x for x in _fuselage_stations(fm, 0.0, L) if x > 0.05]
    outer = fuselage_loft(fm, xs, 0.0, ruled=False)
    # Inner cavity starts where the section is big enough for the wall.
    x_in = next(
        (x for x in np.linspace(1, L, 200) if min(fm.half_size(float(x), fm.wall)) > 3.0), L / 3
    )
    inner_xs = [float(x_in)] + [x for x in xs if x > x_in + 0.5 and x < L] + [L + 1.0]
    inner = loft(
        [section_wire(fm, x, a_b=fm.half_size(min(x, L), fm.wall)) for x in inner_xs], False
    )
    body: cq.Shape = outer.cut(inner)
    _, b_l = fm.half_size(L)
    # Rear mating flange.
    ring = prism_x(fm, L - 3.0, L, fm.half_size(L, fm.wall - 0.01)).cut(
        prism_x(fm, L - 4.0, L + 1.0, fm.half_size(L, fm.wall + 8.0))
    )
    # Four M3 heat-set bosses on a deck, with a camera window in the lower skin.
    z_deck = -0.45 * b_l
    bosses = []
    holes = []
    for xf in (0.6, 0.88):
        for sy in (-1, 1):
            x, y = xf * L, sy * 0.22 * fm.nose_bay_width
            bosses.append(cylinder_between((x, y, -b_l - 2), (x, y, z_deck), 7.0))
            holes.append(cylinder_between((x, y, z_deck - 6.0), (x, y, z_deck + 1), 4.0))
    boss_u = bosses[0].fuse(*bosses[1:]).intersect(outer)
    body = body.fuse(ring, boss_u)
    xw0, xw1 = 0.22 * L, 0.5 * L
    _, bw = fm.half_size(xw0)
    win = box(xw0, xw1, -0.3 * fm.nose_bay_width, 0.3 * fm.nose_bay_width, -b_l - 5, -0.55 * bw)
    holes.append(win)
    for y, z in flange_holes(fm, L):
        holes.append(cylinder_between((L - 5, y, z), (L + 1, y, z), 3.4))
    body = body.cut(*holes)
    return one_solid(body, "nose bay")


def wing_clamp(model: CadModel) -> cq.Solid:
    """Wing-to-fuselage clamp: spar sockets at the dihedral angle, cross-bolt holes."""
    fm = model.fuselage
    xs = model.spar_x
    r = model.spar["outer_mm"] / 2
    zc = float(model.surfaces["wing_right"].spar_point_at(0.0)[2])
    a_in, b_in = fm.half_size(xs, fm.wall + 0.3)
    body = prism_x(fm, xs - 15, xs + 15, (a_in, b_in)).intersect(
        box(xs - 16, xs + 16, -a_in - 1, a_in + 1, zc - r - 6, zc + r + 6)
    )
    cuts = []
    for key in ("wing_right", "wing_left"):
        s = model.surfaces[key]
        cuts.append(
            cylinder_between(
                s.spar_point_at(2.0), s.spar_point_at(s.s0 + 5), s.spar_od_mm + SPAR_CLEARANCE_MM
            )
        )
        sp = s.spar_point_at(0.55 * a_in)
        up = np.array([0.0, 0.0, 30.0])
        cuts.append(cylinder_between(sp - up, sp + up, 3.2))
    return one_solid(body.cut(*cuts), "wing clamp")


# ---------------------------------------------------------------------------
# Booms: clamps, motor mounts, tilt mechanism, landing gear
# ---------------------------------------------------------------------------


def boom_clamp_geometry(model: CadModel, side: str) -> dict[str, Any]:
    """Extent of the boom-to-wing clamp and its four M3 bolt positions (x, y)."""
    surf = model.surfaces["wing_right" if side == "right" else "wing_left"]
    bm = model.boom
    sgn = -1.0 if side == "left" else 1.0
    yo = bm["y_mm"]
    w = bm["diameter_mm"] + 12.0
    ya, yb = yo - w / 2, yo + w / 2
    secs = [surf.section(s) for s in (ya - 1.0, yo, yb + 1.0)]
    pts = np.vstack(secs)
    xle = float(pts[:, 0].min())
    chord = surf.chord(yo)
    env = model.envelope
    max_len = 0.9 * math.hypot(env[0], env[1]) - w
    x0 = xle - 10.0
    # Front half of the chord: around the spar, where the wing is deepest; the rear bolts
    # pass through the wing (matching holes are cut in the wing piece).
    x1 = min(xle + 0.5 * chord + 8.0, x0 + max_len)
    rb = bm["diameter_mm"] / 2
    yc = sgn * yo
    bolts = [(xb, yc + dy) for xb in (x0 + 5.0, x1 - 6.0) for dy in (-(rb + 3.5), rb + 3.5)]
    return {
        "surf": surf,
        "secs": secs,
        "x0": x0,
        "x1": x1,
        "y": sorted((sgn * ya, sgn * yb)),
        "zmax": float(pts[:, 2].max()),
        "bolts": bolts,
        "rb": rb,
        "yc": yc,
    }


def boom_clamp_halves(model: CadModel, side: str) -> list[tuple[str, cq.Shape]]:
    """Boom-to-wing clamp around the boom and the local wing section, split upper/lower."""
    geo = boom_clamp_geometry(model, side)
    bm = model.boom
    x0, x1, (y0, y1), zmax, rb = geo["x0"], geo["x1"], geo["y"], geo["zmax"], geo["rb"]
    zb = bm["z_mm"]
    cuff = box(x0, x1, y0, y1, zb - rb - 3.0, max(zmax, zb + rb) + 2.5)
    wing_local = loft([airfoil_wire(s) for s in geo["secs"]], ruled=True)
    yc = geo["yc"]
    cuts = [
        wing_local,
        cylinder_between((x0 - 1, yc, zb), (x1 + 1, yc, zb), bm["diameter_mm"] + 0.2),
    ]
    for xb, yb_ in geo["bolts"]:
        cuts.append(cylinder_between((xb, yb_, zb - 40), (xb, yb_, zmax + 40), 3.4))
    cuff = cuff.cut(*cuts)
    big = 1e4
    upper = one_solid(cuff.intersect(box(-big, big, -big, big, zb, big)), "boom clamp upper")
    lower = one_solid(cuff.intersect(box(-big, big, -big, big, -big, zb)), "boom clamp lower")
    return [("upper", upper), ("lower", lower)]


def _ring(rb: float, length: float, wall: float = 3.0) -> cq.Shape:
    return cylinder_between((-length / 2, 0, 0), (length / 2, 0, 0), 2 * (rb + 0.15 + wall))


def _bore(rb: float, length: float) -> cq.Shape:
    return cylinder_between((-length, 0, 0), (length, 0, 0), 2 * (rb + 0.15))


def motor_plate_holes(pattern: dict[str, Any], z0: float, z1: float, rot45: bool = True):
    a, b = pattern["a_mm"] / 2, pattern["b_mm"] / 2
    d = pattern["screw_mm"] + 0.3
    pts = [(a, 0.0), (-a, 0.0), (0.0, b), (0.0, -b)]
    if rot45:
        c = math.sqrt(0.5)
        pts = [(c * (x - y), c * (x + y)) for x, y in pts]
    return [cylinder_between((x, y, z0), (x, y, z1), d) for x, y in pts]


def motor_plate_height(model: CadModel, tilt: bool) -> float:
    rb = model.boom["diameter_mm"] / 2
    want = model.params["motors"]["height_mm"]
    need = rb + 3.15 + 10.0
    if tilt:
        need = tilt_pin_height(model) + 8.0 + 2.0 + 4.0 + 3.0
    return max(want, need)


def fixed_motor_mount(model: CadModel, motor: dict[str, Any]) -> cq.Solid:
    """Clamp ring on the boom (axis x), web, motor plate with the motor's bolt pattern on top."""
    rb = model.boom["diameter_mm"] / 2
    pd = motor["diameter_mm"] + 4.0
    h = motor_plate_height(model, tilt=False)
    lc = max(24.0, 0.6 * pd)
    ring = _ring(rb, lc)
    web = box(-lc / 2, lc / 2, -4.0, 4.0, 0.0, h - 3.5)
    web2 = box(-3.5, 3.5, -0.3 * pd, 0.3 * pd, rb, h - 3.5)
    plate = cylinder_between((0, 0, h - 4.0), (0, 0, h), pd)
    body = ring.fuse(web, web2, plate)
    cuts = [_bore(rb, lc), cylinder_between((0, 0, h - 10), (0, 0, h + 1), 10.0)]
    cuts += motor_plate_holes(motor["pattern"], h - 5.0, h + 1.0)
    cuts.append(cylinder_between((0, 0, -rb - 10), (0, 0, -rb + 2), 2.5))  # set screw pilot
    return one_solid(body.cut(*cuts), "motor mount")


def tilt_pin_height(model: CadModel) -> float:
    return model.boom["diameter_mm"] / 2 + 3.15 + 9.0


HUB_R = 8.0
HUB_W = 16.0
CHEEK_T = 4.0


def _pin_d(model: CadModel) -> float:
    return 4.0 if model.lift_motor["mass_g"] > 70 else 3.0


def tilt_hinge_block(model: CadModel) -> cq.Solid:
    """Boom clamp with hinge cheeks, hinge-pin bore, servo pocket and the tilt stops."""
    rb = model.boom["diameter_mm"] / 2
    zp = tilt_pin_height(model)
    sv = model.tilt_servo or {}
    sl, sw, sh = sv.get("length_mm", 28.0), sv.get("width_mm", 13.5), sv.get("height_mm", 30.0)
    lc = max(28.0, sl + 8.0)
    ring = _ring(rb, lc)
    yc = HUB_W / 2 + 0.5 + CHEEK_T / 2
    cheeks = [
        box(-11, 11, sgn * yc - CHEEK_T / 2, sgn * yc + CHEEK_T / 2, 0, zp + 8) for sgn in (-1, 1)
    ]
    base = box(-11, 11, -yc - CHEEK_T / 2, yc + CHEEK_T / 2, rb, rb + 5.0)
    # Servo pod under the boom: servo lies with its length along x and its output shaft along y.
    z_top = -rb - 1.0
    pod = box(-lc / 2, lc / 2, -(sh / 2 + 2.0), sh / 2, z_top - sw - 4.5, z_top + 2.0)
    body = ring.fuse(*cheeks, base, pod)
    pocket = box(
        -(sl + 0.5) / 2, (sl + 0.5) / 2, -(sh / 2), sh / 2 + 1.0, z_top - sw - 2.5, z_top - 2.0
    )
    # Stops: posts on the inner face of the +y cheek, hit by the tilting arm at 0 deg (hover)
    # and at tilt.max_angle_deg (cruise). The arm rotates about +y; forward tilt is towards -x.
    max_angle = math.radians(model.params["tilt"]["max_angle_deg"])
    r_stop = HUB_R + 4.0
    beta = math.asin(min(0.95, (5.0 + 2.0) / r_stop))
    posts = []
    for ang in (beta, -(max_angle + beta)):
        px, pz = r_stop * math.sin(ang), zp + r_stop * math.cos(ang)
        y_in = HUB_W / 2 + 0.5
        posts.append(cylinder_between((px, y_in + 0.5, pz), (px, y_in - 2.5, pz), 4.0))
    body = body.fuse(*posts)
    pd = _pin_d(model)
    cuts = [
        _bore(rb, lc),
        pocket,
        cylinder_between((0, -yc - 5, zp), (0, yc + 5, zp), pd + 0.2),
        cylinder_between((-sl / 2 - 3, 0, z_top - sw / 2 - 2), (-sl / 2 - 3, 0, z_top + 8), 2.2),
    ]
    return one_solid(body.cut(*cuts), "tilt hinge block")


def tilt_motor_mount(model: CadModel) -> cq.Solid:
    """Tilting part: hub on the hinge pin, arm, motor plate and the servo horn link tab.

    Local frame: hinge pin on the y axis through the origin, motor axis +z at hover."""
    motor = model.lift_motor
    pd = motor["diameter_mm"] + 4.0
    zp = tilt_pin_height(model)
    h = motor_plate_height(model, tilt=True) - zp
    hub = cylinder_between((0, -HUB_W / 2, 0), (0, HUB_W / 2, 0), 2 * HUB_R)
    arm = box(-6.5, 6.5, -HUB_W / 2, HUB_W / 2, 0.0, h - 3.5)
    plate = cylinder_between((0, 0, h - 4.0), (0, 0, h), pd)
    tab = box(HUB_R - 2, HUB_R + 10, -2.0, 2.0, -4.0, 4.0)
    body = hub.fuse(arm, plate, tab)
    cuts = [
        cylinder_between((0, -HUB_W, 0), (0, HUB_W, 0), _pin_d(model) + 0.2),
        cylinder_between((HUB_R + 6, -5, 0), (HUB_R + 6, 5, 0), 2.1),
        cylinder_between((0, 0, h - 10), (0, 0, h + 1), 10.0),
    ]
    cuts += motor_plate_holes(motor["pattern"], h - 5.0, h + 1.0)
    return one_solid(body.cut(*cuts), "tilt motor mount")


def pusher_plate(model: CadModel, with_holes: bool = True) -> cq.Solid:
    fm = model.fuselage
    x = min(model.params["pusher"]["x_mm"], fm.length - 6.0)
    a, b = fm.half_size(x, fm.wall - 0.01)
    plate = prism_x(fm, x - 4.0, x, (a, b))
    if not with_holes:
        return plate
    pm = model.pusher_motor or {}
    pat = pm.get("pattern", {"a_mm": 19.0, "b_mm": 19.0, "screw_mm": 3.0})
    holes = [
        transform(hh, np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]], float), np.array([x, 0, 0]))
        for hh in motor_plate_holes(pat, -6.0, 6.0)
    ]
    holes.append(cylinder_between((x - 6, 0, 0), (x + 1, 0, 0), 10.0))
    return one_solid(plate.cut(*holes), "pusher mount")


def build_pusher_mount(model: CadModel) -> cq.Solid:
    return pusher_plate(model, with_holes=True)


def gear_positions(model: CadModel) -> list[float]:
    g = model.geometry
    xf, xr = g["front_rotor_x_mm"], g["rear_rotor_x_mm"]
    kind = model.params["landing_gear"]["type"]
    if kind == "skids":
        return [xf + 0.25 * (xr - xf), xr - 0.25 * (xr - xf)]
    if kind == "legs":
        return [xf + 30.0, xr - 30.0]
    return []


SKID_TUBE_MM = 8.0


def gear_leg(model: CadModel) -> cq.Solid:
    """Leg on the boom: clamp ring, strut and a skid-tube socket or a foot pad (local frame)."""
    rb = model.boom["diameter_mm"] / 2
    length = model.boom["z_mm"] - model.geometry["landing_gear_bottom_z_mm"]
    length = max(length, rb + 20.0)
    w = 16.0
    ring = _ring(rb, w, wall=3.5)
    strut = box(-w / 2, w / 2, -5.0, 5.0, -length + 1.0, -rb)
    if model.params["landing_gear"]["type"] == "skids":
        rs = SKID_TUBE_MM / 2 + 0.15
        foot = cylinder_between(
            (-w / 2, 0, -length + rs + 2.5), (w / 2, 0, -length + rs + 2.5), 2 * (rs + 2.5)
        )
        cuts = [
            _bore(rb, w),
            cylinder_between((-w, 0, -length + rs + 2.5), (w, 0, -length + rs + 2.5), 2 * rs),
        ]
    else:
        foot = box(-w / 2, w / 2, -15.0, 15.0, -length, -length + 6.0)
        cuts = [_bore(rb, w)]
    body = ring.fuse(strut, foot)
    return one_solid(body.cut(*cuts), "landing gear leg")


def tail_mount(model: CadModel, tube_d: float) -> cq.Solid:
    """Block clamped on a tube (axis x) with a vertical socket for the fin/pylon spar."""
    rt = tube_d / 2
    xs = FIN_SPAR_CHORD_FRACTION * model.params["tail"]["chord_mm"]
    body = box(-17, 17, -(rt + 4), rt + 4, -(rt + 4), rt + TAIL_MOUNT_TOP_MM)
    cuts = [
        _bore(rt, 40),
        cylinder_between(
            (0, 0, rt + 1.5), (0, 0, rt + TAIL_MOUNT_TOP_MM + 1), model.tail_spar_od + 0.3
        ),
        cylinder_between((-12, 0, -rt - 10), (-12, 0, rt + 10), 3.2),
        cylinder_between((12, 0, -rt - 10), (12, 0, rt + 10), 3.2),
    ]
    del xs
    return one_solid(body.cut(*cuts), "tail mount")


def _apex_sections(model: CadModel) -> list[np.ndarray]:
    """Left root, centre and right root sections of the V-tail apex (ruled between them)."""
    r = model.surfaces["tail_right"]
    left = model.surfaces["tail_left"]
    s_root = r.s0
    loop = r.te_profile().loop()
    c = r.chord(s_root)
    mid_origin = r.origin + np.array([0.0, 0.0, s_root * float(r.span_axis[2])])
    mid = (
        mid_origin
        + np.outer((loop[:, 0] - 0.25) * c + 0.25 * c, r.xhat)
        + np.outer(loop[:, 1] * c, np.array([0.0, 0.0, 1.0]))
    )
    return [left.section(s_root), mid, r.section(s_root)]


def v_tail_apex(model: CadModel) -> cq.Solid:
    """Apex block joining the two V-tail panels: a ruled loft from the left panel's root
    section through a centre section to the right panel's root section (so both bonding faces
    match the panel roots exactly), with spar sockets and, under it, the pylon saddle."""
    r = model.surfaces["tail_right"]
    left = model.surfaces["tail_left"]
    s_root = r.s0
    body: cq.Shape = loft([airfoil_wire(sec) for sec in _apex_sections(model)], ruled=True)
    if "tail_pylon" in model.surfaces:
        body = body.fuse(apex_saddle(model))
    cuts = []
    for surf in (r, left):
        cuts.append(
            cylinder_between(
                surf.spar_point_at(2.0), surf.spar_point_at(s_root + 2), surf.spar_od_mm + 0.3
            )
        )
    if "tail_pylon" in model.surfaces:
        py = model.surfaces["tail_pylon"]
        x = py.origin[0] + FIN_SPAR_CHORD_FRACTION * py.chord(py.s0)
        z_top = py.spar_s_end + 1.0
        cuts.append(cylinder_between((x, 0, z_top - 40), (x, 0, z_top), model.tail_spar_od + 0.3))
    return one_solid(body.cut(*cuts), "V-tail apex")


def apex_saddle_z(model: CadModel) -> tuple[float, float]:
    """(pylon top, saddle top): the pylon ends flat 1 mm under the apex; the saddle block
    fills from there to 3 mm inside the apex bottom."""
    py = model.surfaces["tail_pylon"]
    half_t = 0.5 * py.thickness_mm(py.s1, 0.3) + 3.0
    secs = _apex_sections(model)
    pts = []
    for a_, b_ in ((secs[0], secs[1]), (secs[1], secs[2])):
        for t in np.linspace(0, 1, 9):
            pts.append(a_ + (b_ - a_) * t)
    allp = np.vstack(pts)
    near = allp[np.abs(allp[:, 1]) <= half_t]
    z_low = float(near[:, 2].min())
    return z_low - 1.0, z_low + 3.0


def apex_saddle(model: CadModel) -> cq.Solid:
    py = model.surfaces["tail_pylon"]
    c = py.chord(py.s1)
    x0 = py.origin[0] + 0.03 * c
    x1 = py.origin[0] + 0.85 * c
    half_t = 0.5 * py.thickness_mm(py.s1, 0.3)
    z0, z1 = apex_saddle_z(model)
    return box(x0, x1, -half_t, half_t, z0, z1)


def vertical_member(model: CadModel, surf: Surface, sa: float, sb: float) -> cq.Solid:
    """Fin or pylon piece, trimmed where it meets the stabiliser or the V-tail apex."""
    solid = surface_piece(model, surf, sa, sb)
    if sb >= surf.s1 - 1e-6:
        if "tail_hstab" in model.surfaces:
            hs = model.surfaces["tail_hstab"]
            y = float(surf.origin[1])
            secs = [hs.section(y - 30.0), hs.section(y + 30.0)]
            solid = one_solid(
                solid.cut(loft([airfoil_wire(s) for s in secs], ruled=True)), f"{surf.label} top"
            )
        if "tail_right" in model.surfaces:
            z = apex_saddle_z(model)[0]
            solid = one_solid(solid.cut(box(-1e4, 1e4, -1e4, 1e4, z, 1e4)), f"{surf.label} top")
    return solid


# ---------------------------------------------------------------------------
# Part list
# ---------------------------------------------------------------------------


def _place(rot: Any, trans: Any) -> tuple[np.ndarray, np.ndarray]:
    return np.asarray(rot, float), np.asarray(trans, float)


I3 = np.eye(3)
ROT_Z180 = np.diag([-1.0, -1.0, 1.0])


def part_specs(model: CadModel) -> list[PartSpec]:
    p = model.params
    g = model.geometry
    bm = model.boom
    yo, zb = bm["y_mm"], bm["z_mm"]
    layout = p["layout"]
    specs: list[PartSpec] = []
    for side, short in (("right", "Wing R"), ("left", "Wing L")):
        specs.append(
            PartSpec(
                key=f"wing_{side}",
                label=f"Wing panel, {side}",
                short=short,
                profile="lw_surface",
                split="surface",
                orientation="le_down",
                surface=f"wing_{side}",
                description="Wing panel lofted through the airfoil sections with incidence, "
                "twist, dihedral and sweep; spar channel at 25 % chord, boom bore, aileron "
                "hinge line groove on the upper surface, incidence-pin hole at the root.",
            )
        )
    specs.append(
        PartSpec(
            key="fuselage",
            label="Fuselage shell",
            short="Fuselage",
            profile="lw_shell",
            split="fuselage",
            orientation="upright_x",
            build=lambda: build_fuselage(model),
            description="0.8 mm shell (two perimeters of LW-PLA) from the nose-bay flange to the "
            "tail, with the nose-bay mating flange, spar and incidence-pin holes and the tail "
            "boom socket.",
        )
    )
    specs.append(
        PartSpec(
            key="nose_bay",
            label="Nose camera bay",
            short="Nose bay",
            profile="lw_shell",
            split="fuselage",
            orientation="upright_x",
            build=lambda: build_nose_bay(model),
            description="Swappable nose module: camera window in the lower skin, four M3 "
            "heat-set bosses on the payload deck and the rear mating flange (4 x M3).",
        )
    )
    specs.append(
        PartSpec(
            key="wing_clamp",
            label="Wing-to-fuselage clamp",
            short="Wing clamp",
            profile="pacf_mount",
            split="none",
            orientation="flat_face",
            build=lambda: wing_clamp(model),
            description="Block inside the fuselage holding both spar tubes at the dihedral "
            "angle; one M3 cross bolt through each spar.",
        )
    )
    for side, sgn in (("right", 1.0), ("left", -1.0)):
        specs.append(
            PartSpec(
                key=f"boom_clamp_{side}",
                label=f"Boom-to-wing clamp, {side}",
                short=f"Boom clamp {'R' if sgn > 0 else 'L'}",
                profile="petg_light",
                split="fixed",
                orientation="flat_face",
                build_fixed=lambda side=side: boom_clamp_halves(model, side),
                description="Two-piece cuff around the boom and the front half of the wing "
                "section (over the spar), bolted with 4 x M3: two ahead of the leading edge, two "
                "through the wing.",
            )
        )
    xf, xr = g["front_rotor_x_mm"], g["rear_rotor_x_mm"]
    tilt_x = g.get("tilt_hinge_x_mm")
    motor = model.lift_motor

    def mount_instances(x: float) -> list[tuple[np.ndarray, np.ndarray]]:
        return [_place(I3, (x, yo, zb)), _place(I3, (x, -yo, zb))]

    fixed_xs = []
    if layout == "front_tilt":
        fixed_xs = [("rear", xr)]
    elif layout == "rear_tilt":
        fixed_xs = [("front", xf)]
    else:
        fixed_xs = [("front", xf), ("rear", xr)]
    for name, x in fixed_xs:
        specs.append(
            PartSpec(
                key=f"motor_mount_{name}",
                label=f"Motor mount, {name}",
                short=f"Motor mount {name}",
                profile="petg_mount",
                split="none",
                orientation="flat_face",
                quantity=2,
                build=lambda: fixed_motor_mount(model, motor),
                instances=mount_instances(x),
                description=f"Clamp on the boom with the motor plate ({motor['mount_pattern']} "
                "pattern, 10 mm centre hole) at the motor height.",
            )
        )
    if layout != "quad_pusher" and tilt_x is not None:
        zp = tilt_pin_height(model)
        specs.append(
            PartSpec(
                key="tilt_hinge",
                label="Tilt hinge block",
                short="Tilt hinge",
                profile="pacf_mount",
                split="none",
                orientation="flat_face",
                quantity=2,
                build=lambda: tilt_hinge_block(model),
                instances=mount_instances(tilt_x),
                description="Boom clamp with hinge cheeks, hinge-pin bore, servo pocket sized "
                f"to the tilt servo and stops at 0 and {p['tilt']['max_angle_deg']:g} degrees.",
            )
        )
        specs.append(
            PartSpec(
                key="tilt_motor_mount",
                label="Tilting motor mount",
                short="Tilt motor mount",
                profile="pacf_mount",
                split="none",
                orientation="flat_face",
                quantity=2,
                build=lambda: tilt_motor_mount(model),
                instances=[
                    _place(I3, (tilt_x, yo, zb + zp)),
                    _place(I3, (tilt_x, -yo, zb + zp)),
                ],
                description="Motor plate on an arm that pivots on the hinge pin; tab for the "
                "servo pushrod (M2 link).",
            )
        )
    if layout == "quad_pusher":
        specs.append(
            PartSpec(
                key="pusher_mount",
                label="Pusher motor mount",
                short="Pusher mount",
                profile="asa_mount",
                split="none",
                orientation="flat_face",
                build=lambda: build_pusher_mount(model),
                description="Firewall plate bonded inside the fuselage tail with the pusher "
                "motor's bolt pattern.",
            )
        )
    # Tail surfaces
    tl = model.tail_layout
    for key, surf in model.surfaces.items():
        if not key.startswith("tail_"):
            continue
        specs.append(
            PartSpec(
                key=key,
                label={
                    "tail_hstab": "Horizontal stabiliser",
                    "tail_fin": "Fin",
                    "tail_pylon": "Tail pylon",
                    "tail_right": "V-tail panel, right",
                    "tail_left": "V-tail panel, left",
                    "tail_fin_right": "Fin, right",
                    "tail_fin_left": "Fin, left",
                }.get(key, surf.label),
                short=surf.label,
                profile="lw_surface",
                split="surface",
                orientation="le_down",
                surface=key,
                description="Tail surface with a carbon spar channel "
                f"({model.tail_spar_od:g} mm tube).",
            )
        )
    if "tail_right" in model.surfaces:
        specs.append(
            PartSpec(
                key="tail_apex",
                label="V-tail apex block",
                short="V-tail apex",
                profile="petg_mount",
                split="none",
                orientation="flat_face",
                build=lambda: v_tail_apex(model),
                description="Joins the two V-tail panels at the root; spar sockets for both "
                "panels and the pylon.",
            )
        )
    verticals = tl.get("vertical", [])
    if verticals:
        if p["tail"]["type"] == "twin_boom_h":
            tube_d = bm["diameter_mm"]
            inst = []
            for name in verticals:
                s = model.surfaces[name]
                x = s.origin[0] + FIN_SPAR_CHORD_FRACTION * s.chord(s.s0)
                inst.append(_place(I3, (x, s.origin[1], zb)))
        elif model.tail_boom is not None:
            tube_d = model.tail_boom["diameter_mm"]
            s = model.surfaces[verticals[0]]
            x = s.origin[0] + FIN_SPAR_CHORD_FRACTION * s.chord(s.s0)
            inst = [_place(I3, (x, 0.0, model.tail_boom["z_mm"]))]
        else:
            inst = []
        if inst:
            specs.append(
                PartSpec(
                    key="tail_mount",
                    label="Tail mount",
                    short="Tail mount",
                    profile="petg_mount",
                    split="none",
                    orientation="flat_face",
                    quantity=len(inst),
                    build=lambda tube_d=tube_d: tail_mount(model, tube_d),
                    instances=inst,
                    description="Clamp on the tail tube with the socket for the fin or pylon "
                    "spar; two M3 clamp bolts.",
                )
            )
    gx = gear_positions(model)
    if gx:
        inst = [_place(I3, (x, sy * yo, zb)) for x in gx for sy in (1, -1)]
        specs.append(
            PartSpec(
                key="landing_gear",
                label="Landing gear leg"
                if p["landing_gear"]["type"] == "legs"
                else "Landing skid leg",
                short="Gear leg",
                profile="petg_mount",
                split="none",
                orientation="flat_face",
                quantity=len(inst),
                build=lambda: gear_leg(model),
                instances=inst,
                description="Leg clamped on the boom"
                + (
                    f" with a socket for the {SKID_TUBE_MM:g} mm carbon skid tube."
                    if p["landing_gear"]["type"] == "skids"
                    else " with a foot pad (add a rubber foot)."
                ),
            )
        )
    return specs


# ---------------------------------------------------------------------------
# Purchased items for the assembly
# ---------------------------------------------------------------------------


def tubes(model: CadModel) -> list[dict[str, Any]]:
    """Carbon tubes and rods with their cut lengths and placement."""
    out: list[dict[str, Any]] = []
    sp = model.spar
    for key in ("wing_right", "wing_left"):
        s = model.surfaces[key]
        a = s.spar_point_at(5.0)
        b = s.spar_point_at(s.spar_s_end - 1.0)
        ends = [a, b]
        if sp.get("splice_y_mm"):
            ends = [a, s.spar_point_at(sp["splice_y_mm"]), b]
        for i in range(len(ends) - 1):
            out.append(
                {
                    "key": f"spar_{key[5:]}" + (f"_{i + 1}" if len(ends) > 2 else ""),
                    "label": f"Wing spar tube ({key[5:]})",
                    "od_mm": sp["outer_mm"],
                    "wall_mm": sp["wall_mm"],
                    "p0": ends[i],
                    "p1": ends[i + 1],
                }
            )
    bm = model.boom
    for sgn, side in ((1, "right"), (-1, "left")):
        y = sgn * bm["y_mm"]
        out.append(
            {
                "key": f"boom_{side}",
                "label": f"Boom ({side})",
                "od_mm": bm["diameter_mm"],
                "wall_mm": bm["wall_mm"],
                "p0": np.array([bm["x0_mm"], y, bm["z_mm"]]),
                "p1": np.array([bm["x0_mm"] + bm["length_mm"], y, bm["z_mm"]]),
            }
        )
    tb = model.tail_boom
    if tb is not None:
        out.append(
            {
                "key": "tail_boom",
                "label": "Tail boom",
                "od_mm": tb["diameter_mm"],
                "wall_mm": 1.0,
                "p0": np.array([tb["x0_mm"], 0.0, tb["z_mm"]]),
                "p1": np.array([tb["x1_mm"], 0.0, tb["z_mm"]]),
            }
        )
    for key, s in model.surfaces.items():
        if key.startswith("tail_") and s.spar_line() is not None:
            a, b = s.spar_line()
            out.append(
                {
                    "key": f"spar_{key}",
                    "label": f"Spar tube ({s.label})",
                    "od_mm": s.spar_od_mm,
                    "wall_mm": 1.0 if s.spar_od_mm >= 6 else 0.0,
                    "p0": a,
                    "p1": b,
                }
            )
    gx = gear_positions(model)
    if gx and model.params["landing_gear"]["type"] == "skids":
        rb = bm["diameter_mm"] / 2
        length = max(bm["z_mm"] - model.geometry["landing_gear_bottom_z_mm"], rb + 20.0)
        z = bm["z_mm"] - length + SKID_TUBE_MM / 2 + 0.15 + 2.5
        for sgn, side in ((1, "right"), (-1, "left")):
            y = sgn * bm["y_mm"]
            out.append(
                {
                    "key": f"skid_{side}",
                    "label": f"Skid tube ({side})",
                    "od_mm": SKID_TUBE_MM,
                    "wall_mm": 1.0,
                    "p0": np.array([gx[0] - 60.0, y, z]),
                    "p1": np.array([gx[1] + 60.0, y, z]),
                }
            )
    for t in out:
        t["length_mm"] = float(np.linalg.norm(np.asarray(t["p1"]) - np.asarray(t["p0"])))
    return out


def tube_solid(t: dict[str, Any]) -> cq.Shape:
    outer = cylinder_between(t["p0"], t["p1"], t["od_mm"])
    if t.get("wall_mm", 0) > 0 and t["od_mm"] - 2 * t["wall_mm"] > 0.5:
        a, b = np.asarray(t["p0"], float), np.asarray(t["p1"], float)
        d = (b - a) / np.linalg.norm(b - a)
        outer = outer.cut(cylinder_between(a - d, b + d, t["od_mm"] - 2 * t["wall_mm"]))
    return outer


def assembly_extras(model: CadModel) -> list[tuple[str, cq.Shape, str]]:
    """(name, solid, colour) for motors, propellers, battery and payload (simple volumes)."""
    out: list[tuple[str, cq.Shape, str]] = []
    g = model.geometry
    bm = model.boom
    zb = bm["z_mm"]
    motor = model.lift_motor
    tilt_layout = model.layout != "quad_pusher"
    for rot in g["rotors"]:
        if rot["id"] == "pusher":
            continue
        x, y = rot["position"][0], rot["position"][1]
        tilts = rot["tilts"] and tilt_layout
        x_m = g["tilt_hinge_x_mm"] if tilts else x
        z0 = zb + motor_plate_height(model, tilts)
        out.append(
            (
                f"motor_{rot['id']}",
                cylinder_between(
                    (x_m, y, z0), (x_m, y, z0 + motor["height_mm"]), motor["diameter_mm"]
                ),
                "#202020",
            )
        )
        zprop = z0 + motor["height_mm"] + 8
        out.append(
            (
                f"prop_disc_{rot['id']}",
                cylinder_between((x_m, y, zprop), (x_m, y, zprop + 2), rot["diameter_mm"]),
                "#9AA7B5",
            )
        )
    if model.pusher_motor is not None:
        pm = model.pusher_motor
        x = min(model.params["pusher"]["x_mm"], model.fuselage.length - 6.0)
        out.append(
            (
                "motor_pusher",
                cylinder_between((x, 0, 0), (x + pm["height_mm"], 0, 0), pm["diameter_mm"]),
                "#202020",
            )
        )
        d = model.params["pusher"]["prop_diameter_mm"]
        xp = x + pm["height_mm"] + 8
        out.append(("prop_disc_pusher", cylinder_between((xp, 0, 0), (xp + 2, 0, 0), d), "#9AA7B5"))
    bb = model.battery_box
    c = bb["center"]
    out.append(
        (
            "battery",
            box(
                c[0] - bb["length_mm"] / 2,
                c[0] + bb["length_mm"] / 2,
                -bb["width_mm"] / 2,
                bb["width_mm"] / 2,
                c[2] - bb["height_mm"] / 2,
                c[2] + bb["height_mm"] / 2,
            ),
            "#E0B000",
        )
    )
    pb = model.payload_box
    c = pb["center"]
    out.append(
        (
            "payload",
            box(
                c[0] - pb["length_mm"] / 2,
                c[0] + pb["length_mm"] / 2,
                -pb["width_mm"] / 2,
                pb["width_mm"] / 2,
                c[2] - pb["height_mm"] / 2,
                c[2] + pb["height_mm"] / 2,
            ),
            "#7A4FB0",
        )
    )
    return out
