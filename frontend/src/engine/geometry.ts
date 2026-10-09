/**
 * Derived geometry (docs/phases/PHASE2.md sections 1 and 5; docs/ENGINE.md "Geometry").
 *
 * Coordinates: origin at the nose tip on the centreline, x aft, y to starboard, z up; mm.
 * Every formula here must be reproduced by the Phase 3 Python port to 0.1 %
 * (shared/fixtures/tier1_cases.json), so conventions are spelled out in comments.
 */

import type { AirfoilSummary, DesignParameters } from '../api/types';
import {
  AIRFOIL_SHAPE_FALLBACK,
  AIRFOIL_SHAPE_GENERIC,
  DESIGN_V2_DEFAULTS,
  MOTOR_CAN_DRAW_FRACTION,
  NOSE_LENGTH_DIAMETERS,
  NOSE_LENGTH_MAX_FRACTION,
  NOSE_SEGMENTS,
  ROUNDED_RECT_CORNER_FRACTION,
  TAIL_END_SCALE,
  TAIL_LENGTH_DIAMETERS,
  TAIL_LENGTH_MAX_FRACTION,
} from './constants';
import type {
  AirfoilSummaryMap,
  BoomGeometry,
  FuselageGeometry,
  FuselageStation,
  Geometry,
  RenderPrimitives,
  RotorGeometry,
  Status,
  TailGeometry,
  Vec3,
  WingGeometry,
} from './types';
import { degToRad, isNum, radToDeg } from './units';

/** Design parameters with every schema v2 field present. */
export type ResolvedParameters = DesignParameters & {
  wing: DesignParameters['wing'] & { twist_deg: number };
  booms: DesignParameters['booms'] & { diameter_mm: number };
  tail: DesignParameters['tail'] & { v_angle_deg: number; airfoil: string };
  propulsion: NonNullable<DesignParameters['propulsion']>;
  battery: NonNullable<DesignParameters['battery']>;
  allowances: NonNullable<DesignParameters['allowances']>;
};

/** Fill schema v2 defaults (contract section 2) into a v1 or partial v2 document. Never mutates. */
export function withDefaults(p: DesignParameters): ResolvedParameters {
  const d = DESIGN_V2_DEFAULTS;
  return {
    ...p,
    wing: { ...p.wing, twist_deg: p.wing.twist_deg ?? d.wing_twist_deg },
    booms: { ...p.booms, diameter_mm: p.booms.diameter_mm ?? d.boom_diameter_mm },
    tail: {
      ...p.tail,
      v_angle_deg: p.tail.v_angle_deg ?? d.tail_v_angle_deg,
      airfoil: p.tail.airfoil ?? d.tail_airfoil,
    },
    propulsion: { ...d.propulsion, ...(p.propulsion ?? {}) },
    battery: { ...d.battery, ...(p.battery ?? {}) },
    allowances: { ...d.allowances, ...(p.allowances ?? {}) },
  };
}

export interface AirfoilShape {
  /** Thickness ratio t/c. */
  t: number;
  /** Chordwise position of maximum thickness (fraction). */
  xt: number;
  /** Maximum camber (fraction). */
  m: number;
  /** Chordwise position of maximum camber (fraction). */
  xm: number;
  /** Where the numbers came from. */
  origin: 'summary' | 'builtin' | 'generic';
}

/** Thickness and camber of an airfoil: the backend summary when given, else the built-in table, else NACA digits, else generic. */
export function airfoilShape(id: string, summary?: AirfoilSummary): AirfoilShape {
  if (summary && isNum(summary.thickness_pct) && summary.thickness_pct > 0) {
    return {
      t: summary.thickness_pct / 100,
      xt: isNum(summary.x_thickness_pct) && summary.x_thickness_pct > 0 ? summary.x_thickness_pct / 100 : 0.3,
      m: isNum(summary.camber_pct) ? summary.camber_pct / 100 : 0,
      xm: isNum(summary.x_camber_pct) ? summary.x_camber_pct / 100 : 0.4,
      origin: 'summary',
    };
  }
  const key = id.toLowerCase();
  const builtin = AIRFOIL_SHAPE_FALLBACK[key];
  if (builtin) return { ...builtin, origin: 'builtin' };
  const naca = /^naca(\d)(\d)(\d\d)$/.exec(key);
  if (naca) {
    const m = Number(naca[1]) / 100;
    return { t: Number(naca[3]) / 100, xt: 0.3, m, xm: m > 0 ? Number(naca[2]) / 10 : 0, origin: 'builtin' };
  }
  return { ...AIRFOIL_SHAPE_GENERIC, origin: 'generic' };
}

/**
 * NACA 4-digit section (Abbott & von Doenhoff 1959, section 6.4), unit chord, Selig order
 * (upper surface from the trailing edge to the leading edge, then the lower surface back),
 * cosine spacing, closed trailing edge. Used to draw sections until real coordinates load.
 */
export function naca4Coordinates(m: number, p: number, t: number, pointsPerSide = 25): [number, number][] {
  const upper: [number, number][] = [];
  const lower: [number, number][] = [];
  for (let i = 0; i <= pointsPerSide; i++) {
    const beta = (Math.PI * i) / pointsPerSide;
    const x = 0.5 * (1 - Math.cos(beta));
    const yt =
      5 * t * (0.2969 * Math.sqrt(x) - 0.126 * x - 0.3516 * x * x + 0.2843 * x ** 3 - 0.1036 * x ** 4);
    let yc = 0;
    let dyc = 0;
    if (m > 0 && p > 0 && p < 1) {
      if (x < p) {
        yc = (m / (p * p)) * (2 * p * x - x * x);
        dyc = ((2 * m) / (p * p)) * (p - x);
      } else {
        yc = (m / ((1 - p) ** 2)) * (1 - 2 * p + 2 * p * x - x * x);
        dyc = ((2 * m) / ((1 - p) ** 2)) * (p - x);
      }
    }
    const th = Math.atan(dyc);
    upper.push([x - yt * Math.sin(th), yc + yt * Math.cos(th)]);
    lower.push([x + yt * Math.sin(th), yc - yt * Math.cos(th)]);
  }
  return [...upper.reverse(), ...lower.slice(1)];
}

// ---------- Closed-form planform formulas (Raymer ch. 4 "Wing geometry", fig. of the trapezoidal wing) ----------

/** Trapezoidal planform: area, aspect ratio, taper, MAC and its spanwise station. Span is tip to tip, projected. */
export function trapezoid(spanM: number, rootM: number, tipM: number) {
  const taper = tipM / rootM;
  const area = (spanM * (rootM + tipM)) / 2;
  const ar = (spanM * spanM) / area;
  const mac = (2 / 3) * rootM * ((1 + taper + taper * taper) / (1 + taper));
  const macY = (spanM / 6) * ((1 + 2 * taper) / (1 + taper));
  return { taper, area, ar, mac, macY };
}

/** Sweep of the line at chord fraction n from the leading-edge sweep (Raymer ch. 4: tan L_n = tan L_LE - (4 n / A) (1 - taper) / (1 + taper)). */
export function sweepAt(n: number, sweepLeRad: number, ar: number, taper: number): number {
  return Math.atan(Math.tan(sweepLeRad) - ((4 * n) / ar) * ((1 - taper) / (1 + taper)));
}

/** Perimeter of a fuselage cross-section, mm (Ramanujan's ellipse approximation; rounded rectangle exact). */
export function sectionPerimeter(kind: 'ellipse' | 'rounded_rect', w: number, h: number): number {
  if (w <= 0 || h <= 0) return 0;
  if (kind === 'ellipse') {
    const a = w / 2;
    const b = h / 2;
    return Math.PI * (3 * (a + b) - Math.sqrt((3 * a + b) * (a + 3 * b)));
  }
  const r = ROUNDED_RECT_CORNER_FRACTION * Math.min(w, h);
  return 2 * (w + h) - (8 - 2 * Math.PI) * r;
}

/** Cross-section area, mm^2. */
export function sectionArea(kind: 'ellipse' | 'rounded_rect', w: number, h: number): number {
  if (w <= 0 || h <= 0) return 0;
  if (kind === 'ellipse') return (Math.PI * w * h) / 4;
  const r = ROUNDED_RECT_CORNER_FRACTION * Math.min(w, h);
  return w * h - (4 - Math.PI) * r * r;
}

/** Lifting-surface wetted area from exposed planform: 2 S (1 + 0.25 t/c) (Torenbeek, Synthesis of Subsonic Airplane Design, 1982, with equal root and tip t/c; agrees within ~1 % with Raymer ch. 7 S_wet = S_exp (1.977 + 0.52 t/c)). */
export function liftingWetted(exposedArea: number, tc: number): number {
  return 2 * exposedArea * (1 + 0.25 * tc);
}

function fuselageGeometry(p: ResolvedParameters): FuselageGeometry {
  const f = p.fuselage;
  const L = f.length_mm;
  const d = Math.max(f.width_mm, f.height_mm);
  const noseLen = Math.min(NOSE_LENGTH_DIAMETERS * d, NOSE_LENGTH_MAX_FRACTION * L);
  const tailLen = Math.min(TAIL_LENGTH_DIAMETERS * d, TAIL_LENGTH_MAX_FRACTION * L);
  const station = (x: number, s: number): FuselageStation => ({
    x_mm: x,
    width_mm: f.width_mm * s,
    height_mm: f.height_mm * s,
    perimeter_mm: sectionPerimeter(f.cross_section, f.width_mm, f.height_mm) * s,
  });
  const stations: FuselageStation[] = [];
  // Elliptical nose: scale = sqrt(1 - (1 - x/l_n)^2), NOSE_SEGMENTS equal steps in x.
  for (let i = 0; i <= NOSE_SEGMENTS; i++) {
    const x = (noseLen * i) / NOSE_SEGMENTS;
    const u = 1 - x / noseLen;
    stations.push(station(x, Math.sqrt(Math.max(0, 1 - u * u))));
  }
  // Constant section, then a straight tail cone to TAIL_END_SCALE.
  stations.push(station(L - tailLen, 1));
  stations.push(station(L, TAIL_END_SCALE));
  // Wetted area: sum of frustum lateral areas between stations using the perimeter-equivalent radius.
  let wet = 0;
  for (let i = 0; i < stations.length - 1; i++) {
    const a = stations[i];
    const b = stations[i + 1];
    const dx = b.x_mm - a.x_mm;
    const dr = (b.perimeter_mm - a.perimeter_mm) / (2 * Math.PI);
    wet += ((a.perimeter_mm + b.perimeter_mm) / 2) * Math.sqrt(dx * dx + dr * dr);
  }
  const amax = sectionArea(f.cross_section, f.width_mm, f.height_mm);
  const deq = Math.sqrt((4 * amax) / Math.PI);
  return {
    length_mm: L,
    width_mm: f.width_mm,
    height_mm: f.height_mm,
    cross_section: f.cross_section,
    stations,
    nose_length_mm: noseLen,
    tail_length_mm: tailLen,
    max_cross_section_area_m2: amax / 1e6,
    equivalent_diameter_mm: deq,
    fineness_ratio: deq > 0 ? L / deq : NaN,
    wetted_area_m2: wet / 1e6,
    nose_bay: { x0_mm: 0, x1_mm: p.nose_bay.length_mm, width_mm: p.nose_bay.width_mm, height_mm: p.nose_bay.height_mm },
  };
}

/** Place a unit-chord airfoil polyline in aircraft coordinates. `up` is the section's thickness direction. */
function placeSection(
  coords: [number, number][],
  le: Vec3,
  chord: number,
  incidenceDeg: number,
  up: Vec3,
): Vec3[] {
  const th = degToRad(incidenceDeg);
  const c = Math.cos(th);
  const s = Math.sin(th);
  return coords.map(([xc, zc]) => {
    // Rotate about the quarter chord, nose up positive (x aft, so the leading edge rises).
    const xl = (xc - 0.25) * chord;
    const zl = zc * chord;
    const xr = xl * c + zl * s;
    const zr = zl * c - xl * s;
    return [le[0] + 0.25 * chord + xr, le[1] + up[1] * zr, le[2] + up[2] * zr] as Vec3;
  });
}

export interface BuildGeometryOptions {
  /** Airfoil summaries (thickness etc.); built-in values are used when absent. */
  airfoils?: AirfoilSummaryMap;
  /** Unit-chord Selig-order coordinates by airfoil id from GET /api/airfoils/{id}. */
  coordinates?: Record<string, [number, number][]>;
  /** Skip render primitives (the estimate does not need them). Default true. */
  render?: boolean;
}

/** Derived geometry shared by the 3D view, the drawings and the estimates. Never throws. */
export function buildGeometry(params: DesignParameters, options: BuildGeometryOptions = {}): Geometry {
  const p = withDefaults(params);
  const statuses: Status[] = [];
  const w = p.wing;

  // ----- Wing -----
  const wingShape = airfoilShape(w.airfoil, options.airfoils?.[w.airfoil]);
  const tz = trapezoid(w.span_mm, w.root_chord_mm, w.tip_chord_mm);
  const sweepLe = degToRad(w.sweep_deg);
  const semi = w.span_mm / 2;
  const fusHalf = p.fuselage.width_mm / 2;
  const chordAt = (y: number) => w.root_chord_mm - (w.root_chord_mm - w.tip_chord_mm) * (y / semi);
  const cFus = chordAt(Math.min(fusHalf, semi));
  const exposedMm2 = Math.max(0, tz.area - Math.min(fusHalf, semi) * (w.root_chord_mm + cFus));
  const macXle = w.x_le_mm + tz.macY * Math.tan(sweepLe);
  const dihedral = degToRad(w.dihedral_deg);
  const wing: WingGeometry = {
    span_mm: w.span_mm,
    area_m2: tz.area / 1e6,
    exposed_area_m2: exposedMm2 / 1e6,
    aspect_ratio: tz.ar,
    taper_ratio: tz.taper,
    root_chord_mm: w.root_chord_mm,
    tip_chord_mm: w.tip_chord_mm,
    mac_mm: tz.mac,
    mac_y_mm: tz.macY,
    mac_x_le_mm: macXle,
    ac_x_mm: macXle + 0.25 * tz.mac,
    sweep_le_deg: w.sweep_deg,
    sweep_quarter_chord_deg: radToDeg(sweepAt(0.25, sweepLe, tz.ar, tz.taper)),
    sweep_max_thickness_deg: radToDeg(sweepAt(wingShape.xt, sweepLe, tz.ar, tz.taper)),
    thickness_ratio: wingShape.t,
    x_max_thickness: wingShape.xt,
    wetted_area_m2: liftingWetted(exposedMm2, wingShape.t) / 1e6,
    root_le: [w.x_le_mm, 0, w.z_mm],
    tip_le: [w.x_le_mm + semi * Math.tan(sweepLe), semi, w.z_mm + semi * Math.tan(dihedral)],
  };

  // ----- Booms and rotors -----
  const bx0 = w.x_le_mm + p.booms.x_offset_mm;
  const bx1 = bx0 + p.booms.length_mm;
  const yo = p.booms.lateral_offset_mm;
  const booms: BoomGeometry[] = (['left', 'right'] as const).map((side) => {
    const y = side === 'left' ? -yo : yo;
    return { side, start: [bx0, y, w.z_mm], end: [bx1, y, w.z_mm], diameter_mm: p.booms.diameter_mm, length_mm: p.booms.length_mm };
  });
  const xf = bx0 + p.motors.front_x_mm;
  const xr = bx0 + p.motors.rear_x_mm;
  const zm = w.z_mm + p.motors.height_mm;
  const D = p.propulsion.prop_diameter_mm;
  const layout = p.layout;
  const frontTilts = layout === 'front_tilt';
  const rearTilts = layout === 'rear_tilt';
  const rotors: RotorGeometry[] = [
    { id: 'front_left', position: [xf, -yo, zm], diameter_mm: D, axis: [0, 0, 1], tilts: frontTilts, stopped_in_cruise: !frontTilts },
    { id: 'front_right', position: [xf, yo, zm], diameter_mm: D, axis: [0, 0, 1], tilts: frontTilts, stopped_in_cruise: !frontTilts },
    { id: 'rear_left', position: [xr, -yo, zm], diameter_mm: D, axis: [0, 0, 1], tilts: rearTilts, stopped_in_cruise: !rearTilts },
    { id: 'rear_right', position: [xr, yo, zm], diameter_mm: D, axis: [0, 0, 1], tilts: rearTilts, stopped_in_cruise: !rearTilts },
  ];
  if (layout === 'quad_pusher') {
    rotors.push({
      id: 'pusher',
      position: [p.pusher.x_mm, 0, 0],
      diameter_mm: p.pusher.prop_diameter_mm,
      axis: [-1, 0, 0],
      tilts: false,
      stopped_in_cruise: false,
    });
  }
  const tiltHingeX = layout === 'quad_pusher' ? null : bx0 + p.tilt.axis_x_mm;

  // ----- Tail -----
  const t = p.tail;
  const tailShape = airfoilShape(t.airfoil, options.airfoils?.[t.airfoil]);
  const isV = t.type === 'v_tail' || t.type === 'inverted_v';
  const gammaDeg = isV ? (t.type === 'inverted_v' ? -1 : 1) * t.v_angle_deg : 0;
  const gamma = degToRad(Math.abs(gammaDeg));
  const spanChord = (t.span_mm * t.chord_mm) / 1e6;
  let planform: number;
  let hArea: number;
  let vArea: number;
  let pitchArea: number;
  let tailAr: number;
  if (isV) {
    planform = spanChord / Math.cos(gamma);
    hArea = planform * Math.cos(gamma);
    vArea = planform * Math.sin(gamma);
    pitchArea = planform * Math.cos(gamma) ** 2;
    tailAr = t.span_mm / (t.chord_mm * Math.cos(gamma));
  } else {
    const fins = t.type === 'twin_boom_h' ? 2 : 1;
    hArea = spanChord;
    vArea = (fins * t.height_mm * t.chord_mm) / 1e6;
    planform = hArea + vArea;
    pitchArea = hArea;
    tailAr = t.span_mm / t.chord_mm;
  }
  const tailC4 = wing.ac_x_mm + t.arm_mm;
  const tailLe = tailC4 - 0.25 * t.chord_mm;
  const tailTe = tailLe + t.chord_mm;
  const twinBoom = t.type === 'twin_boom_h';
  const tailZ = (twinBoom ? w.z_mm : 0) + t.height_mm;
  const supportLength = twinBoom ? Math.max(0, tailTe - bx1) : Math.max(0, tailTe - p.fuselage.length_mm);
  const sRef = tz.area / 1e6;
  const tail: TailGeometry = {
    type: t.type,
    planform_area_m2: planform,
    horizontal_area_m2: hArea,
    vertical_area_m2: vArea,
    pitch_effective_area_m2: pitchArea,
    aspect_ratio: tailAr,
    chord_mm: t.chord_mm,
    panel_angle_deg: gammaDeg,
    arm_mm: t.arm_mm,
    quarter_chord_x_mm: tailC4,
    le_x_mm: tailLe,
    te_x_mm: tailTe,
    z_mm: tailZ,
    thickness_ratio: tailShape.t,
    x_max_thickness: tailShape.xt,
    wetted_area_m2: liftingWetted(planform, tailShape.t),
    horizontal_volume_coefficient: (hArea * t.arm_mm) / (sRef * tz.mac),
    vertical_volume_coefficient: (vArea * t.arm_mm) / (sRef * w.span_mm),
    support_length_mm: supportLength,
    support_kind: twinBoom ? 'booms' : 'centre',
  };

  const fuselage = fuselageGeometry(p);
  const gearBottom =
    Math.min(-p.fuselage.height_mm / 2, w.z_mm - p.booms.diameter_mm / 2) - Math.max(0, p.landing_gear.height_mm);

  // ----- Geometry statuses (plain language) -----
  if (p.motors.rear_x_mm - p.motors.front_x_mm < D) {
    statuses.push({
      key: 'geometry.props_fore_aft',
      label: 'Propeller clearance (front to rear)',
      level: 'fail',
      message: `The front and rear propellers on each boom overlap: they are ${fmt(p.motors.rear_x_mm - p.motors.front_x_mm)} mm apart but ${fmt(D)} mm across. Move the motors further apart or choose smaller propellers.`,
    });
  }
  if (2 * yo < D) {
    statuses.push({
      key: 'geometry.props_left_right',
      label: 'Propeller clearance (left to right)',
      level: 'fail',
      message: `The left and right propellers overlap: the booms are ${fmt(2 * yo)} mm apart but the propellers are ${fmt(D)} mm across. Move the booms outwards or choose smaller propellers.`,
    });
  }
  if (yo - D / 2 < fusHalf && 2 * yo >= D) {
    statuses.push({
      key: 'geometry.props_fuselage',
      label: 'Propeller clearance (fuselage)',
      level: 'warn',
      message: `Seen from above, the propeller discs reach over the fuselage side (${fmt(fusHalf - (yo - D / 2))} mm overlap). In hover the fuselage blocks part of the airflow; move the booms outwards if you can.`,
    });
  }
  if (yo <= semi) {
    const leAtBoom = w.x_le_mm + yo * Math.tan(sweepLe);
    const teAtBoom = leAtBoom + chordAt(yo);
    if (xf + D / 2 > leAtBoom) {
      statuses.push({
        key: 'geometry.front_prop_wing',
        label: 'Front propeller over the wing',
        level: 'warn',
        message: `The front propeller discs reach ${fmt(xf + D / 2 - leAtBoom)} mm over the wing leading edge. The wing then blocks part of the hover airflow (more hover power); move the boom front or the front motor forward.`,
      });
    }
    if (xr - D / 2 < teAtBoom) {
      statuses.push({
        key: 'geometry.rear_prop_wing',
        label: 'Rear propeller over the wing',
        level: 'warn',
        message: `The rear propeller discs reach ${fmt(teAtBoom - (xr - D / 2))} mm over the wing trailing edge. Move the rear motor aft to keep the wing out of the hover airflow.`,
      });
    }
  } else {
    statuses.push({
      key: 'geometry.booms_outside_wing',
      label: 'Booms outside the wing',
      level: 'fail',
      message: 'The booms are further out than the wing tips, so nothing holds them. Reduce the boom offset or increase the span.',
    });
  }
  if (p.motors.rear_x_mm > p.booms.length_mm || p.motors.front_x_mm < 0) {
    statuses.push({
      key: 'geometry.motor_off_boom',
      label: 'Motor beyond the boom',
      level: 'warn',
      message: 'A motor station lies beyond the end of the boom. Lengthen the boom or move the motor onto it.',
    });
  }
  if (tiltHingeX !== null) {
    const tiltMotorX = frontTilts ? xf : xr;
    if (Math.abs(tiltHingeX - tiltMotorX) > 0.25 * D) {
      statuses.push({
        key: 'geometry.tilt_axis',
        label: 'Tilt axis position',
        level: 'warn',
        message: `The tilt axis is ${fmt(Math.abs(tiltHingeX - tiltMotorX))} mm from the ${frontTilts ? 'front' : 'rear'} motors that tilt. Put the axis close to (normally just behind or in front of) the tilting motors.`,
      });
    }
  }
  if (supportLength > 0) {
    statuses.push({
      key: 'geometry.tail_support',
      label: 'Tail support',
      level: 'info',
      message: twinBoom
        ? `The tail sits ${fmt(supportLength)} mm behind the boom ends; the booms are assumed to be extended to carry it (included in the mass).`
        : `The tail sits ${fmt(supportLength)} mm behind the fuselage; a carbon tail boom of that length is assumed (included in the mass and drag).`,
    });
  }
  if (isV && (t.v_angle_deg < 10 || t.v_angle_deg > 70)) {
    statuses.push({
      key: 'geometry.v_angle',
      label: 'V-tail angle',
      level: 'warn',
      message: `A V-tail angle of ${fmt(t.v_angle_deg)} degrees gives very unequal pitch and yaw authority; 30-45 degrees is usual.`,
    });
  }

  const render = options.render === false ? emptyRender() : buildRender(p, wing, tail, fuselage, booms, rotors, tiltHingeX, gearBottom, options);

  return {
    layout,
    wing,
    tail,
    fuselage,
    booms,
    rotors,
    lift_rotor_centre_x_mm: (xf + xr) / 2,
    front_rotor_x_mm: xf,
    rear_rotor_x_mm: xr,
    tilt_hinge_x_mm: tiltHingeX,
    landing_gear_bottom_z_mm: gearBottom,
    render,
    statuses,
  };
}

function fmt(v: number): string {
  return Number.isFinite(v) ? String(Math.round(v)) : '?';
}

function emptyRender(): RenderPrimitives {
  return {
    surfaces: [],
    fuselage: { stations: [], cross_section: 'ellipse', nose_bay_x1_mm: 0 },
    booms: [],
    tail_supports: [],
    motors: [],
    props: [],
    tilt_hinges: [],
    landing_gear: { type: 'none', segments: [] },
  };
}

function sectionCoords(id: string, options: BuildGeometryOptions): [number, number][] {
  const c = options.coordinates?.[id];
  if (c && c.length >= 5) return c;
  const s = airfoilShape(id, options.airfoils?.[id]);
  return naca4Coordinates(s.m, s.xm, s.t);
}

function buildRender(
  p: ResolvedParameters,
  wing: WingGeometry,
  tail: TailGeometry,
  fuselage: FuselageGeometry,
  booms: BoomGeometry[],
  rotors: RotorGeometry[],
  tiltHingeX: number | null,
  gearBottom: number,
  options: BuildGeometryOptions,
): RenderPrimitives {
  const w = p.wing;
  const wingCoords = sectionCoords(w.airfoil, options);
  const tailCoords = sectionCoords(p.tail.airfoil, options);
  const dih = degToRad(w.dihedral_deg);
  const surfaces: RenderPrimitives['surfaces'] = [];
  for (const side of [1, -1]) {
    const up: Vec3 = [0, -side * Math.sin(dih), Math.cos(dih)];
    const tip: Vec3 = [wing.tip_le[0], side * wing.tip_le[1], wing.tip_le[2]];
    surfaces.push({
      name: side > 0 ? 'wing_right' : 'wing_left',
      sections: [
        placeSection(wingCoords, wing.root_le, w.root_chord_mm, w.incidence_deg, up),
        placeSection(wingCoords, tip, w.tip_chord_mm, w.incidence_deg + w.twist_deg, up),
      ],
    });
  }
  const t = p.tail;
  const half = t.span_mm / 2;
  const le = tail.le_x_mm;
  const z0 = tail.z_mm;
  if (t.type === 'v_tail' || t.type === 'inverted_v') {
    const g = degToRad(tail.panel_angle_deg);
    for (const side of [1, -1]) {
      const up: Vec3 = [0, -side * Math.sin(g), Math.cos(g)];
      surfaces.push({
        name: side > 0 ? 'tail_right' : 'tail_left',
        sections: [
          placeSection(tailCoords, [le, 0, z0], t.chord_mm, 0, up),
          placeSection(tailCoords, [le, side * half, z0 + half * Math.tan(g)], t.chord_mm, 0, up),
        ],
      });
    }
  } else {
    surfaces.push({
      name: 'horizontal_tail',
      sections: [
        placeSection(tailCoords, [le, -half, z0], t.chord_mm, 0, [0, 0, 1]),
        placeSection(tailCoords, [le, half, z0], t.chord_mm, 0, [0, 0, 1]),
      ],
    });
    const finYs = t.type === 'twin_boom_h' ? [-p.booms.lateral_offset_mm, p.booms.lateral_offset_mm] : [0];
    const finBase = t.type === 'twin_boom_h' ? w.z_mm : 0;
    finYs.forEach((y, i) => {
      // Fin sections lie in the x-y plane, so "up" (thickness) is along y.
      surfaces.push({
        name: finYs.length > 1 ? `fin_${i === 0 ? 'left' : 'right'}` : 'fin',
        sections: [
          placeSection(tailCoords, [le, y, finBase], t.chord_mm, 0, [0, 1, 0]),
          placeSection(tailCoords, [le, y, finBase + t.height_mm], t.chord_mm, 0, [0, 1, 0]),
        ],
      });
    });
  }

  const tailSupports: RenderPrimitives['tail_supports'] = [];
  if (tail.support_length_mm > 0) {
    if (tail.support_kind === 'booms') {
      for (const b of booms) tailSupports.push({ start: b.end, end: [tail.te_x_mm, b.end[1], b.end[2]], diameter_mm: b.diameter_mm });
    } else {
      tailSupports.push({ start: [p.fuselage.length_mm, 0, 0], end: [tail.te_x_mm, 0, 0], diameter_mm: p.booms.diameter_mm });
    }
  }
  const motors: RenderPrimitives['motors'] = rotors.map((r) => {
    const dm = MOTOR_CAN_DRAW_FRACTION * r.diameter_mm;
    return { id: r.id, position: r.position, diameter_mm: dm, height_mm: 0.6 * dm };
  });
  const props: RenderPrimitives['props'] = rotors.map((r) => ({ id: r.id, centre: r.position, diameter_mm: r.diameter_mm, axis: r.axis }));
  const tiltHinges: RenderPrimitives['tilt_hinges'] =
    tiltHingeX === null ? [] : booms.map((b) => ({ position: [tiltHingeX, b.start[1], b.start[2]] as Vec3, axis: [0, 1, 0] as Vec3 }));
  const segs: { start: Vec3; end: Vec3 }[] = [];
  const gt = p.landing_gear.type;
  const xf = rotors[0].position[0];
  const xr = rotors[2].position[0];
  const zb = w.z_mm - p.booms.diameter_mm / 2;
  for (const b of booms) {
    const y = b.start[1];
    if (gt === 'skids') {
      const xa = xf + 0.25 * (xr - xf);
      const xb = xr - 0.25 * (xr - xf);
      segs.push({ start: [xa, y, zb], end: [xa, y, gearBottom] });
      segs.push({ start: [xb, y, zb], end: [xb, y, gearBottom] });
      const ext = 0.1 * (xb - xa);
      segs.push({ start: [xa - ext, y, gearBottom], end: [xb + ext, y, gearBottom] });
    } else if (gt === 'legs') {
      segs.push({ start: [xf, y, zb], end: [xf, y, gearBottom] });
      segs.push({ start: [xr, y, zb], end: [xr, y, gearBottom] });
    }
  }
  return {
    surfaces,
    fuselage: { stations: fuselage.stations, cross_section: fuselage.cross_section, nose_bay_x1_mm: p.nose_bay.length_mm },
    booms,
    tail_supports: tailSupports,
    motors,
    props,
    tilt_hinges: tiltHinges,
    landing_gear: { type: gt, segments: segs },
  };
}
