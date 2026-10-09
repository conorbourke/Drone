/**
 * 2D projections of the engine's render primitives for the top, side and front drawings and
 * the version comparison. Pure functions, no React or DOM.
 *
 * Drawing coordinates are millimetres with v pointing down (SVG):
 *   top   u = y (starboard right), v = x (nose up)
 *   side  u = x (nose left), v = -z
 *   front u = -y (looking at the nose, starboard on the left), v = -z
 */
import type { Geometry, Vec3 } from '../engine';
import type { Delta } from './handles';

export type View = 'top' | 'side' | 'front';
export type Pt = [number, number];

export type ShapeRole = 'fuselage' | 'nosebay' | 'wing' | 'tail' | 'boom' | 'support' | 'motor' | 'prop' | 'gear';

export interface Shape {
  role: ShapeRole;
  /** Closed polygon unless `open`. */
  points: Pt[];
  open?: boolean;
  /** Stroke width in mm for open lines (gear legs, thin parts). */
  width?: number;
}

export interface Bounds {
  minU: number;
  maxU: number;
  minV: number;
  maxV: number;
}

export function project(view: View, p: Vec3): Pt {
  switch (view) {
    case 'top':
      return [p[1], p[0]];
    case 'side':
      return [p[0], -p[2]];
    case 'front':
      return [-p[1], -p[2]];
  }
}

/** Pointer displacement on the drawing (mm) as a displacement in aircraft coordinates. */
export function unprojectDelta(view: View, du: number, dv: number): Delta {
  switch (view) {
    case 'top':
      return { dx: dv, dy: du, dz: 0 };
    case 'side':
      return { dx: du, dy: 0, dz: -dv };
    case 'front':
      return { dx: 0, dy: -du, dz: -dv };
  }
}

/** Convex hull (Andrew's monotone chain), counter-clockwise, no repeated end point. */
export function convexHull(points: Pt[]): Pt[] {
  const pts = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (pts.length < 3) return pts;
  const cross = (o: Pt, a: Pt, b: Pt) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower: Pt[] = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper: Pt[] = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  upper.pop();
  lower.pop();
  return lower.concat(upper);
}

function normalize(v: Vec3): Vec3 {
  const l = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / l, v[1] / l, v[2] / l];
}

/** Two unit vectors spanning the plane perpendicular to `axis`. */
export function discBasis(axis: Vec3): [Vec3, Vec3] {
  const a = normalize(axis);
  const ref: Vec3 = Math.abs(a[2]) < 0.9 ? [0, 0, 1] : [1, 0, 0];
  const u = normalize([
    a[1] * ref[2] - a[2] * ref[1],
    a[2] * ref[0] - a[0] * ref[2],
    a[0] * ref[1] - a[1] * ref[0],
  ]);
  const v: Vec3 = [a[1] * u[2] - a[2] * u[1], a[2] * u[0] - a[0] * u[2], a[0] * u[1] - a[1] * u[0]];
  return [u, v];
}

/** Points on a circle of the given diameter around `centre`, perpendicular to `axis`. */
export function discPoints(centre: Vec3, diameter: number, axis: Vec3, n = 36): Vec3[] {
  const [u, v] = discBasis(axis);
  const r = diameter / 2;
  const out: Vec3[] = [];
  for (let i = 0; i < n; i++) {
    const t = (2 * Math.PI * i) / n;
    const c = Math.cos(t) * r;
    const s = Math.sin(t) * r;
    out.push([centre[0] + u[0] * c + v[0] * s, centre[1] + u[1] * c + v[1] * s, centre[2] + u[2] * c + v[2] * s]);
  }
  return out;
}

/** A round tube from `a` to `b` as its silhouette in the view (hull of the two end discs). */
function tube(view: View, a: Vec3, b: Vec3, d: number): Pt[] {
  const axis: Vec3 = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const ring = [...discPoints(a, d, axis, 16), ...discPoints(b, d, axis, 16)];
  return convexHull(ring.map((p) => project(view, p)));
}

function fuselageShapes(view: View, g: Geometry): Shape[] {
  const f = g.render.fuselage;
  const st = f.stations;
  if (st.length < 2) return [];
  if (view === 'front') {
    const w = g.fuselage.width_mm / 2;
    const h = g.fuselage.height_mm / 2;
    const pts: Pt[] = [];
    const n = 48;
    for (let i = 0; i < n; i++) {
      const t = (2 * Math.PI * i) / n;
      if (f.cross_section === 'ellipse') {
        pts.push([w * Math.cos(t), h * Math.sin(t)]);
      } else {
        // Superellipse as a rounded rectangle silhouette.
        const c = Math.cos(t);
        const s = Math.sin(t);
        pts.push([w * Math.sign(c) * Math.abs(c) ** 0.4, h * Math.sign(s) * Math.abs(s) ** 0.4]);
      }
    }
    return [{ role: 'fuselage', points: pts }];
  }
  const half = (s: (typeof st)[number]) => (view === 'top' ? s.width_mm : s.height_mm) / 2;
  const outline = (stations: typeof st): Pt[] => {
    const pts: Pt[] = [
      ...stations.map((s) => [s.x_mm, -half(s)] as Pt),
      ...[...stations].reverse().map((s) => [s.x_mm, half(s)] as Pt),
    ];
    // Top view: nose up, so x runs down the drawing.
    return view === 'top' ? pts.map(([x, h]) => [h, x] as Pt) : pts;
  };
  const shapes: Shape[] = [{ role: 'fuselage', points: outline(st) }];
  // Nose bay: the part of the body ahead of the bay's rear face, highlighted.
  const x1 = Math.min(f.nose_bay_x1_mm, st[st.length - 1].x_mm);
  if (x1 > 0) {
    const bay = st.filter((s) => s.x_mm < x1);
    const i = st.findIndex((s) => s.x_mm >= x1);
    if (i > 0) {
      const a = st[i - 1];
      const b = st[i];
      const t = (x1 - a.x_mm) / (b.x_mm - a.x_mm || 1);
      bay.push({
        x_mm: x1,
        width_mm: a.width_mm + (b.width_mm - a.width_mm) * t,
        height_mm: a.height_mm + (b.height_mm - a.height_mm) * t,
        perimeter_mm: 0,
      });
    }
    if (bay.length >= 2) shapes.push({ role: 'nosebay', points: outline(bay) });
  }
  return shapes;
}

/** Every outline of the aircraft in one view, back to front in drawing order. */
export function viewShapes(view: View, g: Geometry): Shape[] {
  const r = g.render;
  const shapes: Shape[] = [];
  const isWing = (name: string) => name.startsWith('wing');
  // Tail and supports first so the wing and fuselage sit on top in the top view.
  for (const s of r.tail_supports) shapes.push({ role: 'support', points: tube(view, s.start, s.end, s.diameter_mm) });
  for (const surface of r.surfaces.filter((s) => !isWing(s.name))) {
    shapes.push({ role: 'tail', points: convexHull(surface.sections.flat().map((p) => project(view, p))) });
  }
  for (const b of r.booms) shapes.push({ role: 'boom', points: tube(view, b.start, b.end, b.diameter_mm) });
  if (view !== 'top') shapes.push(...fuselageShapes(view, g));
  for (const surface of r.surfaces.filter((s) => isWing(s.name))) {
    shapes.push({ role: 'wing', points: convexHull(surface.sections.flat().map((p) => project(view, p))) });
  }
  if (view === 'top') shapes.push(...fuselageShapes(view, g));
  for (const seg of r.landing_gear.segments) {
    shapes.push({ role: 'gear', points: [project(view, seg.start), project(view, seg.end)], open: true, width: 6 });
  }
  r.motors.forEach((m, i) => {
    const axis = r.props[i]?.axis ?? [0, 0, 1];
    const base: Vec3 = [m.position[0] - axis[0] * m.height_mm, m.position[1] - axis[1] * m.height_mm, m.position[2] - axis[2] * m.height_mm];
    const ring = [...discPoints(m.position, m.diameter_mm, axis, 16), ...discPoints(base, m.diameter_mm, axis, 16)];
    shapes.push({ role: 'motor', points: convexHull(ring.map((p) => project(view, p))) });
  });
  for (const p of r.props) {
    shapes.push({ role: 'prop', points: convexHull(discPoints(p.centre, p.diameter_mm, p.axis).map((q) => project(view, q))) });
  }
  return shapes;
}

export function boundsOf(shapes: Shape[], extra: Pt[] = []): Bounds {
  const b: Bounds = { minU: Infinity, maxU: -Infinity, minV: Infinity, maxV: -Infinity };
  const add = ([u, v]: Pt) => {
    if (!Number.isFinite(u) || !Number.isFinite(v)) return;
    b.minU = Math.min(b.minU, u);
    b.maxU = Math.max(b.maxU, u);
    b.minV = Math.min(b.minV, v);
    b.maxV = Math.max(b.maxV, v);
  };
  shapes.forEach((s) => s.points.forEach(add));
  extra.forEach(add);
  if (!Number.isFinite(b.minU)) return { minU: -500, maxU: 500, minV: -500, maxV: 500 };
  return b;
}

/** Bounds grown by a margin on every side. */
export function padBounds(b: Bounds, margin: number): Bounds {
  return { minU: b.minU - margin, maxU: b.maxU + margin, minV: b.minV - margin, maxV: b.maxV + margin };
}

export function unionBounds(list: Bounds[]): Bounds {
  return list.reduce(
    (acc, b) => ({
      minU: Math.min(acc.minU, b.minU),
      maxU: Math.max(acc.maxU, b.maxU),
      minV: Math.min(acc.minV, b.minV),
      maxV: Math.max(acc.maxV, b.maxV),
    }),
    { minU: Infinity, maxU: -Infinity, minV: Infinity, maxV: -Infinity },
  );
}

export function pointsAttr(points: Pt[]): string {
  return points.map(([u, v]) => `${round1(u)},${round1(v)}`).join(' ');
}

function round1(x: number): number {
  return Math.round(x * 10) / 10;
}

/** Positions of the drag handles in aircraft coordinates (mm). */
export function handlePositions(g: Geometry): Record<string, Vec3> {
  const w = g.wing;
  const tipTe: Vec3 = [w.tip_le[0] + w.tip_chord_mm, w.tip_le[1], w.tip_le[2]];
  const boom = g.booms.find((b) => b.side === 'right') ?? g.booms[0];
  return {
    span: w.tip_le,
    root_chord: [w.root_le[0] + w.root_chord_mm, 0, w.root_le[2]],
    tip_chord: tipTe,
    wing_x: w.root_le,
    boom_offset: boom ? boom.start : [0, 0, 0],
    tail_arm: [g.tail.quarter_chord_x_mm, 0, g.tail.z_mm],
    fuselage_length: [0, 0, 0],
  };
}

/** Which handles each drawing shows. */
export const VIEW_HANDLES: Record<View, string[]> = {
  top: ['span', 'root_chord', 'tip_chord', 'wing_x', 'boom_offset', 'tail_arm', 'fuselage_length'],
  side: ['wing_x', 'root_chord', 'tail_arm', 'fuselage_length'],
  front: ['span', 'boom_offset'],
};
