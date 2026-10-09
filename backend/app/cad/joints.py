"""Joint features between split pieces.

Every joint gets

* two tapered alignment pins on one face and matching tapered sockets in the other face, with
  0.2 mm radial clearance (``KEY_CLEARANCE_MM``);
* a bonding surface: a flat face with glue grooves (lifting surfaces), or a 7 mm frame ring on
  each side of a fuselage joint with a glue groove in one ring;
* where a spar passes, the spar channel runs straight through the joint (it is cut from every
  piece with one cutter, see ``parts.surface_piece``); outboard of the spar tube end a short
  carbon joiner rod channel (30 mm each side) carries the joint instead.
"""

from __future__ import annotations

import itertools
from typing import Any

import cadquery as cq
import numpy as np

from app.cad.model import (
    KEY_CLEARANCE_MM,
    SPAR_CHORD_FRACTION,
    SPAR_CLEARANCE_MM,
    CadModel,
    FuselageModel,
    Surface,
    _rod_at_most,
)
from app.cad.parts import cone_between, cylinder_between, one_solid, oriented_box, prism_x

KEY_LENGTH_MM = 8.0
KEY_TAPER = 0.7  # tip diameter / base diameter
GROOVE_WIDTH_MM = 1.2
GROOVE_DEPTH_MM = 0.6
FRAME_RING_MM = 7.0
FRAME_THICKNESS_MM = 3.0
JOINER_HALF_LENGTH_MM = 30.0


def socket_diameters(d: float, length: float) -> tuple[float, float]:
    """Socket diameters at 0.5 mm before the face and 0.5 mm past the pin tip.

    The pin runs from 1 mm inside its own piece (diameter d) to ``length`` past the face
    (diameter ``KEY_TAPER * d``); the socket follows the same taper, 0.2 mm larger in radius."""
    slope = (d * KEY_TAPER - d) / (length + 1.0)
    c = 2 * KEY_CLEARANCE_MM
    return d + slope * 0.5 + c, d + slope * (length + 1.5) + c


def _surface_key_positions(surf: Surface, s: float) -> tuple[list[float], float]:
    """Chord fractions of the two keys and the pin base diameter at station s."""
    spar_xc = surf.spar_xc
    front_c = [0.10, 0.12, 0.14, 0.16]
    rear_c = [0.60, 0.56, 0.52, 0.64, 0.48]
    best: tuple[list[float], float] | None = None
    for xf in front_c:
        for xr in rear_c:
            t = min(surf.thickness_mm(s, xf), surf.thickness_mm(s, xr))
            d = float(np.clip(0.4 * t, 3.0, 6.0))
            c = surf.chord(s)
            clear = 0.5 * (surf.spar_od_mm + SPAR_CLEARANCE_MM) + d / 2 + 1.5
            if abs(xf - spar_xc) * c < clear or abs(xr - spar_xc) * c < clear:
                continue
            if t < d + 2 * 1.0:
                continue
            if best is None or d > best[1]:
                best = ([xf, xr], d)
    if best is None:
        t = surf.thickness_mm(s, 0.3)
        return [0.12, 0.6], float(max(2.0, 0.35 * t))
    return best


def surface_key_tips(surf: Surface, s: float) -> list[np.ndarray]:
    """Points of the pin tips protruding past station s (for envelope planning)."""
    xcs, _ = _surface_key_positions(surf, s)
    a = surf.axis_world()
    return [surf.mid_point(s, xc) + a * KEY_LENGTH_MM for xc in xcs]


def surface_joint(
    surf: Surface, s: float, inboard: cq.Shape, outboard: cq.Shape
) -> tuple[cq.Solid, cq.Solid, dict[str, Any]]:
    a = surf.axis_world()
    xcs, d = _surface_key_positions(surf, s)
    pins, sockets = [], []
    keys = []
    for xc in xcs:
        p = surf.mid_point(s, xc)
        pins.append(cone_between(p - a * 1.0, p + a * KEY_LENGTH_MM, d, d * KEY_TAPER))
        d0, d1 = socket_diameters(d, KEY_LENGTH_MM)
        sockets.append(cone_between(p - a * 0.5, p + a * (KEY_LENGTH_MM + 0.5), d0, d1))
        keys.append(
            {
                "position_mm": [round(float(v), 2) for v in p],
                "chord_fraction": xc,
                "base_diameter_mm": round(d, 2),
                "tip_diameter_mm": round(d * KEY_TAPER, 2),
                "length_mm": KEY_LENGTH_MM,
                "clearance_mm": KEY_CLEARANCE_MM,
            }
        )
    # Glue grooves along the mid-thickness line, between and behind the keys and the spar.
    x2, n2 = surf.axes(s)
    if surf.mirror_y:
        x2 = x2 * np.array([1, -1, 1])
        n2 = n2 * np.array([1, -1, 1])
    c = surf.chord(s)
    spar_xc = surf.spar_xc
    gap_spar = (0.5 * (surf.spar_od_mm + SPAR_CLEARANCE_MM) + 2.0) / c
    gap_key = (d / 2 + 2.0) / c
    marks = sorted([(xcs[0], gap_key), (spar_xc, gap_spar), (xcs[1], gap_key)])
    spans = []
    for (xa, ga), (xb, gb) in itertools.pairwise(marks):
        spans.append((xa + ga, xb - gb))
    spans.append((marks[-1][0] + marks[-1][1], 0.85))
    grooves = []
    for g0, g1 in spans:
        if (g1 - g0) * c < 6.0:
            continue
        if min(surf.thickness_mm(s, g0), surf.thickness_mm(s, g1)) < 4.0:
            continue
        p0, p1 = surf.mid_point(s, g0), surf.mid_point(s, g1)
        ex = (p1 - p0) / np.linalg.norm(p1 - p0)
        ez = a
        ey = np.cross(ez, ex)
        centre = 0.5 * (p0 + p1) + a * (GROOVE_DEPTH_MM / 2 - 0.05)
        grooves.append(
            oriented_box(
                centre,
                ex,
                ey,
                ez,
                (float(np.linalg.norm(p1 - p0)), GROOVE_WIDTH_MM, GROOVE_DEPTH_MM + 0.1),
            )
        )
    # Spar continuity, or a joiner rod outboard of the spar end.
    channel: dict[str, Any]
    if surf.spar_od_mm > 0 and surf.spar_s_start <= s <= surf.spar_s_end - 5:
        channel = {
            "kind": "spar tube",
            "diameter_mm": surf.spar_od_mm,
            "channel_mm": surf.spar_od_mm + SPAR_CLEARANCE_MM,
            "continuous": True,
        }
        joiner = None
    else:
        t = surf.thickness_mm(s, SPAR_CHORD_FRACTION)
        rod = _rod_at_most(0.45 * t)
        mp = surf.mid_point(s, SPAR_CHORD_FRACTION)
        joiner = cylinder_between(
            mp - a * JOINER_HALF_LENGTH_MM, mp + a * JOINER_HALF_LENGTH_MM, rod + SPAR_CLEARANCE_MM
        )
        channel = {
            "kind": "carbon joiner rod",
            "diameter_mm": rod,
            "length_mm": 2 * JOINER_HALF_LENGTH_MM,
            "channel_mm": rod + SPAR_CLEARANCE_MM,
            "continuous": True,
        }
    inb = inboard.fuse(*pins)
    outb = outboard.cut(*sockets, *grooves)
    if joiner is not None:
        inb = inb.cut(joiner)
        outb = outb.cut(joiner)
    record = {
        "kind": "surface",
        "station_mm": round(float(s), 2),
        "keys": keys,
        "bonding": {
            "kind": "flat face with glue grooves",
            "grooves": len(grooves),
            "groove_mm": [GROOVE_WIDTH_MM, GROOVE_DEPTH_MM],
        },
        "channel": channel,
    }
    return one_solid(inb, f"{surf.label} joint"), one_solid(outb, f"{surf.label} joint"), record


def fuselage_key_points(fm: FuselageModel, x: float) -> list[tuple[float, float]]:
    a, _ = fm.half_size(x, fm.wall + 4.2)
    return [(a, 0.0), (-a, 0.0)]


def fuselage_joint(
    model: CadModel, fm: FuselageModel, hull: cq.Shape, x: float, fwd: cq.Shape, aft: cq.Shape
) -> tuple[cq.Solid, cq.Solid, dict[str, Any]]:
    w = fm.wall
    inner = fm.half_size(x, w - 0.01)
    ring_in = fm.half_size(x, w + FRAME_RING_MM)
    t = FRAME_THICKNESS_MM
    ring_f = prism_x(fm, x - t, x, inner).cut(prism_x(fm, x - t - 1, x + 1, ring_in))
    ring_a = prism_x(fm, x, x + t, inner).cut(prism_x(fm, x - 1, x + t + 1, ring_in))
    d = 3.5
    pins, bosses, sockets, keys = [], [], [], []
    for y, z in fuselage_key_points(fm, x):
        pins.append(cone_between((x - 1.0, y, z), (x + 6.0, y, z), d, d * KEY_TAPER))
        bosses.append(cylinder_between((x, y, z), (x + 8.0, y, z), 8.0))
        d0, d1 = socket_diameters(d, 6.0)
        sockets.append(cone_between((x - 0.5, y, z), (x + 6.5, y, z), d0, d1))
        keys.append(
            {
                "position_mm": [round(x, 2), round(y, 2), round(z, 2)],
                "base_diameter_mm": d,
                "tip_diameter_mm": round(d * KEY_TAPER, 2),
                "length_mm": 6.0,
                "clearance_mm": KEY_CLEARANCE_MM,
            }
        )
    g_out = fm.half_size(x, w + 0.6)
    g_in = fm.half_size(x, w + 1.6)
    groove = prism_x(fm, x - 0.1, x + GROOVE_DEPTH_MM, g_out).cut(prism_x(fm, x - 1, x + 2, g_in))
    slab_f = hull.intersect(_slab(x - 50, x))
    slab_a = hull.intersect(_slab(x, x + 50))
    a_add = ring_a.fuse(*bosses).intersect(slab_a)
    f_new = fwd.fuse(ring_f.intersect(slab_f), *pins)
    a_new = aft.fuse(a_add).cut(*sockets, groove)
    record = {
        "kind": "fuselage",
        "station_mm": round(float(x), 2),
        "keys": keys,
        "bonding": {
            "kind": "flat face: 7 mm frame ring on each side with a glue groove",
            "grooves": 1,
            "groove_mm": [1.0, GROOVE_DEPTH_MM],
        },
        "channel": None,
    }
    return one_solid(f_new, "fuselage joint"), one_solid(a_new, "fuselage joint"), record


def _slab(x0: float, x1: float) -> cq.Solid:
    big = 1e4
    return cq.Solid.makeBox(x1 - x0, 2 * big, 2 * big, cq.Vector(x0, -big, -big))
