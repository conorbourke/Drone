/**
 * Aerodynamics (docs/ENGINE.md "Aerodynamics"): airfoil data interpolation, lift-curve slope,
 * maximum lift and stall speed, parasite drag build-up, Oswald efficiency, induced drag and the
 * neutral point.
 */

import type { AirfoilPolarSummary, AirfoilSummary, Scale } from '../api/types';
import type { Atmosphere } from './atmosphere';
import { mach, reynolds } from './atmosphere';
import {
  CD_FLAT_PLATE,
  CD_MOTOR_CAN_FRONT,
  CD_MOTOR_CAN_SIDE,
  CD_ROUND_STRUT,
  CF_LAMINAR_COEFF,
  CF_TURB_COEFF,
  CF_TURB_EXP,
  CLMAX_3D_FACTOR,
  FORM_FACTOR_MACH_FLOOR,
  FUSELAGE_LIFT_CAP,
  FUSELAGE_LIFT_FACTOR_COEFF,
  GEAR_STRUT_DIAMETER_FRACTION,
  GENERIC_POLAR,
  GENERIC_TAIL_POLAR,
  INTERFERENCE,
  LAMINAR_FRACTION,
  LEAKAGE_PROTUBERANCE_FRACTION,
  MOTOR_CAN_DENSITY,
  MOTOR_CAN_HEIGHT_RATIO,
  OSWALD_MAX,
  OSWALD_MIN,
  SECTION_CL_ALPHA_MAX,
  PROP_AZIMUTH_AVERAGE,
  PROP_BLADE_RADIUS_FRACTION,
  PROP_BLADE_THICKNESS_RATIO,
  PROP_MEAN_CHORD_FRACTION,
  SURFACE_ROUGHNESS_M,
  TAIL_EFFICIENCY,
} from './constants';
import { sectionArea, type ResolvedParameters } from './geometry';
import type { DragItem, Geometry, Status } from './types';
import { degToRad } from './units';

// ---------- Airfoil data ----------

export type PolarPoint = Omit<AirfoilPolarSummary, 're' | 'cl_max_at_sweep_end'>;

export interface PolarLookup {
  polar: PolarPoint;
  /** 'table' (interpolated), 'clamped' (outside the table's Re range), 'generic' (no data). */
  quality: 'table' | 'clamped' | 'generic';
  reMin: number;
  reMax: number;
  /**
   * True when a table row used for this lookup had its cl_max at the end of the XFOIL alpha
   * sweep: cl_max is then a lower bound.
   */
  clMaxLowerBound: boolean;
}

const POLAR_KEYS: (keyof PolarPoint)[] = [
  'cl_max',
  'alpha_cl_max_deg',
  'alpha_zero_lift_deg',
  'cl_alpha_per_rad',
  'cd_min',
  'cl_at_cd_min',
  'cm0',
];

/** Interpolate the XFOIL polar summary linearly in log(Re); clamp outside the table. */
export function interpolatePolar(summary: AirfoilSummary | undefined, re: number, use: 'wing' | 'tail'): PolarLookup {
  const rows = (summary?.polar_summary ?? [])
    .filter((r) => Number.isFinite(r.re) && r.re > 0 && POLAR_KEYS.every((k) => Number.isFinite(r[k])))
    .slice()
    .sort((a, b) => a.re - b.re);
  if (rows.length === 0 || !Number.isFinite(re) || re <= 0) {
    return { polar: use === 'tail' ? { ...GENERIC_TAIL_POLAR } : { ...GENERIC_POLAR }, quality: 'generic', reMin: NaN, reMax: NaN, clMaxLowerBound: false };
  }
  const reMin = rows[0].re;
  const reMax = rows[rows.length - 1].re;
  const pick = (r: AirfoilPolarSummary): PolarPoint => {
    const out = {} as PolarPoint;
    for (const k of POLAR_KEYS) out[k] = r[k];
    return out;
  };
  const atEnd = (r: AirfoilPolarSummary) => r.cl_max_at_sweep_end === true;
  if (re <= reMin) return { polar: pick(rows[0]), quality: re < reMin * 0.98 ? 'clamped' : 'table', reMin, reMax, clMaxLowerBound: atEnd(rows[0]) };
  if (re >= reMax) {
    const last = rows[rows.length - 1];
    return { polar: pick(last), quality: re > reMax * 1.02 ? 'clamped' : 'table', reMin, reMax, clMaxLowerBound: atEnd(last) };
  }
  let i = 0;
  while (i < rows.length - 2 && rows[i + 1].re < re) i++;
  const a = rows[i];
  const b = rows[i + 1];
  const t = (Math.log(re) - Math.log(a.re)) / (Math.log(b.re) - Math.log(a.re));
  const out = {} as PolarPoint;
  for (const k of POLAR_KEYS) out[k] = a[k] + t * (b[k] - a[k]);
  // An interpolated cl_max is a lower bound if either neighbour's is.
  return { polar: out, quality: 'table', reMin, reMax, clMaxLowerBound: atEnd(a) || atEnd(b) };
}

// ---------- Lift ----------

/**
 * 3D lift-curve slope per radian, Helmbold/DATCOM form (Raymer ch. 12.4; Anderson, Fundamentals
 * of Aerodynamics, Helmbold's equation for Lambda = 0):
 *   CLa = 2 pi A / (2 + sqrt(4 + (A beta / eta)^2 (1 + tan^2(L_t) / beta^2))) x F (S_exp / S_ref)
 * with eta = cla / (2 pi / beta), beta^2 = 1 - M^2, L_t the sweep of the maximum-thickness line.
 * `bodyFactor` is F S_exp / S_ref (pass 1 for a tail); Raymer: if it exceeds 1, use 0.98.
 *
 * The section slope is capped at 2 pi (thin-airfoil theory). At Re 60k-200k the XFOIL fit can
 * exceed it (e.g. SD7037 8.57/rad at 60k) because a laminar separation bubble distorts the lift
 * curve inside the fit range; that is not a real whole-wing slope (see SECTION_CL_ALPHA_MAX).
 */
export function liftCurveSlope(ar: number, sweepMaxThicknessRad: number, sectionClAlpha: number, machNo: number, bodyFactor = 1): number {
  const beta2 = Math.max(1e-4, 1 - machNo * machNo);
  const beta = Math.sqrt(beta2);
  const cla = Math.min(sectionClAlpha, SECTION_CL_ALPHA_MAX);
  const eta = cla / ((2 * Math.PI) / beta);
  const tanL = Math.tan(sweepMaxThicknessRad);
  const root = Math.sqrt(4 + ((ar * ar * beta2) / (eta * eta)) * (1 + (tanL * tanL) / beta2));
  const factor = bodyFactor > 1 ? FUSELAGE_LIFT_CAP : bodyFactor;
  return ((2 * Math.PI * ar) / (2 + root)) * factor;
}

/** Fuselage lift factor F S_exp / S_ref with F = 1.07 (1 + d/b)^2 (Raymer ch. 12.4). */
export function wingBodyFactor(g: Geometry): number {
  const F = FUSELAGE_LIFT_FACTOR_COEFF * (1 + g.fuselage.equivalent_diameter_mm / g.wing.span_mm) ** 2;
  return F * (g.wing.exposed_area_m2 / g.wing.area_m2);
}

/** Wing maximum lift coefficient: 0.9 x section cl_max x cos(sweep c/4) (Raymer ch. 12.4). */
export function wingClMax(sectionClMax: number, sweepQuarterChordRad: number): number {
  return CLMAX_3D_FACTOR * sectionClMax * Math.cos(sweepQuarterChordRad);
}

/** Stall speed, m/s: sqrt(2 W / (rho S CLmax)) (Anderson, Aircraft Performance and Design, ch. 5). */
export function stallSpeed(weightN: number, rho: number, areaM2: number, clMax: number): number {
  return Math.sqrt((2 * weightN) / (rho * areaM2 * clMax));
}

/** Lift coefficient for level flight: W / (q S). */
export function liftCoefficient(weightN: number, rho: number, speed: number, areaM2: number): number {
  return weightN / (0.5 * rho * speed * speed * areaM2);
}

// ---------- Skin friction and form factors (Raymer ch. 12.5) ----------

/** Laminar flat-plate Cf = 1.328 / sqrt(Re) (Blasius). */
export function cfLaminar(re: number): number {
  return CF_LAMINAR_COEFF / Math.sqrt(re);
}

/** Turbulent flat-plate Cf = 0.455 / ((log10 Re)^2.58 (1 + 0.144 M^2)^0.65) (Raymer ch. 12.5). */
export function cfTurbulent(re: number, machNo: number): number {
  return CF_TURB_COEFF / (Math.log10(re) ** CF_TURB_EXP * (1 + 0.144 * machNo * machNo) ** 0.65);
}

/** Cutoff Reynolds number for surface roughness k (subsonic): 38.21 (l/k)^1.053 (Raymer ch. 12.5). */
export function cutoffReynolds(lengthM: number, roughnessM: number): number {
  return 38.21 * (lengthM / roughnessM) ** 1.053;
}

/** Mixed laminar/turbulent Cf: laminar fraction weighted; the turbulent part uses min(Re, cutoff Re). */
export function skinFriction(re: number, machNo: number, laminarFraction: number, lengthM: number, roughnessM: number): number {
  const reT = Math.min(re, cutoffReynolds(lengthM, roughnessM));
  return laminarFraction * cfLaminar(re) + (1 - laminarFraction) * cfTurbulent(reT, machNo);
}

/** Lifting-surface form factor [1 + 0.6/(x/c)_m (t/c) + 100 (t/c)^4] [1.34 M^0.18 cos(L_m)^0.28], Mach held at >= 0.2 (see constants). */
export function formFactorLifting(tc: number, xMaxThickness: number, machNo: number, sweepMaxThicknessRad: number): number {
  const m = Math.max(machNo, FORM_FACTOR_MACH_FLOOR);
  return (1 + (0.6 / xMaxThickness) * tc + 100 * tc ** 4) * (1.34 * m ** 0.18 * Math.cos(sweepMaxThicknessRad) ** 0.28);
}

/** Body form factor 0.9 + 5/f^1.5 + f/400 with f the fineness ratio (Raymer ch. 12.5). */
export function formFactorBody(fineness: number): number {
  return 0.9 + 5 / fineness ** 1.5 + fineness / 400;
}

/**
 * Oswald span efficiency (Raymer ch. 12.6): straight wings e = 1.78 (1 - 0.045 A^0.68) - 0.64;
 * swept (LE sweep > 30 deg) e = 4.61 (1 - 0.045 A^0.68) cos(L_LE)^0.15 - 3.1. Blended linearly
 * between 25 and 35 degrees to avoid a jump; clamped to 0.5-0.95.
 */
export function oswaldEfficiency(ar: number, sweepLeDeg: number): number {
  const base = 1 - 0.045 * ar ** 0.68;
  const straight = 1.78 * base - 0.64;
  const swept = 4.61 * base * Math.cos(degToRad(Math.min(Math.abs(sweepLeDeg), 89))) ** 0.15 - 3.1;
  const s = Math.abs(sweepLeDeg);
  const t = s <= 25 ? 0 : s >= 35 ? 1 : (s - 25) / 10;
  const e = (1 - t) * straight + t * swept;
  return Math.min(OSWALD_MAX, Math.max(OSWALD_MIN, e));
}

/** Induced drag coefficient CL^2 / (pi e A) (Anderson, Fundamentals of Aerodynamics, ch. 5). */
export function inducedDragCoefficient(cl: number, e: number, ar: number): number {
  return (cl * cl) / (Math.PI * e * ar);
}

// ---------- Stopped propellers and pods ----------

/**
 * Drag area D/q (m^2) of one stopped propeller sitting edge-on to the flow: Cd_flat x blades x
 * (0.85 R) x h x 2/pi, with h = c sin(theta) + t cos(theta) the projected blade height at 0.75 R,
 * c = 0.08 D, t = 0.12 c, theta = atan(pitch / (0.75 pi D)). See constants.ts for sources.
 */
export function stoppedPropDragArea(diameterMm: number, pitchMm: number, blades: number): number {
  const D = diameterMm / 1000;
  const c = PROP_MEAN_CHORD_FRACTION * D;
  const theta = Math.atan(pitchMm / 1000 / (0.75 * Math.PI * D));
  const h = c * Math.sin(theta) + PROP_BLADE_THICKNESS_RATIO * c * Math.cos(theta);
  return CD_FLAT_PLATE * Math.max(1, blades) * PROP_BLADE_RADIUS_FRACTION * (D / 2) * h * PROP_AZIMUTH_AVERAGE;
}

/** Motor can diameter, m, from motor mass, g (cylinder of height 0.6 d, mean density 3500 kg/m^3). */
export function motorCanDiameter(massG: number): number {
  const v = massG / 1000 / MOTOR_CAN_DENSITY;
  return Math.cbrt(v / ((Math.PI / 4) * MOTOR_CAN_HEIGHT_RATIO));
}

// ---------- Build-up ----------

export interface DragContext {
  p: ResolvedParameters;
  g: Geometry;
  atm: Atmosphere;
  speed: number;
  scale: Scale;
  liftMotorMassG: number;
}

/** Parasite drag component build-up, CD0 = sum(Cf FF Q S_wet) / S_ref + drag areas / S_ref, plus leakage (Raymer ch. 12.5). */
export function parasiteDrag(ctx: DragContext): { items: DragItem[]; cd0: number } {
  const { p, g, atm, speed, scale } = ctx;
  const S = g.wing.area_m2;
  const M = mach(atm, speed);
  const lam = LAMINAR_FRACTION[scale];
  const k = SURFACE_ROUGHNESS_M[scale];
  const items: DragItem[] = [];
  const friction = (key: string, label: string, lengthM: number, wet: number, lamFrac: number, ff: number, q: number) => {
    if (!(wet > 0) || !(lengthM > 0)) return;
    const re = reynolds(atm, speed, lengthM);
    const cf = skinFriction(re, M, lamFrac, lengthM, k);
    const area = cf * ff * q * wet;
    items.push({
      key,
      label,
      drag_area_m2: area,
      cd0: area / S,
      reynolds: re,
      cf,
      form_factor: ff,
      interference: q,
      wetted_area_m2: wet,
      source: 'Raymer ch. 12.5 component build-up: flat-plate skin friction (laminar fraction weighted) x form factor x interference x wetted area.',
    });
  };
  const w = g.wing;
  friction('wing', 'Wing', w.mac_mm / 1000, w.wetted_area_m2, lam.wing,
    formFactorLifting(w.thickness_ratio, w.x_max_thickness, M, degToRad(w.sweep_max_thickness_deg)), INTERFERENCE.wing);
  const t = g.tail;
  const qTail = t.type === 'v_tail' || t.type === 'inverted_v' ? INTERFERENCE.tail_v : t.type === 'twin_boom_h' ? INTERFERENCE.tail_h : INTERFERENCE.tail_conventional;
  friction('tail', 'Tail', t.chord_mm / 1000, t.wetted_area_m2, lam.tail, formFactorLifting(t.thickness_ratio, t.x_max_thickness, M, 0), qTail);
  friction('fuselage', 'Fuselage (with nose bay)', g.fuselage.length_mm / 1000, g.fuselage.wetted_area_m2, lam.body,
    formFactorBody(g.fuselage.fineness_ratio), INTERFERENCE.fuselage);
  const boomCount = Math.max(1, Math.round(p.booms.count));
  const d = p.booms.diameter_mm;
  friction('booms', 'Motor booms', p.booms.length_mm / 1000, (boomCount * Math.PI * d * p.booms.length_mm) / 1e6, lam.body,
    formFactorBody(p.booms.length_mm / d), INTERFERENCE.boom);
  if (t.support_length_mm > 0) {
    const n = t.support_kind === 'booms' ? boomCount : 1;
    friction('tail_support', t.support_kind === 'booms' ? 'Boom extensions' : 'Tail boom', t.support_length_mm / 1000,
      (n * Math.PI * d * t.support_length_mm) / 1e6, lam.body, formFactorBody(t.support_length_mm / d), INTERFERENCE.boom);
  }
  const stopped = g.rotors.filter((r) => r.id !== 'pusher' && r.stopped_in_cruise).length;
  const running = g.rotors.filter((r) => r.id !== 'pusher' && !r.stopped_in_cruise).length;
  if (stopped > 0) {
    const a = stopped * stoppedPropDragArea(p.propulsion.prop_diameter_mm, p.propulsion.prop_pitch_mm, p.propulsion.prop_blades);
    items.push({
      key: 'stopped_props',
      label: `Stopped lift propellers (${stopped})`,
      drag_area_m2: a,
      cd0: a / S,
      source: 'Blades as flat plates edge-on (Cd 1.2, Hoerner), projected height at 0.75 R, averaged over the stop position (2/pi). Engine method, +/-50 %.',
    });
  }
  const dm = motorCanDiameter(ctx.liftMotorMassG);
  if (dm > 0) {
    const side = stopped * CD_MOTOR_CAN_SIDE * dm * MOTOR_CAN_HEIGHT_RATIO * dm;
    const front = running * CD_MOTOR_CAN_FRONT * (Math.PI / 4) * dm * dm;
    items.push({
      key: 'motor_pods',
      label: 'Lift motors and mounts',
      drag_area_m2: side + front,
      cd0: (side + front) / S,
      source: 'Stopped motor cans as short cylinders side-on (Cd 0.8) and running tilted motors face-on behind the spinner (Cd 0.3), Hoerner; can size from motor mass.',
    });
  }
  const gear = p.landing_gear;
  if (gear.type !== 'none' && gear.height_mm > 0) {
    const strutD = Math.max(4, GEAR_STRUT_DIAMETER_FRACTION * gear.height_mm) / 1000;
    const struts = 4;
    const a = CD_ROUND_STRUT * struts * strutD * (gear.height_mm / 1000);
    items.push({
      key: 'landing_gear',
      label: `Landing gear (${gear.type})`,
      drag_area_m2: a,
      cd0: a / S,
      source: 'Four round struts of 6 % of the gear height (min 4 mm), Cd 1.0 on frontal area (Hoerner, subcritical cylinder).',
    });
  }
  const sum = items.reduce((s, i) => s + i.drag_area_m2, 0);
  const leak = LEAKAGE_PROTUBERANCE_FRACTION * sum;
  items.push({
    key: 'leakage',
    label: 'Seams, hatches and protuberances',
    drag_area_m2: leak,
    cd0: leak / S,
    source: 'Leakage and protuberance allowance, 10 % of the build-up (Raymer ch. 12.5: 2-10 %).',
  });
  return { items, cd0: (sum + leak) / S };
}

// ---------- Static stability (Nelson ch. 2; Etkin and Reid ch. 2) ----------

/** Downwash gradient at the tail: d(eps)/d(alpha) = 2 CLa_w / (pi A) (Nelson ch. 2). */
export function downwashGradient(clAlphaWing: number, ar: number): number {
  return (2 * clAlphaWing) / (Math.PI * ar);
}

/** Apparent-mass factor (k2 - k1) of a prolate spheroid against fineness ratio (Lamb; chart in Nelson ch. 2, Multhopp's method). */
export function munkFactor(fineness: number): number {
  const table: [number, number][] = [
    [1, 0],
    [2, 0.49],
    [3, 0.68],
    [4, 0.78],
    [5, 0.83],
    [6, 0.87],
    [8, 0.92],
    [10, 0.94],
    [15, 0.97],
    [20, 0.98],
  ];
  if (!(fineness > 1)) return 0;
  if (fineness >= 20) return 0.98;
  for (let i = 0; i < table.length - 1; i++) {
    const [f0, k0] = table[i];
    const [f1, k1] = table[i + 1];
    if (fineness <= f1) return k0 + ((fineness - f0) / (f1 - f0)) * (k1 - k0);
  }
  return 0.98;
}

/**
 * Fuselage pitching-moment slope dCm/dalpha per radian (positive = destabilising), Multhopp's
 * strip method as given in Nelson ch. 2: (k2 - k1) (pi/2) / (S c) x sum(w_f^2 x upwash factor x dx).
 * Ahead of the wing the upwash factor is 1 + c_r CLa / (4 pi x) (bound-vortex upwash at distance x
 * ahead of the root quarter chord: engine simplification of Nelson's chart); over the wing root it is
 * skipped; behind the wing it is (x / l_h)(1 - deps/dalpha) with x from the root trailing edge.
 */
export function fuselageMomentSlope(g: Geometry, clAlphaWing: number, depsDalpha: number): number {
  const f = g.fuselage;
  const st = f.stations;
  if (st.length < 2) return 0;
  const S = g.wing.area_m2;
  const c = g.wing.mac_mm / 1000;
  const k = munkFactor(f.fineness_ratio);
  const rootLe = g.wing.root_le[0];
  const rootTe = rootLe + g.wing.root_chord_mm;
  const rootC4 = rootLe + 0.25 * g.wing.root_chord_mm;
  const lh = Math.max(1, g.tail.quarter_chord_x_mm - rootTe);
  const widthAt = (x: number): number => {
    for (let i = 0; i < st.length - 1; i++) {
      if (x >= st[i].x_mm && x <= st[i + 1].x_mm) {
        const t = (x - st[i].x_mm) / Math.max(1e-9, st[i + 1].x_mm - st[i].x_mm);
        return st[i].width_mm + t * (st[i + 1].width_mm - st[i].width_mm);
      }
    }
    return 0;
  };
  const N = 40;
  const L = f.length_mm;
  let sum = 0;
  for (let i = 0; i < N; i++) {
    const x = ((i + 0.5) * L) / N;
    const dx = L / N;
    let factor: number;
    if (x < rootLe) factor = 1 + (g.wing.root_chord_mm * clAlphaWing) / (4 * Math.PI * Math.max(rootC4 - x, 0.25 * g.wing.root_chord_mm));
    else if (x <= rootTe) factor = 0;
    else factor = ((x - rootTe) / lh) * (1 - depsDalpha);
    const wf = widthAt(x) / 1000;
    sum += wf * wf * factor * (dx / 1000);
  }
  return (k * (Math.PI / 2) * sum) / (S * c);
}

export interface NeutralPointResult {
  x_np_mm: number;
  clAlphaWing: number;
  clAlphaTail: number;
  depsDalpha: number;
  cmAlphaFuselage: number;
}

/**
 * Stick-fixed neutral point (Etkin and Reid ch. 2; Nelson ch. 2): the lift-weighted position of the
 * wing and tail aerodynamic centres, moved forward by the fuselage moment:
 *   x_np = (a_w x_acw + a_t' x_act) / (a_w + a_t') - c Cma_f / (a_w + a_t'),
 *   a_t' = eta_t a_t (S_t,pitch / S)(1 - deps/dalpha).
 */
export function neutralPoint(g: Geometry, wingClAlpha: number, tailSectionClAlpha: number, machNo: number): NeutralPointResult {
  const aw = wingClAlpha;
  const at = liftCurveSlope(g.tail.aspect_ratio, 0, tailSectionClAlpha, machNo, 1);
  const de = downwashGradient(aw, g.wing.aspect_ratio);
  const atEff = TAIL_EFFICIENCY * at * (g.tail.pitch_effective_area_m2 / g.wing.area_m2) * (1 - de);
  const cmf = fuselageMomentSlope(g, aw, de);
  const total = aw + atEff;
  const x = (aw * g.wing.ac_x_mm + atEff * g.tail.quarter_chord_x_mm) / total - (g.wing.mac_mm * cmf) / total;
  return { x_np_mm: x, clAlphaWing: aw, clAlphaTail: at, depsDalpha: de, cmAlphaFuselage: cmf };
}

/** Volume of the fuselage loft, m^3 (for reference and the Phase 3 port). */
export function fuselageVolume(g: Geometry): number {
  const st = g.fuselage.stations;
  let v = 0;
  for (let i = 0; i < st.length - 1; i++) {
    const a = sectionArea(g.fuselage.cross_section, st[i].width_mm, st[i].height_mm);
    const b = sectionArea(g.fuselage.cross_section, st[i + 1].width_mm, st[i + 1].height_mm);
    v += ((a + b) / 2) * (st[i + 1].x_mm - st[i].x_mm);
  }
  return v / 1e9;
}

/** Status notes about airfoil data quality. */
export function polarStatuses(id: string, which: 'wing' | 'tail', lookup: PolarLookup, re: number): Status[] {
  if (lookup.quality === 'generic') {
    return [{
      key: `airfoil.${which}_generic`,
      label: `${which === 'wing' ? 'Wing' : 'Tail'} airfoil data`,
      level: 'warn',
      message: `No polar data for airfoil "${id}" yet (still loading, or not in the library), so typical values are used. Pick a library airfoil to get XFOIL-based numbers.`,
    }];
  }
  if (lookup.quality === 'clamped') {
    return [{
      key: `airfoil.${which}_re_range`,
      label: `${which === 'wing' ? 'Wing' : 'Tail'} Reynolds number`,
      level: 'info',
      message: `The ${which} works at a Reynolds number of about ${Math.round(re / 1000)}k, outside the airfoil table (${Math.round(lookup.reMin / 1000)}k-${Math.round(lookup.reMax / 1000)}k); the nearest table values are used.`,
    }];
  }
  return [];
}
