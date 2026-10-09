/**
 * Mass build-up, battery, motor sizing and balance (docs/ENGINE.md "Mass and balance").
 *
 * Every component carries its mass, its x position, a relative uncertainty, a source and a
 * plain-language explanation. Motors are sized for the hover thrust-to-weight minimum, which
 * depends on the total mass, so the build-up is iterated to a fixed point (at most 20 passes).
 */

import type { Mission, Scale, Settings } from '../api/types';
import type { Atmosphere } from './atmosphere';
import {
  AREAL_DENSITY,
  AREAL_DENSITY_UNCERTAINTY,
  ALLOWANCE_UNCERTAINTY,
  CARBON_TUBE_DENSITY,
  CARBON_TUBE_MIN_WALL_M,
  CARBON_TUBE_UNCERTAINTY,
  CARBON_TUBE_WALL_FRACTION,
  CELL_NOMINAL_V,
  CONTROL_SERVO_COUNT,
  CONTROL_SERVO_FIXED_G,
  CONTROL_SERVO_PER_TAKEOFF_G,
  ELLIPTIC_CENTROID,
  ESC_CURRENT_MARGIN,
  ESC_MASS_FIXED_G,
  ESC_MASS_PER_A,
  ESC_MASS_UNCERTAINTY,
  ETA_MOTOR_MAX,
  FIGURE_OF_MERIT_MAX,
  FIGURE_OF_MERIT_PUSHER_STATIC,
  G0,
  HOVER_DOWNLOAD_FRACTION,
  LANDING_GEAR_FRACTION,
  MASS_MAX_ITERATIONS,
  MASS_TOLERANCE_G,
  MOTOR_MASS_COEFF,
  MOTOR_MASS_EXP,
  MOTOR_MASS_UNCERTAINTY,
  MOTOR_MOUNT_FRACTION,
  PACK_SPECIFIC_ENERGY_UNCERTAINTY,
  PACK_SPECIFIC_ENERGY_WH_PER_KG,
  PROP_MASS_EXP,
  PROP_MASS_REF_DIAMETER_M,
  PROP_MASS_REF_G,
  PROP_MASS_UNCERTAINTY,
  PUSHER_THRUST_TO_WEIGHT,
  SPAR_ALLOWABLE_STRESS_PA,
  SPAR_DEPTH_FRACTION,
  SPAR_EXTRA_FACTOR,
  SPAR_TUBE_THICKNESS_FRACTION,
  SPAR_ULTIMATE_LOAD_FACTOR,
  TILT_MECH_FIXED_G,
  TILT_MECH_FRACTION,
} from './constants';
import type { ResolvedParameters } from './geometry';
import { qRel, qRange } from './quantity';
import type { Geometry, MassComponent, MassResult, Quantity } from './types';
import { clamp } from './units';

// ---------- Statistical relations (each stated in constants.ts and docs/ENGINE.md) ----------

/** Carbon tube mass per metre, g/m, from outer diameter in mm: rho pi (D - t) t, t = max(1 mm, 0.05 D). */
export function carbonTubeMassPerM(diameterMm: number): number {
  const d = diameterMm / 1000;
  const t = Math.max(CARBON_TUBE_MIN_WALL_M, CARBON_TUBE_WALL_FRACTION * d);
  if (d <= t) return CARBON_TUBE_DENSITY * Math.PI * (d / 2) ** 2 * 1000; // solid rod
  return CARBON_TUBE_DENSITY * Math.PI * (d - t) * t * 1000;
}

/** Motor mass in g from maximum continuous electrical power in W: 1.2 P^0.73 (statistical fit). */
export function motorMassG(maxPowerW: number): number {
  return maxPowerW > 0 ? MOTOR_MASS_COEFF * maxPowerW ** MOTOR_MASS_EXP : 0;
}

/** ESC mass in g from its continuous current rating in A: 1.0 I + 5 (statistical fit). */
export function escMassG(ratedCurrentA: number): number {
  return ratedCurrentA > 0 ? ESC_MASS_PER_A * ratedCurrentA + ESC_MASS_FIXED_G : 0;
}

/** Propeller mass in g from diameter in mm and blade count: 20 (D / 0.3048 m)^2.5 x blades / 2 (statistical fit). */
export function propMassG(diameterMm: number, blades: number): number {
  if (diameterMm <= 0) return 0;
  return PROP_MASS_REF_G * (diameterMm / 1000 / PROP_MASS_REF_DIAMETER_M) ** PROP_MASS_EXP * (Math.max(1, blades) / 2);
}

/** Momentum-theory ideal power, W, for thrust T (N) on a disc of area A (m^2): T^1.5 / sqrt(2 rho A) (Leishman ch. 2). */
export function idealHoverPower(thrustN: number, discAreaM2: number, rho: number): number {
  if (thrustN <= 0 || discAreaM2 <= 0) return 0;
  return thrustN ** 1.5 / Math.sqrt(2 * rho * discAreaM2);
}

/** Pack nominal voltage, capacity and energy. Capacity = capacity_mah x cells_parallel. */
export function packElectrics(p: ResolvedParameters) {
  const b = p.battery;
  const voltage = b.cells_series * CELL_NOMINAL_V[b.chemistry];
  const capacityAh = (b.capacity_mah / 1000) * b.cells_parallel;
  const energyWh = voltage * capacityAh;
  const massG = (energyWh / PACK_SPECIFIC_ENERGY_WH_PER_KG[b.chemistry]) * 1000;
  return { voltage, capacityAh, energyWh, massG };
}

export interface MassContext {
  p: ResolvedParameters;
  g: Geometry;
  mission: Mission;
  settings: Settings;
  atm: Atmosphere;
}

export interface MassSolution {
  result: MassResult;
  /** Components at minimum payload (same as result.components except the payload row). */
  componentsMin: MassComponent[];
  /** Front-pair share of hover thrust at maximum and minimum payload (may be outside 0-1 when the CG is outside the rotors). */
  frontShareMax: number;
  frontShareMin: number;
  cgMaxX: number;
  cgMinX: number;
  cgMaxSigma: number;
  cgMinSigma: number;
  /** Relative 1-sigma-like mass uncertainty at maximum payload (correlated structure + independent rest). */
  massRel: number;
  totalMaxG: number;
  totalMinG: number;
  liftMotorMassG: number;
  pusherMotorMassG: number;
  pack: ReturnType<typeof packElectrics>;
}

/** Mass-weighted x position and its uncertainty (structure items correlated, the rest independent). */
export function centreOfGravity(components: MassComponent[]): { x: number; sigma: number; total: number } {
  let m = 0;
  let mx = 0;
  for (const c of components) {
    m += c.mass_g;
    mx += c.mass_g * c.x_mm;
  }
  const x = m > 0 ? mx / m : NaN;
  let structLinear = 0;
  let indep = 0;
  for (const c of components) {
    const d = (c.x_mm - x) * c.uncertainty * c.mass_g;
    if (c.group === 'structure') structLinear += d;
    else indep += d * d;
  }
  return { x, sigma: m > 0 ? Math.sqrt(structLinear * structLinear + indep) / m : NaN, total: m };
}

/** Mass uncertainty in g: structure components add linearly (shared method), the rest by root-sum-square. */
export function massSigma(components: MassComponent[]): number {
  let structLinear = 0;
  let indep = 0;
  for (const c of components) {
    const s = c.uncertainty * c.mass_g;
    if (c.group === 'structure') structLinear += s;
    else indep += s * s;
  }
  return Math.sqrt(structLinear * structLinear + indep);
}

/** Front-pair share of hover thrust from the moment balance of the two motor pairs about the CG (statics). */
export function frontThrustShare(cgX: number, frontX: number, rearX: number): number {
  const span = rearX - frontX;
  if (!(Math.abs(span) > 1e-9)) return 0.5;
  return (rearX - cgX) / span;
}

function fuselageWettedCentroidX(g: Geometry): number {
  const st = g.fuselage.stations;
  let a = 0;
  let ax = 0;
  for (let i = 0; i < st.length - 1; i++) {
    const dx = st[i + 1].x_mm - st[i].x_mm;
    const seg = ((st[i].perimeter_mm + st[i + 1].perimeter_mm) / 2) * dx;
    a += seg;
    ax += seg * (st[i].x_mm + st[i + 1].x_mm) / 2;
  }
  return a > 0 ? ax / a : g.fuselage.length_mm * 0.45;
}

interface Build {
  components: MassComponent[];
  liftMotorMaxPowerW: number;
  liftMotorMassG: number;
  liftMotorMaxThrustN: number;
  pusherMaxPowerW: number;
  pusherMotorMassG: number;
}

/** One pass of the build-up for a take-off mass guess (kg, maximum payload) and a CG guess (mm). */
function buildComponents(ctx: MassContext, mtowKg: number, cgGuessX: number): Build {
  const { p, g, mission, settings, atm } = ctx;
  const scale: Scale = mission.scale === 'final' ? 'final' : 'prototype';
  const dens = AREAL_DENSITY[scale];
  const densU = AREAL_DENSITY_UNCERTAINTY[scale];
  const pack = packElectrics(p);
  const W = mtowKg * G0;
  const comps: MassComponent[] = [];
  const add = (c: MassComponent) => {
    if (Number.isFinite(c.mass_g) && c.mass_g > 0) comps.push(c);
  };
  const scaleWord = scale === 'final' ? 'carbon-fibre composite' : '3D-printed lightweight (foaming) PLA';

  // ----- Structure -----
  const wing = g.wing;
  add({
    key: 'wing_structure',
    label: 'Wing structure (skins, ribs)',
    group: 'structure',
    mass_g: wing.area_m2 * dens.wing * 1000,
    uncertainty: densU.wing,
    x_mm: wing.mac_x_le_mm + 0.5 * wing.mac_mm,
    source: `Areal density ${dens.wing} kg/m² of wing area for ${scaleWord} (estimate; ${scale === 'prototype' ? 'placeholder to calibrate with Phase 6 built weights' : 'composite lay-up estimate'}).`,
    explain: 'The wing shell and ribs, estimated from the wing area times a typical weight per square metre for this kind of construction.',
  });
  const tRootMm = wing.thickness_ratio * wing.root_chord_mm;
  const sparX = wing.mac_x_le_mm + wing.x_max_thickness * wing.mac_mm;
  if (scale === 'prototype') {
    const dSpar = clamp(Math.floor(SPAR_TUBE_THICKNESS_FRACTION * tRootMm), 8, 30);
    add({
      key: 'wing_spar',
      label: `Wing spar (carbon tube ${dSpar} mm, full span)`,
      group: 'structure',
      mass_g: carbonTubeMassPerM(dSpar) * (wing.span_mm / 1000),
      uncertainty: CARBON_TUBE_UNCERTAINTY,
      x_mm: sparX,
      source: 'Carbon tube mass per metre from diameter (tube geometry, 1550 kg/m³, wall max(1 mm, 5 % of D)); diameter 70 % of the root thickness.',
      explain: 'The carbon tube running through the wing that carries the bending load. Its size is a starting guess until the Phase 3 structure check sizes it.',
    });
  } else {
    const semiM = wing.span_mm / 2000;
    const moment = SPAR_ULTIMATE_LOAD_FACTOR * (W / 2) * ELLIPTIC_CENTROID * semiM;
    const depthM = (SPAR_DEPTH_FRACTION * tRootMm) / 1000;
    const capArea = depthM > 0 ? moment / (SPAR_ALLOWABLE_STRESS_PA * depthM) : 0;
    const massKg = 2 * 2 * CARBON_TUBE_DENSITY * capArea * (semiM / 3) * SPAR_EXTRA_FACTOR;
    add({
      key: 'wing_spar',
      label: 'Wing spar caps (carbon, bending-sized)',
      group: 'structure',
      mass_g: massKg * 1000,
      uncertainty: 0.35,
      x_mm: sparX,
      source: 'Spar caps sized for root bending at an ultimate load factor of 6 (4 x 1.5) with an elliptic lift distribution, 600 MPa allowable, x1.3 for webs and joints (estimate).',
      explain: 'The carbon spar caps that carry the wing bending load, sized from the weight and span. A bigger, heavier aircraft needs a stronger spar.',
    });
  }
  add({
    key: 'fuselage_structure',
    label: 'Fuselage shell and frames',
    group: 'structure',
    mass_g: g.fuselage.wetted_area_m2 * dens.fuselage * 1000,
    uncertainty: densU.fuselage,
    x_mm: fuselageWettedCentroidX(g),
    source: `Areal density ${dens.fuselage} kg/m² of fuselage surface for ${scaleWord} (estimate${scale === 'prototype' ? '; placeholder to calibrate with Phase 6 built weights' : ''}).`,
    explain: 'The fuselage skin, internal frames and mounts, estimated from its surface area.',
  });
  add({
    key: 'tail_structure',
    label: 'Tail surfaces',
    group: 'structure',
    mass_g: g.tail.planform_area_m2 * dens.tail * 1000,
    uncertainty: densU.tail,
    x_mm: g.tail.quarter_chord_x_mm + 0.25 * g.tail.chord_mm,
    source: `Areal density ${dens.tail} kg/m² of tail panel area for ${scaleWord} (estimate).`,
    explain: 'The tail panels that keep the aircraft pointing straight and level, estimated from their area.',
  });
  const boomPerM = carbonTubeMassPerM(p.booms.diameter_mm);
  const boomCount = Math.max(1, Math.round(p.booms.count));
  add({
    key: 'booms',
    label: `Motor booms (${boomCount} carbon tubes, ${p.booms.diameter_mm} mm)`,
    group: 'structure',
    mass_g: boomCount * boomPerM * (p.booms.length_mm / 1000),
    uncertainty: CARBON_TUBE_UNCERTAINTY,
    x_mm: g.booms[0].start[0] + p.booms.length_mm / 2,
    source: 'Carbon tube mass per metre from diameter (tube geometry, 1550 kg/m³, wall max(1 mm, 5 % of D)).',
    explain: 'The carbon tubes that hold the four lift motors.',
  });
  if (g.tail.support_length_mm > 0) {
    const n = g.tail.support_kind === 'booms' ? boomCount : 1;
    const startX = g.tail.support_kind === 'booms' ? g.booms[0].end[0] : g.fuselage.length_mm;
    add({
      key: 'tail_support',
      label: g.tail.support_kind === 'booms' ? 'Boom extensions to the tail' : 'Tail boom',
      group: 'structure',
      mass_g: n * boomPerM * (g.tail.support_length_mm / 1000),
      uncertainty: CARBON_TUBE_UNCERTAINTY,
      x_mm: startX + g.tail.support_length_mm / 2,
      source: 'Carbon tube of the boom diameter; mass per metre from tube geometry.',
      explain: 'Tube needed to carry the tail behind the fuselage or booms.',
    });
  }

  // ----- Lift propulsion: sized for the hover thrust-to-weight minimum on the more heavily loaded pair -----
  // The lightest camera moves the CG aft; size for the worse of the two payload cases.
  const totalG = mtowKg * 1000;
  const dPayload = mission.payload_max_g - mission.payload_min_g;
  const cgMinGuess = totalG - dPayload > 0 ? (totalG * cgGuessX - dPayload * (p.nose_bay.length_mm / 2)) / (totalG - dPayload) : cgGuessX;
  const shareMax = clamp(frontThrustShare(cgGuessX, g.front_rotor_x_mm, g.rear_rotor_x_mm), 0, 1);
  const shareMin = clamp(frontThrustShare(cgMinGuess, g.front_rotor_x_mm, g.rear_rotor_x_mm), 0, 1);
  const worstShare = Math.max(shareMax, 1 - shareMax, shareMin, 1 - shareMin);
  const tw = settings.checks.hover_thrust_to_weight_min;
  const D = p.propulsion.prop_diameter_mm;
  const discArea = Math.PI * (D / 2000) ** 2;
  const motorMaxThrust = (tw * W * (1 + HOVER_DOWNLOAD_FRACTION) * worstShare) / 2;
  const motorMaxPower = idealHoverPower(motorMaxThrust, discArea, atm.rho) / FIGURE_OF_MERIT_MAX / ETA_MOTOR_MAX;
  const motorG = motorMassG(motorMaxPower);
  const escG = escMassG((motorMaxPower / pack.voltage) * ESC_CURRENT_MARGIN);
  const propG = propMassG(D, p.propulsion.prop_blades);
  const xf = g.front_rotor_x_mm;
  const xr = g.rear_rotor_x_mm;
  for (const [pos, x] of [['front', xf], ['rear', xr]] as const) {
    add({
      key: `motors_${pos}`,
      label: `Lift motors, ${pos} pair`,
      group: 'propulsion',
      mass_g: 2 * motorG,
      uncertainty: MOTOR_MASS_UNCERTAINTY,
      x_mm: x,
      source: `Statistical motor mass 1.2 x P^0.73 g for ${Math.round(motorMaxPower)} W each, sized so the four motors give ${tw} x the weight (momentum theory, figure of merit 0.55 at full power).`,
      explain: 'Two of the four lift motors. They are sized so that, together, they can lift the aircraft with the thrust-to-weight margin set in Settings.',
    });
    add({
      key: `escs_${pos}`,
      label: `ESCs, ${pos} pair`,
      group: 'propulsion',
      mass_g: 2 * escG,
      uncertainty: ESC_MASS_UNCERTAINTY,
      x_mm: x,
      source: 'Statistical ESC mass 1.0 g per amp of rating + 5 g; rating 1.2 x the full-power current.',
      explain: 'The speed controllers that drive the motors, sized for the full-power current.',
    });
    add({
      key: `props_${pos}`,
      label: `Lift propellers, ${pos} pair`,
      group: 'propulsion',
      mass_g: 2 * propG,
      uncertainty: PROP_MASS_UNCERTAINTY,
      x_mm: x,
      source: 'Statistical propeller mass 20 g x (D / 305 mm)^2.5 per two blades.',
      explain: 'Two lift propellers, estimated from their diameter.',
    });
    add({
      key: `mounts_${pos}`,
      label: `Motor mounts, ${pos} pair`,
      group: 'structure',
      mass_g: 2 * MOTOR_MOUNT_FRACTION * motorG,
      uncertainty: 0.5,
      x_mm: x,
      source: 'Mount mass 20 % of motor mass (estimate).',
      explain: 'Brackets that fix the motors to the booms.',
    });
  }
  if (p.layout !== 'quad_pusher') {
    const perSide = TILT_MECH_FIXED_G + TILT_MECH_FRACTION * (motorG + propG);
    add({
      key: 'tilt_mechanism',
      label: 'Tilt mechanism (2 servos and hinges)',
      group: 'systems',
      mass_g: 2 * perSide,
      uncertainty: 0.5,
      x_mm: g.tilt_hinge_x_mm ?? (p.layout === 'front_tilt' ? xf : xr),
      source: 'Per side 25 g + 25 % of the tilted motor and propeller mass (estimate).',
      explain: 'The servos, hinges and bearings that tilt the motors forward for wing flight.',
    });
  }
  let pusherPower = 0;
  let pusherMotor = 0;
  if (p.layout === 'quad_pusher') {
    const Dp = p.pusher.prop_diameter_mm;
    const area = Math.PI * (Dp / 2000) ** 2;
    pusherPower = idealHoverPower(PUSHER_THRUST_TO_WEIGHT * W, area, atm.rho) / FIGURE_OF_MERIT_PUSHER_STATIC / ETA_MOTOR_MAX;
    pusherMotor = motorMassG(pusherPower);
    const px = p.pusher.x_mm;
    add({
      key: 'pusher_motor',
      label: 'Pusher motor',
      group: 'propulsion',
      mass_g: pusherMotor,
      uncertainty: MOTOR_MASS_UNCERTAINTY,
      x_mm: px,
      source: `Statistical motor mass for ${Math.round(pusherPower)} W, sized for a static thrust of ${PUSHER_THRUST_TO_WEIGHT} x the weight (momentum theory, figure of merit 0.6).`,
      explain: 'The separate motor that pushes the aircraft in wing flight. It needs enough thrust to accelerate through the transition.',
    });
    add({
      key: 'pusher_esc',
      label: 'Pusher ESC',
      group: 'propulsion',
      mass_g: escMassG((pusherPower / pack.voltage) * ESC_CURRENT_MARGIN),
      uncertainty: ESC_MASS_UNCERTAINTY,
      x_mm: px,
      source: 'Statistical ESC mass 1.0 g per amp + 5 g.',
      explain: 'The speed controller for the pusher motor.',
    });
    add({
      key: 'pusher_prop',
      label: 'Pusher propeller',
      group: 'propulsion',
      mass_g: propMassG(Dp, 2),
      uncertainty: PROP_MASS_UNCERTAINTY,
      x_mm: px,
      source: 'Statistical propeller mass 20 g x (D / 305 mm)^2.5.',
      explain: 'The pusher propeller.',
    });
    add({
      key: 'pusher_mount',
      label: 'Pusher motor mount',
      group: 'structure',
      mass_g: MOTOR_MOUNT_FRACTION * pusherMotor,
      uncertainty: 0.5,
      x_mm: px,
      source: 'Mount mass 20 % of motor mass (estimate).',
      explain: 'The bracket that holds the pusher motor.',
    });
  }

  // ----- Systems, energy, payload -----
  const servoEach = CONTROL_SERVO_FIXED_G + CONTROL_SERVO_PER_TAKEOFF_G * mtowKg * 1000;
  add({
    key: 'servos_wing',
    label: 'Aileron servos (2)',
    group: 'systems',
    mass_g: (CONTROL_SERVO_COUNT / 2) * servoEach,
    uncertainty: 0.4,
    x_mm: wing.mac_x_le_mm + 0.7 * wing.mac_mm,
    source: 'Each 6 g + 0.25 % of take-off mass (estimate: 9 g class at 2.5 kg, 60-70 g class at 24 kg).',
    explain: 'Servos that move the ailerons.',
  });
  add({
    key: 'servos_tail',
    label: 'Tail servos (2)',
    group: 'systems',
    mass_g: (CONTROL_SERVO_COUNT / 2) * servoEach,
    uncertainty: 0.4,
    x_mm: g.tail.le_x_mm,
    source: 'Each 6 g + 0.25 % of take-off mass (estimate).',
    explain: 'Servos that move the tail control surfaces.',
  });
  add({
    key: 'avionics',
    label: 'Avionics allowance',
    group: 'systems',
    mass_g: p.allowances.avionics_g,
    uncertainty: ALLOWANCE_UNCERTAINTY,
    x_mm: p.wing.x_le_mm,
    source: 'Owner allowance (design parameter allowances.avionics_g), placed under the wing leading edge; replaced by real parts in Phase 4.',
    explain: 'Autopilot, GPS, receiver, telemetry radio and power module, as an allowance until real parts are chosen.',
  });
  const gearType = p.landing_gear.type;
  add({
    key: 'landing_gear',
    label: `Landing gear (${gearType})`,
    group: 'structure',
    mass_g: LANDING_GEAR_FRACTION[gearType] * mtowKg * 1000,
    uncertainty: 0.5,
    x_mm: (xf + xr) / 2,
    source: `${LANDING_GEAR_FRACTION[gearType] * 100} % of take-off mass for ${gearType} (estimate).`,
    explain: 'The skids or legs the aircraft stands and lands on.',
  });
  add({
    key: 'battery',
    label: `Battery ${p.battery.cells_series}S${p.battery.cells_parallel}P ${p.battery.chemistry === 'lipo' ? 'LiPo' : 'Li-ion'}`,
    group: 'energy',
    mass_g: pack.massG,
    uncertainty: PACK_SPECIFIC_ENERGY_UNCERTAINTY,
    x_mm: p.battery.x_mm,
    source: `Pack energy ${pack.energyWh.toFixed(0)} Wh at ${PACK_SPECIFIC_ENERGY_WH_PER_KG[p.battery.chemistry]} Wh/kg pack-level specific energy (typical datasheet value).`,
    explain: 'The flight battery, estimated from its energy content and a typical energy per kilogram for this chemistry.',
  });
  // Wiring is a fraction f of the empty mass, which includes the wiring itself: f/(1-f) x the rest.
  const emptyNoWiring = comps.filter((c) => c.group !== 'energy').reduce((s, c) => s + c.mass_g, 0);
  const emptyCg = centreOfGravity(comps.filter((c) => c.group !== 'energy')).x;
  const f = clamp(p.allowances.wiring_fraction, 0, 0.5);
  add({
    key: 'wiring',
    label: 'Wiring, connectors and fasteners',
    group: 'systems',
    mass_g: (f / (1 - f)) * emptyNoWiring,
    uncertainty: ALLOWANCE_UNCERTAINTY,
    x_mm: emptyCg,
    source: `Owner allowance: ${(f * 100).toFixed(1)} % of the empty mass (design parameter allowances.wiring_fraction).`,
    explain: 'Wires, plugs, screws and glue, taken as a fraction of the empty aircraft.',
  });
  add({
    key: 'payload',
    label: 'Nose-bay payload (camera)',
    group: 'payload',
    mass_g: mission.payload_max_g,
    uncertainty: 0,
    x_mm: p.nose_bay.length_mm / 2,
    source: 'Mission payload range, placed at the centre of the nose bay.',
    explain: 'The camera in the nose bay. This row shows the heaviest payload; balance is also checked with the lightest.',
  });

  return {
    components: comps,
    liftMotorMaxPowerW: motorMaxPower,
    liftMotorMassG: motorG,
    liftMotorMaxThrustN: motorMaxThrust,
    pusherMaxPowerW: pusherPower,
    pusherMotorMassG: pusherMotor,
  };
}

/** Iterate the build-up to a fixed point in take-off mass (maximum payload). */
export function solveMass(ctx: MassContext): MassSolution {
  const { mission, g } = ctx;
  let m = mission.target_takeoff_mass_kg > 0 && Number.isFinite(mission.target_takeoff_mass_kg) ? mission.target_takeoff_mass_kg : 2.5;
  let cg = (g.front_rotor_x_mm + g.rear_rotor_x_mm) / 2;
  let build = buildComponents(ctx, m, cg);
  let converged = false;
  let iterations = 0;
  for (let i = 0; i < MASS_MAX_ITERATIONS; i++) {
    iterations = i + 1;
    const c = centreOfGravity(build.components);
    const mNew = c.total / 1000;
    const dG = Math.abs(mNew - m) * 1000;
    const dCg = Math.abs(c.x - cg);
    m = mNew;
    cg = c.x;
    if (!Number.isFinite(m) || m > 1e4) break;
    build = buildComponents(ctx, m, cg);
    if (dG < MASS_TOLERANCE_G && dCg < 0.05) {
      converged = true;
      break;
    }
  }
  const comps = build.components;
  const componentsMin = comps.map((c) => (c.key === 'payload' ? { ...c, mass_g: mission.payload_min_g } : c)).filter((c) => c.mass_g > 0);
  const cMax = centreOfGravity(comps);
  const cMin = centreOfGravity(componentsMin);
  const sigma = massSigma(comps);
  const totalMax = cMax.total;
  const totalMin = cMin.total;
  const sum = (pred: (c: MassComponent) => boolean) => comps.filter(pred).reduce((s, c) => s + c.mass_g, 0);
  const battery = sum((c) => c.group === 'energy');
  const empty = sum((c) => c.group !== 'energy' && c.group !== 'payload');
  const structure = sum((c) => c.group === 'structure');
  const structSigma = comps.filter((c) => c.group === 'structure').reduce((s, c) => s + c.uncertainty * c.mass_g, 0);
  const emptySigma = massSigma(comps.filter((c) => c.group !== 'energy' && c.group !== 'payload'));
  const pack = packElectrics(ctx.p);

  const kg = (gr: number) => gr / 1000;
  const mk = (value: number, sig: number, text: Omit<Quantity, 'value' | 'low' | 'high'>) =>
    qRange(value, value - sig, value + sig, text);

  const result: MassResult = {
    components: comps,
    empty: mk(kg(empty), kg(emptySigma), {
      unit: 'kg',
      label: 'Empty mass',
      explain: 'Everything except the battery and the camera. Every gram here costs endurance, so this is the number to watch while building.',
      source: 'Component build-up (structure from areal densities, motors/ESCs/propellers from statistical relations, owner allowances); range from per-component uncertainties.',
    }),
    battery: qRel(kg(battery), PACK_SPECIFIC_ENERGY_UNCERTAINTY, {
      unit: 'kg',
      label: 'Battery mass',
      explain: `The ${pack.energyWh.toFixed(0)} Wh battery's weight. A bigger battery stores more energy but also adds weight the aircraft has to carry.`,
      source: `Pack energy / pack-level specific energy (${PACK_SPECIFIC_ENERGY_WH_PER_KG[ctx.p.battery.chemistry]} Wh/kg, +/-10 %).`,
    }),
    structure: mk(kg(structure), kg(structSigma), {
      unit: 'kg',
      label: 'Structure mass',
      explain: 'Wing, fuselage, tail, booms, mounts and landing gear. These densities are first guesses; your built weights in Phase 6 will correct them.',
      source: 'Areal and linear densities by scale (docs/ENGINE.md); printed values are placeholders. Uncertainties added linearly because they share one method.',
    }),
    takeoff_max_payload: mk(kg(totalMax), kg(sigma), {
      unit: 'kg',
      label: 'Take-off mass (heaviest camera)',
      explain: 'Total weight ready to fly with the heaviest camera. It sets the stall speed, the hover power and whether you stay under the 25 kg legal limit.',
      source: `Component build-up iterated to convergence (${iterations} passes); range: structure uncertainties added linearly, other items root-sum-square.`,
    }),
    takeoff_min_payload: mk(kg(totalMin), kg(sigma), {
      unit: 'kg',
      label: 'Take-off mass (lightest camera)',
      explain: 'Total weight with the lightest camera fitted.',
      source: 'Same build-up with the minimum payload.',
    }),
    structure_fraction: qRel(structure / totalMax, structSigma / Math.max(structure, 1e-9), {
      unit: '',
      label: 'Structure fraction',
      explain: 'Share of the take-off weight that is airframe. Small drones are typically 25-35 %; much more means the structure estimate or the design is heavy.',
      source: 'Structure mass / take-off mass; typical band from Gundlach (2014) ch. 8.',
    }),
    converged,
    iterations,
    lift_motor_max_power_w: build.liftMotorMaxPowerW,
    lift_motor_mass_g: build.liftMotorMassG,
    lift_motor_max_thrust_n: build.liftMotorMaxThrustN,
    pusher_motor_max_power_w: build.pusherMaxPowerW,
  };
  return {
    result,
    componentsMin,
    frontShareMax: frontThrustShare(cMax.x, g.front_rotor_x_mm, g.rear_rotor_x_mm),
    frontShareMin: frontThrustShare(cMin.x, g.front_rotor_x_mm, g.rear_rotor_x_mm),
    cgMaxX: cMax.x,
    cgMinX: cMin.x,
    cgMaxSigma: cMax.sigma,
    cgMinSigma: cMin.sigma,
    massRel: totalMax > 0 ? sigma / totalMax : NaN,
    totalMaxG: totalMax,
    totalMinG: totalMin,
    liftMotorMassG: build.liftMotorMassG,
    pusherMotorMassG: build.pusherMotorMassG,
    pack,
  };
}
