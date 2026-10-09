"""Dimensioned 2D drawings (PDF, ReportLab) from the Python geometry.

Sheets (A3 landscape, millimetres, title block on every sheet):

1. General arrangement: top, front and side views with overall dimensions.
2. Wing planform: chords at the root, fuselage side, boom, MAC, aileron ends, split joints and
   tip; span, MAC position, quarter-chord and spar lines.
3. Tail, booms and motor stations, with the CG and neutral-point marks of the latest analysis.
4. Print pieces: split stations and every piece with its size, fit check and filament.

The canvas is written in ReportLab's invariant mode so the same input gives the same file.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from app.cad.model import CadModel

DRAWING_PAGES = 4
STANDARD_SCALES = [1, 2, 2.5, 5, 10, 20, 25, 50]
PAGE_W, PAGE_H = landscape(A3)
INK = (0.1, 0.12, 0.16)
DIM = (0.15, 0.35, 0.65)
MARK = (0.75, 0.15, 0.1)


class Sheet:
    """Maps model millimetres to the page at a fixed scale inside a view box."""

    def __init__(self, c: Canvas, origin_pt: tuple[float, float], scale: float):
        self.c = c
        self.ox, self.oy = origin_pt
        self.k = mm / scale

    def p(self, u: float, v: float) -> tuple[float, float]:
        return self.ox + u * self.k, self.oy + v * self.k

    def poly(self, pts: Any, close: bool = True, width: float = 0.6, color=INK, fill=None):
        pts = [self.p(float(u), float(v)) for u, v in pts]
        if len(pts) < 2:
            return
        path = self.c.beginPath()
        path.moveTo(*pts[0])
        for q in pts[1:]:
            path.lineTo(*q)
        if close:
            path.close()
        self.c.setLineWidth(width)
        self.c.setStrokeColorRGB(*color)
        if fill is not None:
            self.c.setFillColorRGB(*fill)
        self.c.drawPath(path, stroke=1, fill=1 if fill is not None else 0)

    def line(self, a: Any, b: Any, width: float = 0.5, color=INK, dash: Any = None):
        self.c.setLineWidth(width)
        self.c.setStrokeColorRGB(*color)
        if dash:
            self.c.setDash(list(dash), 0)
        self.c.line(*self.p(*a), *self.p(*b))
        if dash:
            self.c.setDash()

    def circle(self, centre: Any, r: float, width: float = 0.5, color=INK):
        self.c.setLineWidth(width)
        self.c.setStrokeColorRGB(*color)
        x, y = self.p(*centre)
        self.c.circle(x, y, r * self.k, stroke=1, fill=0)

    def text(self, at: Any, s: str, size: float = 7, color=INK, anchor: str = "start", angle=0.0):
        x, y = self.p(*at)
        self.c.setFillColorRGB(*color)
        self.c.setFont("Helvetica", size)
        self.c.saveState()
        self.c.translate(x, y)
        self.c.rotate(angle)
        if anchor == "middle":
            self.c.drawCentredString(0, 0, s)
        elif anchor == "end":
            self.c.drawRightString(0, 0, s)
        else:
            self.c.drawString(0, 0, s)
        self.c.restoreState()

    def dim(self, a: Any, b: Any, offset: float, label: str | None = None, size: float = 7):
        """Aligned dimension between model points a and b, offset (model mm) to the left of
        a->b, with extension lines, arrow ticks and the measured length."""
        a, b = np.asarray(a, float), np.asarray(b, float)
        d = b - a
        length = float(np.linalg.norm(d))
        if length < 1e-6:
            return
        t = d / length
        n = np.array([-t[1], t[0]])
        a2, b2 = a + n * offset, b + n * offset
        ext = n * (2.0 * self.scale_mm() * (1 if offset >= 0 else -1))
        self.line(a, a2 + ext, 0.25, DIM)
        self.line(b, b2 + ext, 0.25, DIM)
        self.line(a2, b2, 0.35, DIM)
        tick = 1.5 * self.scale_mm()
        for q, sgn in ((a2, 1), (b2, -1)):
            self.line(q, q + (t * sgn + n * 0.35) * tick, 0.35, DIM)
            self.line(q, q + (t * sgn - n * 0.35) * tick, 0.35, DIM)
        mid = 0.5 * (a2 + b2) + n * 1.2 * self.scale_mm()
        ang = math.degrees(math.atan2(t[1], t[0]))
        if ang > 90.5 or ang < -89.5:
            ang += 180
        self.text(mid, label or f"{length:.0f}", size, DIM, "middle", ang)

    def scale_mm(self) -> float:
        """Model millimetres per paper millimetre."""
        return mm / self.k


def pick_scale(extent_mm: tuple[float, float], box_mm: tuple[float, float]) -> float:
    for s in STANDARD_SCALES:
        if extent_mm[0] / s <= box_mm[0] and extent_mm[1] / s <= box_mm[1]:
            return s
    return STANDARD_SCALES[-1]


def title_block(c: Canvas, sheet_no: int, title: str, info: dict[str, str], scale: float) -> None:
    w, h = 180 * mm, 32 * mm
    x0, y0 = PAGE_W - w - 10 * mm, 10 * mm
    c.setStrokeColorRGB(*INK)
    c.setLineWidth(0.8)
    c.rect(10 * mm, 10 * mm, PAGE_W - 20 * mm, PAGE_H - 20 * mm)
    c.rect(x0, y0, w, h)
    c.line(x0, y0 + 20 * mm, x0 + w, y0 + 20 * mm)
    c.line(x0 + 110 * mm, y0, x0 + 110 * mm, y0 + 20 * mm)
    c.setFillColorRGB(*INK)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x0 + 3 * mm, y0 + 24 * mm, title)
    c.setFont("Helvetica", 7.5)
    rows = [
        f"Project: {info.get('project', '-')}",
        f"Version: {info.get('version', '-')}",
        f"Date: {info.get('date', '-')}",
    ]
    for i, r in enumerate(rows):
        c.drawString(x0 + 3 * mm, y0 + (14 - 6 * i) * mm, r)
    scale_txt = f"1:{scale:g}" if scale != 1 else "1:1"
    rows2 = [
        f"Scale: {scale_txt} (A3)",
        "Units: mm, degrees",
        f"Sheet {sheet_no} of {DRAWING_PAGES}",
    ]
    for i, r in enumerate(rows2):
        c.drawString(x0 + 113 * mm, y0 + (14 - 6 * i) * mm, r)


# ---------------------------------------------------------------------------
# Outlines
# ---------------------------------------------------------------------------


def _wing_planform(model: CadModel, sign: float) -> list[tuple[float, float]]:
    w = model.params["wing"]
    semi = w["span_mm"] / 2
    sw = math.tan(math.radians(w["sweep_deg"]))
    ys = [0.0, semi]
    le = [(w["x_le_mm"] + y * sw, sign * y) for y in ys]
    te = [
        (
            w["x_le_mm"]
            + y * sw
            + w["root_chord_mm"]
            - (w["root_chord_mm"] - w["tip_chord_mm"]) * y / semi,
            sign * y,
        )
        for y in ys
    ]
    return le + te[::-1]


def _surface_outline_top(surf: Any) -> list[tuple[float, float]]:
    """Planform (x, y) of a surface: leading and trailing edges at both ends."""
    pts = []
    for s in (surf.s0, surf.s1):
        sec = surf.section(s)
        pts.append(sec[np.argmin(sec[:, 0])])
    for s in (surf.s1, surf.s0):
        sec = surf.section(s)
        pts.append(sec[np.argmax(sec[:, 0])])
    return [(float(p[0]), float(p[1])) for p in pts]


def _surface_outline_front(surf: Any) -> list[tuple[float, float]]:
    up, lo = [], []
    for s in np.linspace(surf.s0, surf.s1, 12):
        sec = surf.section(float(s))
        k = np.argmax(sec[:, 2])
        j = np.argmin(sec[:, 2])
        up.append((float(sec[k, 1]), float(sec[k, 2])))
        lo.append((float(sec[j, 1]), float(sec[j, 2])))
    return up + lo[::-1]


def _surface_outline_side(surf: Any) -> list[tuple[float, float]]:
    pts = np.vstack([surf.section(s) for s in (surf.s0, surf.s1)])
    from scipy.spatial import ConvexHull

    xz = pts[:, [0, 2]]
    try:
        h = ConvexHull(xz)
        return [tuple(map(float, xz[i])) for i in h.vertices]
    except Exception:
        return [tuple(map(float, q)) for q in xz]


def _fuselage_top(model: CadModel) -> list[tuple[float, float]]:
    fm = model.fuselage
    xs = np.linspace(0, fm.length, 80)
    up = [(float(x), fm.half_size(float(x))[0]) for x in xs]
    return up + [(x, -y) for x, y in up[::-1]]


def _fuselage_side(model: CadModel) -> list[tuple[float, float]]:
    fm = model.fuselage
    xs = np.linspace(0, fm.length, 80)
    up = [(float(x), fm.half_size(float(x))[1]) for x in xs]
    return up + [(x, -z) for x, z in up[::-1]]


# ---------------------------------------------------------------------------
# Sheets
# ---------------------------------------------------------------------------


def _sheet_ga(c: Canvas, model: CadModel, info: dict[str, str]) -> None:
    g = model.geometry
    p = model.params
    w = p["wing"]
    semi = w["span_mm"] / 2
    fm = model.fuselage
    tail_x1 = max(g["tail"]["te_x_mm"], fm.length, model.boom["x0_mm"] + model.boom["length_mm"])
    x_min = min(0.0, model.boom["x0_mm"] - p["propulsion"]["prop_diameter_mm"] / 2)
    length = tail_x1 - x_min
    span = max(w["span_mm"], 2 * model.boom["y_mm"] + p["propulsion"]["prop_diameter_mm"])
    z_top = max(g["tail"]["z_mm"] + 20, model.boom["z_mm"] + 120)
    z_bot = g["landing_gear_bottom_z_mm"]
    height = z_top - z_bot
    # Layout: top view left (length x span), front view right-top, side view right-bottom.
    box_top = (200.0, 235.0)
    scale = pick_scale((length + 60, span + 60), box_top)
    scale = max(scale, pick_scale((span + 60, height + 60), (215.0, 110.0)))
    scale = max(scale, pick_scale((length + 60, height + 60), (215.0, 95.0)))
    # Top view (x right, starboard down)
    top = Sheet(c, (25 * mm - x_min * mm / scale + 15 * mm, PAGE_H / 2 + 10 * mm), scale)
    top.text((x_min, span / 2 + 35 * scale / 2), "TOP VIEW", 9)
    pl = _wing_planform(model, 1.0)
    top.poly([(x, -y) for x, y in pl])
    top.poly([(x, -y) for x, y in _wing_planform(model, -1.0)])
    top.poly(_fuselage_top(model), fill=(0.93, 0.93, 0.9))
    bm = model.boom
    for sgn in (1, -1):
        y = sgn * bm["y_mm"]
        r = bm["diameter_mm"] / 2
        x0, x1 = bm["x0_mm"], bm["x0_mm"] + bm["length_mm"]
        top.poly([(x0, -y - r), (x1, -y - r), (x1, -y + r), (x0, -y + r)], width=0.5)
    for rot in g["rotors"]:
        x, y = rot["position"][0], rot["position"][1]
        if rot["id"] == "pusher":
            top.line((x, -rot["diameter_mm"] / 2), (x, rot["diameter_mm"] / 2), 0.8, MARK)
        else:
            top.circle((x, -y), rot["diameter_mm"] / 2, 0.3, (0.5, 0.55, 0.6))
    for key, surf in model.surfaces.items():
        if key.startswith("tail_"):
            top.poly([(x, -y) for x, y in _surface_outline_top(surf)], width=0.5)
    if model.tail_boom:
        tb = model.tail_boom
        r = tb["diameter_mm"] / 2
        top.poly(
            [(tb["x0_mm"], -r), (tb["x1_mm"], -r), (tb["x1_mm"], r), (tb["x0_mm"], r)], width=0.4
        )
    top.dim((x_min, -semi), (x_min, semi), 25 * scale / 2, f"span {w['span_mm']:.0f}")
    top.dim(
        (0, semi + 10 * scale / 2),
        (fm.length, semi + 10 * scale / 2),
        -1e-3 + 0.0,
        f"fuselage {fm.length:.0f}",
    )
    top.dim(
        (0, -semi - 5 * scale), (tail_x1, -semi - 5 * scale), 0.0, f"overall length {tail_x1:.0f}"
    )
    cg = model.balance.get("cg_x_mm")
    if cg:
        top.line((cg, -60), (cg, 60), 0.9, MARK)
        top.text((cg, 65), "CG", 7, MARK, "middle")
    npx = model.balance.get("np_x_mm")
    if npx:
        top.line((npx, -60), (npx, 60), 0.9, DIM, dash=(3, 2))
        top.text((npx, -75), "NP", 7, DIM, "middle")
    # Front view (y right as seen from the front = port right; we draw starboard right)
    fx0 = PAGE_W - 240 * mm
    front = Sheet(c, (fx0 + 115 * mm, PAGE_H - 85 * mm), scale)
    front.text((-span / 2, z_top + 10 * scale / 2), "FRONT VIEW", 9)
    for surf in model.surfaces.values():
        front.poly(_surface_outline_front(surf), width=0.5)
    sec = model.fuselage.section_points(fm.length / 2)
    front.poly([(float(q[1]), float(q[2])) for q in sec], fill=(0.93, 0.93, 0.9))
    for sgn in (1, -1):
        front.circle((sgn * bm["y_mm"], bm["z_mm"]), bm["diameter_mm"] / 2, 0.5)
        d = p["propulsion"]["prop_diameter_mm"]
        zp = bm["z_mm"] + model.params["motors"]["height_mm"] + 40
        front.line(
            (sgn * bm["y_mm"] - d / 2, zp), (sgn * bm["y_mm"] + d / 2, zp), 0.6, (0.5, 0.55, 0.6)
        )
        if p["landing_gear"]["type"] != "none":
            front.line((sgn * bm["y_mm"], bm["z_mm"]), (sgn * bm["y_mm"], z_bot), 0.8)
    front.dim((-semi, z_bot - 5), (semi, z_bot - 5), -12 * scale / 2, f"span {w['span_mm']:.0f}")
    front.dim(
        (semi + 10, z_bot),
        (semi + 10, z_top - 20),
        -12 * scale / 2,
        f"height {z_top - 20 - z_bot:.0f}",
    )
    front.text(
        (0, z_bot - 30 * scale / 2),
        f"dihedral {w['dihedral_deg']:g}°, boom spacing {2 * bm['y_mm']:.0f}",
        7,
        DIM,
        "middle",
    )
    # Side view
    side = Sheet(c, (fx0 + 15 * mm - x_min * mm / scale, 125 * mm), scale)
    side.text((x_min, z_top + 5 * scale / 2), "SIDE VIEW", 9)
    side.poly(_fuselage_side(model), fill=(0.93, 0.93, 0.9))
    side.poly(_surface_outline_side(model.surfaces["wing_right"]), width=0.5)
    for key, surf in model.surfaces.items():
        if key.startswith("tail_"):
            side.poly(_surface_outline_side(surf), width=0.5)
    r = bm["diameter_mm"] / 2
    x0, x1 = bm["x0_mm"], bm["x0_mm"] + bm["length_mm"]
    side.poly(
        [(x0, bm["z_mm"] - r), (x1, bm["z_mm"] - r), (x1, bm["z_mm"] + r), (x0, bm["z_mm"] + r)],
        width=0.4,
    )
    side.dim((0, z_bot - 5), (tail_x1, z_bot - 5), -12 * scale / 2, f"{tail_x1:.0f}")
    side.dim((-10, -fm.height / 2), (-10, fm.height / 2), 12 * scale / 2, f"{fm.height:.0f}")
    title_block(c, 1, "General arrangement", info, scale)


def _sheet_wing(c: Canvas, model: CadModel, info: dict[str, str], stations: list[float]) -> None:
    g = model.geometry
    w = model.params["wing"]
    gw = g["wing"]
    semi = w["span_mm"] / 2
    sw = math.tan(math.radians(w["sweep_deg"]))
    xr = w["root_chord_mm"]
    ext_x = max(xr, semi * abs(sw) + w["tip_chord_mm"]) + 80
    scale = pick_scale((semi + 80, ext_x + 60), (330.0, 200.0))
    # Planform drawn with y to the right, x downwards (leading edge at the top).
    sh = Sheet(c, (40 * mm, PAGE_H - 50 * mm), scale)

    def P(x: float, y: float) -> tuple[float, float]:
        return (y, -(x - w["x_le_mm"]))

    def chord_y(y: float) -> float:
        return w["root_chord_mm"] - (w["root_chord_mm"] - w["tip_chord_mm"]) * y / semi

    def le(y: float) -> float:
        return w["x_le_mm"] + y * sw

    sh.text(P(w["x_le_mm"] - 40, 0), "WING PLANFORM (right half, seen from above)", 9)
    sh.poly([P(le(0), 0), P(le(semi), semi), P(le(semi) + chord_y(semi), semi), P(le(0) + xr, 0)])
    root_y = model.wing_root_y
    labels = [(0.0, "root (centreline)"), (root_y, "fuselage side")]
    labels += [(model.boom["y_mm"], "boom")]
    surf = model.surfaces["wing_right"]
    if surf.hinge:
        labels += [(surf.hinge[0], "aileron start"), (surf.hinge[1], "aileron end")]
        sh.line(
            P(le(surf.hinge[0]) + 0.75 * chord_y(surf.hinge[0]), surf.hinge[0]),
            P(le(surf.hinge[1]) + 0.75 * chord_y(surf.hinge[1]), surf.hinge[1]),
            0.6,
            MARK,
            dash=(2, 2),
        )
    labels += [(semi, "tip")]
    for y, name in labels:
        c_y = chord_y(y)
        sh.line(P(le(y), y), P(le(y) + c_y, y), 0.4, DIM)
        sh.text(
            P(le(y) + c_y + 8 * scale / 2.5, y),
            f"{name}: y {y:.0f}, chord {c_y:.0f}",
            6.5,
            DIM,
            "start",
            -90,
        )
    for s in stations:
        sh.line(P(le(s) - 8, s), P(le(s) + chord_y(s) + 8, s), 0.9, MARK, dash=(4, 2))
        sh.text(P(le(s) - 12, s), f"joint {s:.0f}", 6.5, MARK, "middle")
    # MAC
    my = gw["mac_y_mm"]
    sh.line(P(gw["mac_x_le_mm"], my), P(gw["mac_x_le_mm"] + gw["mac_mm"], my), 1.4, (0.1, 0.5, 0.2))
    sh.text(
        P(gw["mac_x_le_mm"] + gw["mac_mm"] + 8 * scale / 2.5, my),
        f"MAC {gw['mac_mm']:.1f} at y {my:.1f}, x_LE {gw['mac_x_le_mm']:.1f}",
        6.5,
        (0.1, 0.5, 0.2),
        "start",
        -90,
    )
    # quarter chord and spar
    sh.line(
        P(le(0) + xr / 4, 0), P(le(semi) + chord_y(semi) / 4, semi), 0.4, INK, dash=(6, 2, 1, 2)
    )
    a, b = model.spar["axis_start"], model.spar["axis_end"]
    sh.line(P(a[0], a[1]), P(b[0], b[1]), 1.0, (0.2, 0.2, 0.2))
    sh.text(
        P(b[0], b[1] + 10 * scale / 2.5),
        f"spar tube {model.spar['outer_mm']:g} mm at 25 % chord, to y {model.spar['end_y_mm']:.0f}",
        6.5,
        INK,
        "start",
    )
    sh.dim(P(le(0) - 25, 0), P(le(0) - 25, semi), 0, f"semi-span {semi:.0f}")
    sh.dim(P(le(0), -15), P(le(0) + xr, -15), 0, f"{xr:.0f}")
    sh.dim(
        P(le(semi), semi + 15), P(le(semi) + chord_y(semi), semi + 15), 0, f"{chord_y(semi):.0f}"
    )
    y0 = 70 * mm
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(*INK)
    txt = [
        f"Span {w['span_mm']:.0f} mm, area {gw['area_m2']:.4f} m², aspect ratio "
        f"{gw['aspect_ratio']:.2f}, taper {gw['taper_ratio']:.3f}",
        f"MAC {gw['mac_mm']:.1f} mm at y = {gw['mac_y_mm']:.1f} mm, MAC leading edge x = "
        f"{gw['mac_x_le_mm']:.1f} mm, aerodynamic centre x = {gw['ac_x_mm']:.1f} mm",
        f"Sweep (LE) {w['sweep_deg']:g}°, dihedral {w['dihedral_deg']:g}°, incidence "
        f"{w['incidence_deg']:g}°, twist {w['twist_deg']:g}° (about the quarter chord)",
        f"Airfoil {w['airfoil']}; trailing edge thickened to 0.8 mm for printing; spar channel "
        f"{model.spar['outer_mm'] + 0.3:g} mm",
    ]
    for i, t in enumerate(txt):
        c.drawString(20 * mm, y0 - i * 5 * mm, t)
    title_block(c, 2, "Wing planform", info, scale)


def _sheet_stations(c: Canvas, model: CadModel, info: dict[str, str]) -> None:
    g = model.geometry
    p = model.params
    bm = model.boom
    fm = model.fuselage
    x_min = min(0.0, bm["x0_mm"]) - 40
    x_max = max(g["tail"]["te_x_mm"], fm.length, bm["x0_mm"] + bm["length_mm"]) + 40
    z_top = g["tail"]["z_mm"] + 60
    z_bot = g["landing_gear_bottom_z_mm"] - 60
    scale = pick_scale((x_max - x_min, z_top - z_bot + 200), (360.0, 200.0))
    sh = Sheet(c, (25 * mm - x_min * mm / scale, 150 * mm - z_bot * mm / scale / 3), scale)
    sh.text((x_min, z_top), "SIDE VIEW: BOOM, MOTOR AND TAIL STATIONS (x from the nose tip)", 9)
    sh.poly(_fuselage_side(model), fill=(0.93, 0.93, 0.9))
    sh.poly(_surface_outline_side(model.surfaces["wing_right"]), width=0.4)
    for key, surf in model.surfaces.items():
        if key.startswith("tail_"):
            sh.poly(_surface_outline_side(surf), width=0.5)
    r = bm["diameter_mm"] / 2
    x0, x1 = bm["x0_mm"], bm["x0_mm"] + bm["length_mm"]
    zb = bm["z_mm"]
    sh.poly([(x0, zb - r), (x1, zb - r), (x1, zb + r), (x0, zb + r)], width=0.5)
    base = z_bot + 20
    marks = [
        (0.0, "nose"),
        (x0, "boom front"),
        (x1, "boom end"),
        (g["front_rotor_x_mm"], "front motors"),
        (g["rear_rotor_x_mm"], "rear motors"),
        (g["tail"]["quarter_chord_x_mm"], "tail c/4"),
        (g["wing"]["ac_x_mm"], "wing a.c."),
    ]
    if g.get("tilt_hinge_x_mm") is not None:
        marks.append((g["tilt_hinge_x_mm"], "tilt axis"))
    for i, (x, name) in enumerate(sorted(marks)):
        yy = base - (i % 3) * 14 * scale / 2.5
        sh.line((x, zb), (x, yy), 0.25, DIM, dash=(2, 2))
        sh.text((x, yy - 4 * scale / 2.5), f"{name} {x:.0f}", 6.5, DIM, "middle")
    sh.dim(
        (g["wing"]["ac_x_mm"], z_top - 20),
        (g["tail"]["quarter_chord_x_mm"], z_top - 20),
        0,
        f"tail arm {p['tail']['arm_mm']:.0f}",
    )
    sh.dim(
        (x_max - 20, 0), (x_max - 20, g["tail"]["z_mm"]), 0, f"tail height {g['tail']['z_mm']:.0f}"
    )
    cg = model.balance.get("cg_x_mm")
    npx = model.balance.get("np_x_mm")
    if cg:
        sh.line((cg, -fm.height), (cg, fm.height), 1.2, MARK)
        sh.text((cg, fm.height + 6), f"CG {cg:.0f}", 7.5, MARK, "middle")
    if model.balance.get("cg_min_x_mm"):
        x = model.balance["cg_min_x_mm"]
        sh.line((x, -fm.height * 0.7), (x, fm.height * 0.7), 0.8, MARK, dash=(2, 1))
        sh.text((x, -fm.height * 0.7 - 10), f"CG light {x:.0f}", 6.5, MARK, "middle")
    if npx:
        sh.line((npx, -fm.height), (npx, fm.height), 1.2, DIM, dash=(4, 2))
        sh.text((npx, fm.height + 16), f"NP {npx:.0f}", 7.5, DIM, "middle")
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(*INK)
    lines = [
        f"CG source: {model.balance.get('cg_source')}; neutral point: "
        + ("latest analysis (AVL)" if npx else "not available, run Analyse"),
        f"Tail: {p['tail']['type'].replace('_', ' ')}, span {p['tail']['span_mm']:.0f}, "
        f"chord {p['tail']['chord_mm']:.0f}, "
        f"airfoil {p['tail']['airfoil']}, spar {model.tail_spar_od:g} mm"
        + (f", V angle {p['tail']['v_angle_deg']:g}°" if "v" in model.tail_layout else ""),
        f"Booms: {bm['diameter_mm']:g} mm carbon tube, {bm['length_mm']:.0f} mm long at "
        f"y = ±{bm['y_mm']:.0f}, z = {bm['z_mm']:.1f}",
        f"Motors {model.lift_motor['mount_pattern']} pattern"
        + (" (generic until Phase 4)" if model.lift_motor.get("generic") else ""),
    ]
    for i, t in enumerate(lines):
        c.drawString(20 * mm, 60 * mm - i * 5 * mm, t)
    title_block(c, 3, "Tail, boom and motor stations, CG and NP", info, scale)


def _sheet_pieces(c: Canvas, info: dict[str, str], parts: list[dict[str, Any]], envelope) -> None:
    c.setFillColorRGB(*INK)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(
        20 * mm,
        PAGE_H - 25 * mm,
        f"Print pieces (usable envelope {envelope[0]:g} x {envelope[1]:g} x {envelope[2]:g} mm)",
    )
    c.setFont("Helvetica", 7)
    y = PAGE_H - 33 * mm
    cols = [20, 75, 150, 215, 240, 270, 300]
    head = [
        "Piece",
        "Split stations / joints",
        "Size in print orientation (mm)",
        "Fits",
        "Qty",
        "Filament",
        "Mass g",
    ]
    for x, h in zip(cols, head, strict=True):
        c.drawString(x * mm, y, h)
    y -= 5 * mm
    for part in parts:
        for pc in part["pieces"]:
            if y < 55 * mm:
                break
            size = " x ".join(f"{v:.0f}" for v in pc["size_mm"])
            vals = [
                pc["label"],
                ", ".join(f"{s:.0f}" for s in part.get("split", {}).get("stations_mm", []))[:40],
                size,
                "yes" if pc["fits"] else "NO",
                str(part["quantity"]),
                part["filament"],
                f"{pc['mass_g']:.0f}",
            ]
            for x, v in zip(cols, vals, strict=True):
                c.drawString(x * mm, y, v)
            y -= 4.2 * mm
    title_block(c, 4, "Print pieces and split stations", info, 1)


def write_drawings(
    path: Path,
    model: CadModel,
    parts_manifest: list[dict[str, Any]],
    info: dict[str, str] | None = None,
) -> int:
    info = {
        "project": "VTOL drone",
        "version": "draft",
        "date": date.today().isoformat(),
        **(info or {}),
    }
    c = Canvas(str(path), pagesize=landscape(A3), invariant=1)
    c.setTitle(f"{info['project']} - drawings")
    c.setAuthor("VTOL designer")
    _sheet_ga(c, model, info)
    c.showPage()
    wing_parts = [p for p in parts_manifest if p["key"] == "wing_right"]
    stations = wing_parts[0]["split"]["stations_mm"] if wing_parts else []
    _sheet_wing(c, model, info, stations)
    c.showPage()
    _sheet_stations(c, model, info)
    c.showPage()
    _sheet_pieces(c, info, parts_manifest, model.envelope)
    c.showPage()
    c.save()
    return path.stat().st_size
