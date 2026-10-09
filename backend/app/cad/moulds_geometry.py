"""Outer-surface solids of the moulded carbon parts, and their outward offsets, for the moulds.

The moulds take the part's outer mould line (OML). Each source below gives:

* ``outer(w, lo, hi)``: a closed solid bounded by the OML offset outward by ``w`` mm (``w = 0``
  is the part's outer surface), restricted to the range [lo, hi] along the part's long axis
  (fuselage family) or covering the whole part (fairing), and
* the data the mould builder needs: long axis, pull-direction candidates, net and mould ranges,
  open ends, trim planes and a curvature measure along the long axis for placing tile joints.

Offsets are true normal offsets of the side-view and plan-view profiles (2D offset with round
joins), applied to the exact cross-section shapes of the CAD kernel (``FuselageModel`` sections,
``Surface`` airfoil sections). For the fairing the offset is separable: the spanwise fillet
profile is offset in its own plane and the airfoil section in the chordwise plane; both are
exact where the other curvature is small, and the mould wall thickness is measured afterwards.

Fuselage and nose bay: the same ``FuselageModel`` function as the printed shell
(``app.cad.parts.build_fuselage`` / ``build_nose_bay``), lofted smoothly through dense sections
(the printed shell uses ruled facets between 12 nose stations; a carbon part takes the smooth
surface). The wing-root fairing is a new part (the kernel has none): a concave fillet of radius
R between the fuselage side and the wing root, lofted through the root airfoil offset by the
fillet height at each spanwise station (see :func:`fairing_source`).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import cadquery as cq
import numpy as np

from app.cad import parts as P
from app.cad.model import CadModel, FuselageModel, Surface

#: Smallest section half-size lofted at a closed nose tip (as the kernel's ``section_wire``).
TIP_MIN_MM = 0.3
#: Fairing sections never come closer than this to the wing skin (rounds the blunt TE).
FAIRING_MIN_OFFSET_MM = 0.5
FAIRING_SECTION_POINTS = 72
FAIRING_FILLET_SECTIONS = 11


# ---------------------------------------------------------------------------
# 2D offsets
# ---------------------------------------------------------------------------


def offset_open_curve(pts: np.ndarray, w: float, corner_deg: float = 2.0) -> np.ndarray:
    """Offset an open 2D polyline by ``w`` to its left (normal = rotate tangent +90 deg), with
    round joins at convex corners sharper than ``corner_deg``."""
    p = np.asarray(pts, float)
    if w == 0:
        return p.copy()
    d = np.diff(p, axis=0)
    seg_len = np.linalg.norm(d, axis=1)
    keep = np.concatenate([[True], seg_len > 1e-9])
    p = p[keep]
    d = np.diff(p, axis=0)
    t = d / np.linalg.norm(d, axis=1, keepdims=True)
    n = np.column_stack([-t[:, 1], t[:, 0]])
    out = [p[0] + w * n[0]]
    for i in range(1, len(p) - 1):
        n0, n1 = n[i - 1], n[i]
        ang = math.atan2(n0[0] * n1[1] - n0[1] * n1[0], float(n0 @ n1))
        if abs(math.degrees(ang)) <= corner_deg:
            m = n0 + n1
            m /= np.linalg.norm(m)
            out.append(p[i] + w * m / max(float(m @ n0), 0.5))
            continue
        turn_left = ang > 0
        if (turn_left and w > 0) or (not turn_left and w < 0):
            # concave side for this offset: the two offset segments cross; use the miter point
            m = n0 + n1
            m /= np.linalg.norm(m)
            out.append(p[i] + w * m / max(float(m @ n0), 0.2))
            continue
        k = max(2, int(abs(math.degrees(ang)) / 5))
        a0 = math.atan2(n0[1], n0[0])
        for j in range(k + 1):
            a = a0 + ang * j / k
            out.append(p[i] + w * np.array([math.cos(a), math.sin(a)]))
    out.append(p[-1] + w * n[-1])
    return np.array(out)


def offset_closed_loop(pts: np.ndarray, w: float) -> np.ndarray:
    """Outward offset of a closed 2D loop (any orientation), round joins at convex corners."""
    p = np.asarray(pts, float)
    if np.linalg.norm(p[0] - p[-1]) < 1e-9:
        p = p[:-1]
    area = 0.5 * float(np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1]))
    if area > 0:  # counter-clockwise: outward is to the right
        p = p[::-1]
    closed = np.vstack([p[-1], p, p[0]])
    off = offset_open_curve(closed, w)
    # the first and last points belong to the wrap-around segment p[-1] -> p[0]
    return off[1:-1]


def resample_loop(pts: np.ndarray, n: int, curvature_weight: float = 0.35) -> np.ndarray:
    """Resample a closed 2D loop to n points, denser where it turns (curvature-weighted)."""
    p = np.asarray(pts, float)
    closed = np.vstack([p, p[:1]])
    d = np.diff(closed, axis=0)
    ds = np.linalg.norm(d, axis=1)
    keep = ds > 1e-9
    closed = np.vstack([closed[:-1][keep], closed[:1]])
    d = np.diff(closed, axis=0)
    ds = np.linalg.norm(d, axis=1)
    ang = np.arctan2(d[:, 1], d[:, 0])
    turn = np.abs(np.angle(np.exp(1j * (ang - np.roll(ang, 1)))))
    perim = float(ds.sum())
    wgt = ds + curvature_weight * perim / (2 * math.pi) * turn
    s = np.concatenate([[0.0], np.cumsum(wgt)])
    targets = np.linspace(0, s[-1], n, endpoint=False)
    x = np.interp(targets, s, closed[:, 0])
    y = np.interp(targets, s, closed[:, 1])
    return np.column_stack([x, y])


def spline_loop_wire(pts3: np.ndarray) -> cq.Wire:
    """Closed periodic B-spline wire through 3D points (one edge, so lofts stay compatible)."""
    vs = [P.V(q) for q in pts3]
    edge = cq.Edge.makeSpline(vs, periodic=True)
    return cq.Wire.assembleEdges([edge])


# ---------------------------------------------------------------------------
# Source description
# ---------------------------------------------------------------------------


@dataclass
class MouldSource:
    key: str
    label: str
    short: str
    long_axis: np.ndarray
    pull_candidates: list[np.ndarray]
    net_range: tuple[float, float]  # along the long axis (aircraft mm, u = p . long_axis)
    mould_range: tuple[float, float]
    outer: Callable[[float, float, float], cq.Shape]  # (w, lo, hi) -> solid
    curvature: Callable[[float], float]
    kinks: list[float]
    trim_planes: list[tuple[np.ndarray, float, str]]  # (normal, offset p.n, description)
    open_ends: list[str]
    clip: Callable[[np.ndarray], tuple[float, float] | None] | None = None
    description: str = ""
    net_description: str = ""
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Fuselage family (nose bay and fuselage shell)
# ---------------------------------------------------------------------------


def _scale_ext(fm: FuselageModel, x: float) -> float:
    """``FuselageModel.scale`` continued past the tail end along the taper (for extensions)."""
    if x <= fm.length:
        return fm.scale(x)
    t = (x - (fm.length - fm.tail_length)) / max(fm.tail_length, 1e-9)
    return max(0.05, 1.0 + (fm.tail_end_scale - 1.0) * t)


def _profile_curve(fm: FuselageModel, half: float, x_end: float) -> np.ndarray:
    """Side/plan profile (x, r) of half-size ``half``: elliptic nose by angle, straight middle,
    linear taper (continued to ``x_end``)."""
    ln = fm.nose_length
    th = np.linspace(0.0, math.pi / 2, 61)
    nose = np.column_stack([ln * (1 - np.cos(th)), half * np.sin(th)])
    x_tail = fm.length - fm.tail_length
    pts = [nose]
    if x_tail > ln + 1e-6:
        mid = np.linspace(ln, x_tail, max(2, int((x_tail - ln) / 20) + 1))[1:]
        pts.append(np.column_stack([mid, np.full_like(mid, half)]))
    tail = np.linspace(x_tail, x_end, max(2, int((x_end - x_tail) / 10) + 1))[1:]
    pts.append(np.column_stack([tail, [half * _scale_ext(fm, float(x)) for x in tail]]))
    return np.vstack(pts)


class FuselageSurface:
    """Half-sizes of the fuselage OML offset outward by w (true 2D offsets of the profiles)."""

    def __init__(self, fm: FuselageModel, x_end: float):
        self.fm = fm
        self.x_end = x_end
        self._cache: dict[float, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}

    def profiles(self, w: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        key = round(w, 4)
        if key not in self._cache:
            fm = self.fm
            curves = []
            for half in (fm.width / 2, fm.height / 2):
                c = _profile_curve(fm, half, self.x_end + w + 5)
                # walking +x along the profile, the outside (larger r) is on the left
                off = offset_open_curve(c, w) if w > 0 else c
                order = np.argsort(off[:, 0], kind="stable")
                curves.append(off[order])
            xs = np.unique(np.concatenate([curves[0][:, 0], curves[1][:, 0]]))
            a = np.interp(xs, curves[0][:, 0], curves[0][:, 1])
            b = np.interp(xs, curves[1][:, 0], curves[1][:, 1])
            self._cache[key] = (xs, a, b)
        return self._cache[key]

    def half_size(self, x: float, w: float) -> tuple[float, float]:
        if w == 0:
            s = _scale_ext(self.fm, x)
            return self.fm.width * s / 2, self.fm.height * s / 2
        xs, a, b = self.profiles(w)
        return float(np.interp(x, xs, a)), float(np.interp(x, xs, b))

    def tip_x(self, w: float) -> float:
        """First x where both half-sizes reach the tip minimum."""
        if w == 0:
            xs = np.linspace(0.0, self.fm.nose_length, 4000)
            ok = [x for x in xs if min(self.half_size(float(x), 0.0)) >= TIP_MIN_MM]
            return float(ok[0]) if ok else 0.05
        xs, a, b = self.profiles(w)
        idx = np.flatnonzero(np.minimum(a, b) >= TIP_MIN_MM)
        return float(xs[idx[0]]) if idx.size else float(xs[0])

    def stations(self, lo: float, hi: float, w: float) -> list[list[float]]:
        """Loft stations split into segments at the nose/constant/taper junctions."""
        fm = self.fm
        ln, xt = fm.nose_length, fm.length - fm.tail_length
        bounds = sorted({lo, hi, *(x for x in (ln, xt) if lo < x < hi)})
        segs = []
        for a, b in itertools.pairwise(bounds):
            if b - a < 0.5:
                continue
            if b <= ln + 1e-6:
                # nose: dense near the tip (angle spacing)
                th = np.linspace(0, math.pi / 2, 49)
                xs = ln * (1 - np.cos(th)) - w * np.cos(th)
                pts = [a, b, *(float(x) for x in xs if a + 0.5 < x < b - 0.5)]
            elif a >= xt - 1e-6:
                n = max(2, int((b - a) / 25) + 1)
                pts = list(np.linspace(a, b, n)) + list(
                    np.linspace(a, min(b, a + 2 * w + 1), 5) if w > 0 else []
                )
            else:
                pts = [a, b]
            pts = sorted(set(round(float(x), 4) for x in pts))
            clean = [pts[0]]
            for x in pts[1:]:
                if x - clean[-1] >= 0.5:
                    clean.append(x)
            if clean[-1] != pts[-1]:
                clean[-1] = pts[-1]
            segs.append(clean)
        return segs

    def solid(self, w: float, lo: float, hi: float) -> cq.Shape:
        fm = self.fm
        lo = max(lo, self.tip_x(w))
        pieces = []
        for seg in self.stations(lo, hi, w):
            wires = [P.section_wire(fm, x, a_b=self.half_size(x, w)) for x in seg]
            ruled = len(seg) == 2
            pieces.append(cq.Solid.makeLoft(wires, ruled))
        if len(pieces) == 1:
            return pieces[0]
        return P.one_solid(pieces[0].fuse(*pieces[1:]).clean(), "mould source loft")

    def curvature(self, x: float) -> float:
        h = 2.0
        vals = []
        for k in (0, 1):
            r = [self.half_size(x + d, 0.0)[k] for d in (-h, 0.0, h)]
            vals.append(abs(r[0] - 2 * r[1] + r[2]) / (h * h))
        return float(max(vals))


def fuselage_family_sources(
    model: CadModel, ext: float, wall: float, flange: float = 25.0
) -> dict[str, MouldSource]:
    fm = model.fuselage
    surf = FuselageSurface(fm, fm.length + ext + 10)
    x_hat = np.array([1.0, 0.0, 0.0])
    pulls = [np.array([0.0, 1.0, 0.0]), np.array([0.0, 0.0, 1.0])]
    nb = fm.nose_bay_length
    kinks = [fm.nose_length, fm.length - fm.tail_length]

    def outer(w: float, lo: float, hi: float) -> cq.Shape:
        return surf.solid(w, lo, hi)

    nose = MouldSource(
        key="nose",
        label="Nose bay shell",
        short="NOSE",
        long_axis=x_hat,
        pull_candidates=pulls,
        net_range=(0.0, nb),
        mould_range=(-wall - flange - 3.0, nb + ext),
        outer=outer,
        curvature=surf.curvature,
        kinks=[k for k in kinks if k < nb + ext],
        trim_planes=[(x_hat, nb + 5.0, "trim line 5 mm aft of the nose-bay rear edge")],
        open_ends=[f"rear (x = {nb:.0f} mm, continues {ext:.0f} mm past the net edge)"],
        description=(
            "Swappable nose bay: closed elliptic/rounded nose and the constant section up to "
            f"x = {nb:.0f} mm. Net part: x 0-{nb:.0f} mm; the camera window, bosses and the "
            "mating flange are cut or bonded in after moulding."
        ),
        net_description=f"x = 0 to {nb:.0f} mm",
    )
    fus = MouldSource(
        key="fuselage",
        label="Fuselage shell",
        short="FUS",
        long_axis=x_hat,
        pull_candidates=pulls,
        net_range=(nb, fm.length),
        mould_range=(nb - ext, fm.length + ext),
        outer=outer,
        curvature=surf.curvature,
        kinks=[k for k in kinks if nb - ext < k < fm.length + ext],
        trim_planes=[
            (x_hat, nb - 5.0, "trim line 5 mm ahead of the nose-bay joint"),
            (x_hat, fm.length + 5.0, "trim line 5 mm behind the tail end"),
        ],
        open_ends=[
            f"front (x = {nb:.0f} mm, nose-bay joint)",
            f"rear (x = {fm.length:.0f} mm, tail end)",
        ],
        description=(
            f"Fuselage shell from the nose-bay joint (x = {nb:.0f} mm) to the tail "
            f"(x = {fm.length:.0f} mm). The tail cap, the nose-bay mating flange, frames and the "
            "spar/boom holes are bonded or drilled after moulding (flat parts from carbon "
            "plate)."
        ),
        net_description=f"x = {nb:.0f} to {fm.length:.0f} mm",
    )
    nose.extra["surface"] = surf
    fus.extra["surface"] = surf
    return {"nose": nose, "fuselage": fus}


# ---------------------------------------------------------------------------
# Wing-root fairing (fillet between the wing root and the fuselage side)
# ---------------------------------------------------------------------------


def fairing_radius(model: CadModel) -> tuple[float, list[str]]:
    """Fillet radius: 6 % of the root chord (10-40 mm), reduced so the fillet's inboard edge
    stays inside the fuselage depth where it can."""
    from app.engine.fullscale import (
        FAIRING_RADIUS_MIN_MM,
        wing_root_fairing_radius_mm,
    )

    wing = model.surfaces["wing_right"]
    fm = model.fuselage
    s0 = wing.s0
    sec = wing.section(s0)
    z_up, z_lo = float(sec[:, 2].max()), float(sec[:, 2].min())
    x_mid = float(sec[:, 0].mean())
    _, b = fm.half_size(x_mid)
    r0 = wing_root_fairing_radius_mm(wing.chord(s0))
    room = min(0.95 * b - z_up, 0.95 * b + z_lo)
    r = max(FAIRING_RADIUS_MIN_MM, min(r0, room))
    notes = []
    if room < FAIRING_RADIUS_MIN_MM:
        notes.append(
            f"The wing root reaches {z_up:.0f} mm above the centreline and the fuselage is only "
            f"{b:.0f} mm deep there, so even the minimum {FAIRING_RADIUS_MIN_MM:g} mm fillet "
            f"stands {FAIRING_RADIUS_MIN_MM - room:.1f} mm proud of the fuselage top at its "
            "inboard edge; a deeper fuselage would let the fairing blend in."
        )
    elif r < r0:
        notes.append(
            f"Fillet radius reduced from {r0:.0f} mm (6 % of the root chord) to {r:.0f} mm so "
            "the fairing's inboard edge stays inside the fuselage depth."
        )
    return r, notes


def _fillet_profile(r: float, ext_in: float, ext_out: float) -> np.ndarray:
    """(t, delta) of the fairing OML: shelf inboard of the fuselage side (t < 0, delta = R),
    concave quarter circle to the wing (t = R, delta = 0), wing skin beyond."""
    phi = np.linspace(0, math.pi / 2, 31)
    arc = np.column_stack([r * (1 - np.cos(phi)), r * (1 - np.sin(phi))])
    shelf = np.array([[-ext_in, r], [-ext_in / 2, r]])
    wing = np.array([[r + ext_out / 2, 0.0], [r + ext_out, 0.0]])
    return np.vstack([shelf, arc, wing])


class FairingSurface:
    """Fairing OML (``w = 0``) and its outward offsets: sections in planes y = const."""

    def __init__(self, wing: Surface, r: float, ext_in: float, ext_out: float):
        self.wing = wing
        self.s0 = wing.s0
        self.r = r
        self.ext_in = ext_in
        self.ext_out = ext_out
        self.base = _fillet_profile(r, ext_in, ext_out)
        self._prof: dict[float, np.ndarray] = {}

    def delta_exact(self, t: float) -> float:
        r = self.r
        if t <= 0:
            return r
        if t >= r:
            return 0.0
        return r - math.sqrt(max(0.0, r * r - (r - t) ** 2))

    def offset_profile(self, w: float) -> np.ndarray:
        key = round(w, 4)
        if key not in self._prof:
            # walking +t the material is below (smaller delta): the outside is on the left
            off = offset_open_curve(self.base, w) if w > 0 else self.base
            self._prof[key] = off[np.argsort(off[:, 0], kind="stable")]
        return self._prof[key]

    def delta(self, t: float, w: float) -> float:
        if w == 0:
            return self.delta_exact(t)
        off = self.offset_profile(w)
        return float(np.interp(t, off[:, 0], off[:, 1]))

    def section(self, t: float, delta: float) -> cq.Wire:
        pts = self.wing.section(self.s0 + t)
        y = float(pts[0, 1])
        loop2 = np.column_stack([pts[:, 0], pts[:, 2]])
        off = offset_closed_loop(loop2, max(delta, FAIRING_MIN_OFFSET_MM))
        res = resample_loop(off, FAIRING_SECTION_POINTS)
        pts3 = np.column_stack([res[:, 0], np.full(len(res), y), res[:, 1]])
        return spline_loop_wire(pts3)

    def fillet_ts(self) -> list[float]:
        phi = np.linspace(0, math.pi / 2, FAIRING_FILLET_SECTIONS)
        return [float(self.r * (1 - math.cos(p))) for p in phi]

    def solid(self, w: float, lo: float = -1e9, hi: float = 1e9) -> cq.Shape:
        """Whole fairing OML offset by w over the mould's spanwise range (``lo``/``hi`` along
        the chord are ignored: the fairing is closed around the leading and trailing edges).
        The OML (w = 0) runs 2 mm past both open ends so the mould cavity breaks through."""
        r = self.r
        if w == 0:
            e_lo, e_hi = -self.ext_in - 2.0, r + self.ext_out + 2.0
            ts = [e_lo, *self.fillet_ts(), e_hi]
            return cq.Solid.makeLoft([self.section(t, self.delta_exact(t)) for t in ts], True)
        e_lo, e_hi = -self.ext_in, r + self.ext_out
        off = self.offset_profile(w)
        inner = [float(t) for t in off[:, 0] if e_lo + 0.3 < t < e_hi - 0.3]
        ts = [e_lo]
        for t in inner:
            if t - ts[-1] >= 1.5:
                ts.append(t)
        if e_hi - ts[-1] < 1.5:
            ts.pop()
        ts.append(e_hi)
        return cq.Solid.makeLoft([self.section(t, self.delta(t, w)) for t in ts], True)

    def net_solid(self) -> cq.Shape:
        """The fillet alone (net part between the fuselage side and the wing run-out)."""
        return cq.Solid.makeLoft(
            [self.section(t, self.delta_exact(t)) for t in self.fillet_ts()], True
        )


def fairing_source(model: CadModel, ext: float, wall: float, flange: float = 25.0) -> MouldSource:
    wing: Surface = model.surfaces["wing_right"]
    s0 = wing.s0
    r, notes = fairing_radius(model)
    fs = FairingSurface(wing, r, ext, ext)
    x2, n2 = wing.axes(s0)
    # Pull normal to the root chord line, in the plane of the root section (no spanwise
    # component): the fillet meets the fuselage side vertically, so any spanwise tilt would
    # turn that edge into an undercut for one of the two halves.
    pull = n2 / np.linalg.norm(n2)
    if pull[2] < 0:
        pull = -pull
    long_axis = x2 - pull * float(x2 @ pull)
    long_axis /= np.linalg.norm(long_axis)

    # Curvature along the chord: the root airfoil's thickness curvature (high at the LE/TE).
    prof = wing.te_profile()
    c0 = wing.chord(s0)
    le = wing.point(s0, 0.0, float(prof.upper(0.0)))[0]
    u_le = float(le @ long_axis)

    def curvature(u: float) -> float:
        xc = (u - u_le) / c0
        if xc <= 0.02 or xc >= 0.98:
            return 1.0
        h = 0.01
        t = [float(prof.thickness(min(1.0, max(0.0, xc + d)))) * c0 for d in (-h, 0.0, h)]
        return abs(t[0] - 2 * t[1] + t[2]) / ((h * c0) ** 2)

    sec0 = wing.section(s0)
    u_vals = sec0 @ long_axis
    pad = r + wall + flange + 3.0  # the flange wraps the leading and trailing edges
    y_hat = np.array([0.0, 1.0, 0.0])
    src = MouldSource(
        key="wing_root_fairing",
        label="Wing-root fairing (right; the left is its mirror image)",
        short="WRF",
        long_axis=long_axis,
        pull_candidates=[pull, np.array([0.0, 0.0, 1.0])],
        net_range=(float(u_vals.min()) - r, float(u_vals.max()) + r),
        mould_range=(float(u_vals.min()) - pad, float(u_vals.max()) + pad),
        outer=fs.solid,
        curvature=curvature,
        kinks=[],
        trim_planes=[
            (y_hat, s0 - 5.0, "trim line 5 mm inboard of the fuselage side (scribe to fit)"),
            (y_hat, s0 + r + 5.0, "trim line 5 mm outboard of the fillet run-out on the wing"),
        ],
        open_ends=[
            f"inboard (y = {s0 - ext:.0f} mm, {ext:.0f} mm past the fuselage side)",
            f"outboard (y = {s0 + r + ext:.0f} mm, on the wing skin)",
        ],
        description=(
            f"Concave fillet of radius {r:.0f} mm between the fuselage side (y = {s0:.1f} mm) and "
            f"the wing root ({c0:.0f} mm chord), wrapping the leading and trailing edges; the "
            "fillet height at each spanwise station is R - sqrt(R^2 - (R - t)^2) above the "
            "wing skin, t measured from the fuselage side. The left fairing is the mirror image "
            "(mirror the print files in the slicer)."
        ),
        net_description=f"y = {s0:.1f} to {s0 + r:.1f} mm, whole root chord",
        notes=notes,
    )
    src.extra.update(
        {
            "surface": fs,
            "radius_mm": r,
            "span_axis": y_hat,
            "span_range": (s0 - ext, s0 + r + ext),
            "net_span_range": (s0, s0 + r),
            "root_y_mm": s0,
        }
    )
    return src
