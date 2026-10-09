/**
 * The Tier 1 estimate: geometry, mass and balance, aerodynamics, performance and checks for one
 * design, in a few milliseconds. Never throws: degenerate input returns valid = false with
 * statuses that explain the problem.
 */

import {
  interpolatePolar,
  liftCurveSlope,
  neutralPoint,
  oswaldEfficiency,
  parasiteDrag,
  polarStatuses,
  stallSpeed,
  wingBodyFactor,
  wingClMax,
} from './aero';
import { SEA_LEVEL, mach, reynolds } from './atmosphere';
import { buildChecks, sortStatuses, validateInput } from './checks';
import {
  AVIONICS_POWER_W,
  CL_MAX_SWEEP_END_EXTRA,
  FIGURE_OF_MERIT_HOVER,
  G0,
  MISSION_PROFILE,
  PROP_COEFF_UNCERTAINTY,
  PUSHER_BLADES,
  PUSHER_PITCH_RATIO,
  TRANSITION_POWER_FACTOR,
  UNCERTAINTY,
} from './constants';
import { buildGeometry, withDefaults } from './geometry';
import { solveMass } from './mass';
import { performanceWithRanges, type PerfInputs } from './performance';
import { genericPropeller } from './propeller';
import { qAbs, qExact, qRange, qRel, rss, rssPowers } from './quantity';
import type { AeroResult, BalanceResult, EngineInput, Estimates, PerformanceResult, Status } from './types';
import { degToRad } from './units';

function now(): number {
  return typeof performance !== 'undefined' && typeof performance.now === 'function' ? performance.now() : Date.now();
}

function invalid(input: EngineInput, statuses: Status[], t0: number): Estimates {
  return {
    valid: false,
    layout: input?.parameters?.layout ?? 'front_tilt',
    geometry: null,
    mass: null,
    balance: null,
    aero: null,
    performance: null,
    statuses: sortStatuses(statuses),
    assumptions: [],
    elapsed_ms: now() - t0,
  };
}

/** Estimate one design (docs/phases/PHASE2.md section 5). */
export function estimate(input: EngineInput): Estimates {
  const t0 = now();
  try {
    const problems = validateInput(input);
    if (problems.some((s) => s.level === 'fail')) return invalid(input, problems, t0);
    return run(input, problems, t0);
  } catch (err) {
    return invalid(input, [{
      key: 'engine.error',
      label: 'Engine',
      level: 'fail',
      message: `The estimate could not be computed for this design (${err instanceof Error ? err.message : String(err)}). Undo the last change; if it persists, report it.`,
    }], t0);
  }
}

function run(input: EngineInput, inputStatuses: Status[], t0: number): Estimates {
  const { mission, settings } = input;
  const airfoils = input.airfoils ?? {};
  const p = withDefaults(input.parameters);
  const scale = mission.scale === 'final' ? 'final' : 'prototype';
  const atm = SEA_LEVEL;
  const V = mission.cruise_speed_mps;
  const statuses: Status[] = [...inputStatuses];

  const g = buildGeometry(input.parameters, { airfoils, render: false });
  statuses.push(...g.statuses);
  const w = g.wing;
  const macM = w.mac_mm / 1000;
  const S = w.area_m2;
  const M = mach(atm, V);

  // ----- Mass and balance (iterated) -----
  const ms = solveMass({ p, g, mission, settings, atm, parts: input.partsMasses ?? null });
  const mass = ms.result;
  const massKg = ms.totalMaxG / 1000;
  const massMinKg = ms.totalMinG / 1000;
  const W = massKg * G0;
  const massRel = ms.massRel;

  // ----- Airfoil data, lift slope, maximum lift and stall (two passes for the stall Reynolds number) -----
  const reCruise = reynolds(atm, V, macM);
  const wingPolar = interpolatePolar(airfoils[p.wing.airfoil], reCruise, 'wing');
  const tailRe = reynolds(atm, V, p.tail.chord_mm / 1000);
  const tailPolar = interpolatePolar(airfoils[p.tail.airfoil], tailRe, 'tail');
  statuses.push(...polarStatuses(p.wing.airfoil, 'wing', wingPolar, reCruise), ...polarStatuses(p.tail.airfoil, 'tail', tailPolar, tailRe));
  const sweepT = degToRad(w.sweep_max_thickness_deg);
  const sweepQ = degToRad(w.sweep_quarter_chord_deg);
  const bodyFactor = wingBodyFactor(g);
  const aw = liftCurveSlope(w.aspect_ratio, sweepT, wingPolar.polar.cl_alpha_per_rad, M, bodyFactor);
  const vs1 = stallSpeed(W, atm.rho, S, wingClMax(wingPolar.polar.cl_max, sweepQ));
  const reStall = reynolds(atm, vs1, macM);
  const stallPolar = interpolatePolar(airfoils[p.wing.airfoil], reStall, 'wing');
  const clMax = wingClMax(stallPolar.polar.cl_max, sweepQ);
  const vs = stallSpeed(W, atm.rho, S, clMax);
  const vsRel = rssPowers([[0.5, massRel], [0.5, UNCERTAINTY.cl_max]]);
  // An XFOIL sweep that ended before the stall gives a cl_max that is only a lower bound: the
  // real CL_max may be higher, so the stall speed may be lower. Widen only that side.
  const clMaxLowerBound = stallPolar.clMaxLowerBound;
  const clMaxRelHigh = clMaxLowerBound ? UNCERTAINTY.cl_max + CL_MAX_SWEEP_END_EXTRA : UNCERTAINTY.cl_max;
  const vsRelLow = rssPowers([[0.5, massRel], [0.5, clMaxRelHigh]]);
  const vsLow = vs * (1 - vsRelLow);
  const vsHigh = vs * (1 + vsRel);

  // ----- Drag -----
  const drag = parasiteDrag({ p, g, atm, speed: V, scale, liftMotorMassG: ms.liftMotorMassG });
  const e = oswaldEfficiency(w.aspect_ratio, w.sweep_le_deg);

  // ----- Neutral point and static margin -----
  const np = neutralPoint(g, aw, tailPolar.polar.cl_alpha_per_rad, M);
  const sm = (cg: number) => ((np.x_np_mm - cg) / w.mac_mm) * 100;
  const smSigma = (cgSigma: number) => rss([UNCERTAINTY.neutral_point_mac * 100, (cgSigma / w.mac_mm) * 100]);

  // ----- Performance -----
  const pack = ms.pack;
  const D = p.propulsion.prop_diameter_mm / 1000;
  // Cruise propeller: the tilted pair of lift propellers, or the pusher (pitch/diameter assumed).
  const pusherCruise = p.layout === 'quad_pusher';
  const cruisePropeller = pusherCruise
    ? genericPropeller(p.pusher.prop_diameter_mm, PUSHER_PITCH_RATIO * p.pusher.prop_diameter_mm, PUSHER_BLADES)
    : genericPropeller(p.propulsion.prop_diameter_mm, p.propulsion.prop_pitch_mm, p.propulsion.prop_blades);
  const cruisePropCount = pusherCruise ? 1 : 2;
  const cruisePropName = pusherCruise
    ? `the ${Math.round(p.pusher.prop_diameter_mm)} mm pusher propeller (pitch assumed ${PUSHER_PITCH_RATIO} x diameter)`
    : `the two tilted ${Math.round(p.propulsion.prop_diameter_mm)} x ${Math.round(p.propulsion.prop_pitch_mm)} mm lift propellers`;
  const perfIn: PerfInputs = {
    massKg,
    frontShare: ms.frontShareMax,
    rho: atm.rho,
    speed: V,
    wingArea: S,
    cd0: drag.cd0,
    oswald: e,
    aspectRatio: w.aspect_ratio,
    cruiseProp: { propeller: cruisePropeller, count: cruisePropCount },
    discArea: Math.PI * (D / 2) ** 2,
    avionicsW: AVIONICS_POWER_W[scale],
    energyWh: pack.energyWh,
    reserveFraction: settings.checks.battery_reserve_fraction,
    packVoltage: pack.voltage,
    capacityAh: pack.capacityAh,
  };
  const pr = performanceWithRanges(perfIn, massRel);
  const prMin = performanceWithRanges({ ...perfIn, massKg: massMinKg, frontShare: ms.frontShareMin }, massRel);
  const n = pr.nominal;
  const etaPropText = `${n.cruisePropEfficiency.toFixed(2)} at J = ${n.cruisePropJ.toFixed(2)}`;

  const cl = n.cl;
  const aero: AeroResult = {
    wing_area: qExact(S, { unit: 'm²', label: 'Wing area', explain: 'The area of the wing seen from above, including the part hidden inside the fuselage. More area lets the aircraft fly slower but adds weight and drag.', source: 'Trapezoidal planform: span x (root + tip chord) / 2.' }),
    aspect_ratio: qExact(w.aspect_ratio, { unit: '', label: 'Aspect ratio', explain: 'Span compared with average chord (span² / area). Long, slender wings (high aspect ratio) make less drag from lift, which helps endurance, but are heavier and more flexible.', source: 'Span² / wing area.' }),
    mac: qExact(w.mac_mm, { unit: 'mm', label: 'Mean aerodynamic chord', explain: 'The "average" chord used as the yardstick for balance: the balance point and static margin are measured along it.', source: 'Closed form for a trapezoidal wing: (2/3) c_root (1 + λ + λ²) / (1 + λ) (Raymer ch. 4).' }),
    wing_loading: qRel(massKg / S, massRel, { unit: 'kg/m²', label: 'Wing loading', explain: `Weight carried per square metre of wing (${(massKg * 10 / S).toFixed(0)} g/dm² in RC terms). Higher wing loading means faster stall and landing speeds but better gust handling.`, source: 'Take-off mass (heaviest camera) / wing area.' }),
    reynolds_cruise: qExact(reCruise, { unit: '', label: 'Reynolds number (cruise)', explain: 'A measure of how "big and fast" the wing is to the air. Small, slow wings (below about 200,000) suffer more drag and stall earlier, so the airfoil choice matters more.', source: 'ρ V c̄ / μ, sea-level ISA, on the mean aerodynamic chord.' }),
    reynolds_stall: qExact(reStall, { unit: '', label: 'Reynolds number (stall)', explain: 'The Reynolds number near the stall speed, used to look up the airfoil’s maximum lift.', source: 'ρ V_stall c̄ / μ, sea-level ISA.' }),
    lift_curve_slope: qRel(aw, UNCERTAINTY.lift_slope, { unit: '/rad', label: 'Wing lift-curve slope', explain: 'How quickly the wing’s lift grows as its angle to the air increases. It feeds the stability (neutral point) calculation.', source: 'Helmbold/DATCOM formula with sweep and fuselage factor (Raymer ch. 12.4; Anderson), section slope from the XFOIL polar.' }),
    cl_max: qRange(clMax, clMax * (1 - UNCERTAINTY.cl_max), clMax * (1 + clMaxRelHigh), { unit: '', label: 'Wing maximum lift coefficient', explain: 'The most lift the wing can make before it stalls, as a coefficient. It sets the stall speed.', source: `0.9 x section cl_max x cos(quarter-chord sweep) (Raymer ch. 12.4), section cl_max from the XFOIL polar at the stall Reynolds number${clMaxLowerBound ? '; the XFOIL sweep ended before the stall, so the section value is a lower bound and the range extends further upwards' : ''}.` }),
    stall_speed: qRange(vs, vsLow, vsHigh, { unit: 'm/s', label: 'Stall speed', explain: `The slowest the wing can hold the aircraft up (${(vs * 3.6).toFixed(0)} km/h), with the heaviest camera. Wing flight must stay well above it, especially in turns and during transition.`, source: `V_s = sqrt(2 W / (ρ S CL_max)) (Anderson, Aircraft Performance and Design); range from ±10 % on CL_max and the mass range${clMaxLowerBound ? `, widened downwards because the airfoil's cl_max is a lower bound (+${CL_MAX_SWEEP_END_EXTRA * 100} % allowed)` : ''}.` }),
    cl_cruise: qRel(cl, massRel, { unit: '', label: 'Cruise lift coefficient', explain: 'How hard the wing works at cruise speed. Efficient small-UAV wings cruise around 0.4-0.8; close to the maximum leaves no margin.', source: 'CL = W / (q S), level flight.' }),
    cruise_to_stall: qRange(V / vs, V / vsHigh, V / vsLow, { unit: '', label: 'Cruise / stall speed', explain: 'How many times faster than the stall the aircraft cruises. Around 1.3 or more gives a safety margin for gusts and turns.', source: 'Cruise speed / stall speed.' }),
    cd0: qRel(drag.cd0, UNCERTAINTY.drag, { unit: '', label: 'Zero-lift drag coefficient', explain: 'The drag the airframe makes just by moving through the air (skin friction, shape, stopped propellers, landing gear). Lower is better for endurance.', source: 'Component build-up: skin friction x form factor x interference x wetted area, plus drag areas (Raymer ch. 12.5); ±15 %.' }),
    drag_items: drag.items,
    oswald: qAbs(e, 0.05, { unit: '', label: 'Oswald efficiency', explain: 'How close the wing comes to the ideal (1.0) for drag caused by making lift. Typical straight wings reach 0.75-0.85.', source: 'Raymer ch. 12.6 empirical fit: 1.78 (1 − 0.045 A^0.68) − 0.64 for straight wings.' }),
    cd_induced: qRel(n.cdInduced, rssPowers([[2, massRel], [1, 0.06]]), { unit: '', label: 'Induced drag coefficient', explain: 'The drag that comes from making lift (wing-tip vortices). It shrinks with a longer span and grows with weight.', source: 'CL² / (π e A) (Anderson, Fundamentals of Aerodynamics ch. 5).' }),
    lift_to_drag: pr.quantity((c) => c.liftToDrag, ['drag', 'mass'], { unit: '', label: 'Lift-to-drag ratio (cruise)', explain: 'How many newtons of lift the aircraft gets per newton of drag at cruise. Higher means less power and longer flights; small VTOL drones typically reach 8-14.', source: 'CL / (CD0 + CDi) at cruise; range from ±15 % drag and the mass range.' }),
    drag_cruise: pr.quantity((c) => c.dragN, ['drag', 'mass'], { unit: 'N', label: 'Cruise drag', explain: 'The force the propellers must overcome in level cruise.', source: 'q S (CD0 + CDi).' }),
    tail_volume_h: qExact(g.tail.horizontal_volume_coefficient, { unit: '', label: 'Horizontal tail volume', explain: 'Tail area times tail arm compared with wing area times chord. It measures how much pitch stability the tail provides; 0.3-0.8 is usual.', source: 'S_h l_t / (S c̄), projected area for V tails (Raymer ch. 6).' }),
    tail_volume_v: qExact(g.tail.vertical_volume_coefficient, { unit: '', label: 'Vertical tail volume', explain: 'Fin area times arm compared with wing area times span. It measures how well the aircraft keeps its nose into the wind; at least 0.02 is usual.', source: 'S_v l_v / (S b), projected area for V tails (Raymer ch. 6).' }),
    downwash_gradient: qExact(np.depsDalpha, { unit: '', label: 'Downwash gradient', explain: 'How much the wing bends the airflow down onto the tail as the angle increases, which makes the tail less effective.', source: 'dε/dα = 2 CLα / (π A) (Nelson ch. 2).' }),
  };

  const balance: BalanceResult = {
    cg_max_payload_x: qAbs(ms.cgMaxX, ms.cgMaxSigma, { unit: 'mm', label: 'Balance point (heaviest camera)', explain: 'Where the aircraft balances, measured from the nose. It must sit between the front and rear motors for hover and ahead of the neutral point for wing flight.', source: 'Mass-weighted average of component positions; range from component mass uncertainties.' }),
    cg_min_payload_x: qAbs(ms.cgMinX, ms.cgMinSigma, { unit: 'mm', label: 'Balance point (lightest camera)', explain: 'The balance point with the lightest camera fitted; it moves back as the camera gets lighter.', source: 'Mass-weighted average of component positions.' }),
    cg_max_payload_mac: qAbs(((ms.cgMaxX - w.mac_x_le_mm) / w.mac_mm) * 100, (ms.cgMaxSigma / w.mac_mm) * 100, { unit: '% MAC', label: 'Balance point, % of chord (heaviest camera)', explain: 'The balance point as a percentage of the mean wing chord from its leading edge; most aircraft balance around 25-35 %.', source: '(x_cg − x_LE,MAC) / c̄.' }),
    cg_min_payload_mac: qAbs(((ms.cgMinX - w.mac_x_le_mm) / w.mac_mm) * 100, (ms.cgMinSigma / w.mac_mm) * 100, { unit: '% MAC', label: 'Balance point, % of chord (lightest camera)', explain: 'The balance point as a percentage of the mean wing chord with the lightest camera.', source: '(x_cg − x_LE,MAC) / c̄.' }),
    neutral_point_x: qAbs(np.x_np_mm, UNCERTAINTY.neutral_point_mac * w.mac_mm, { unit: 'mm', label: 'Neutral point', explain: 'The balance point at which the aircraft would be neither stable nor unstable in pitch. The real balance point must be ahead of it.', source: 'Wing and tail aerodynamic centres weighted by lift slope, tail with downwash and efficiency 0.9, fuselage by Multhopp’s method (Nelson ch. 2; Etkin & Reid ch. 2); ±10 % MAC (Tier 1 against AVL: the fuselage and downwash terms differ by up to ~10 % MAC; the full analysis uses AVL).' }),
    static_margin_max_payload: qAbs(sm(ms.cgMaxX), smSigma(ms.cgMaxSigma), { unit: '% MAC', label: 'Static margin (heaviest camera)', explain: 'How far the balance point is ahead of the neutral point, as a percentage of the wing chord. Positive means stable; 5-20 % is the usual target.', source: '(x_np − x_cg) / c̄ (Nelson ch. 2); range combines ±10 % MAC on the neutral point and the CG uncertainty.' }),
    static_margin_min_payload: qAbs(sm(ms.cgMinX), smSigma(ms.cgMinSigma), { unit: '% MAC', label: 'Static margin (lightest camera)', explain: 'The static margin with the lightest camera: the balance point moves back, so stability is lowest here.', source: '(x_np − x_cg) / c̄ (Nelson ch. 2).' }),
    hover_front_share: qExact(ms.frontShareMax, { unit: '', label: 'Front motors’ share of hover load', explain: 'The fraction of the weight the front pair carries in hover (0.5 is even). Uneven loading leaves less control margin on the busier pair.', source: 'Moment balance of the two motor pairs about the balance point.' }),
  };

  const mp = MISSION_PROFILE;
  const perf: PerformanceResult = {
    cruise_power: pr.quantity((c) => c.cruisePowerW, ['drag', 'mass', 'eta', 'ct', 'cp'], { unit: 'W', label: 'Cruise power', explain: `Electrical power drawn from the battery in level wing flight, including avionics. It decides how long the battery lasts in cruise. The aircraft cruises on ${cruisePropName}; their efficiency comes from a propeller model at the cruise thrust and speed.`, source: `Drag x speed / (η_prop ${etaPropText} x η_motor 0.85 x η_ESC 0.95) + ${perfIn.avionicsW} W avionics; η_prop from the generic CT(J), CP(J) propeller curves (UIUC-trend fit, Brandt & Selig AIAA 2011-1255; as the server engine); range from ±15 % drag, mass, ±15 % on CT and CP and ±7 % motor x ESC efficiency.` }),
    cruise_propeller_efficiency: pr.quantity((c) => c.cruisePropEfficiency, ['drag', 'mass', 'ct', 'cp'], { unit: '', label: 'Cruise propeller efficiency', explain: pusherCruise ? 'Share of the pusher’s shaft power that becomes useful thrust power in cruise.' : 'Share of the tilted propellers’ shaft power that becomes useful thrust power in cruise. Hover-sized propellers carry little thrust in cruise and run close to the advance ratio where their thrust falls to zero, so their efficiency is low.', source: `J CT / CP at thrust = drag / ${cruisePropCount} and the cruise speed, generic fixed-pitch propeller (pitch/diameter ${cruisePropeller.pitchRatio.toFixed(2)}, zero thrust at J = ${cruisePropeller.jZeroThrust.toFixed(2)}; Brandt & Selig AIAA 2011-1255 / UIUC trends, ±${PROP_COEFF_UNCERTAINTY * 100} % on CT and CP).` }),
    hover_power: pr.quantity((c) => c.hoverPowerW, ['mass', 'fm', 'eta'], { unit: 'W', label: 'Hover power', explain: 'Electrical power needed to hover with the heaviest camera. Hovering is expensive, which is why the VTOL drone spends as little time as possible doing it.', source: `Momentum theory per rotor, T^1.5 / sqrt(2 ρ A) / FM ${FIGURE_OF_MERIT_HOVER} (Leishman ch. 2), 3 % download, / (η_motor x η_ESC), + avionics.` }),
    transition_power: pr.quantity((c) => c.transitionPowerW, ['mass', 'fm', 'eta'], { unit: 'W', label: 'Transition power', explain: 'Power during the change between hover and wing flight, the most demanding phase.', source: `Taken as ${TRANSITION_POWER_FACTOR} x hover power (stated assumption until the Phase 3 transition model).` }),
    hover_disc_loading: qRel(n.discLoading, massRel, { unit: 'N/m²', label: 'Disc loading', explain: 'Hover thrust per square metre of propeller disc. Lower disc loading (bigger propellers) needs less hover power.', source: 'Hover thrust / total disc area of the four lift propellers.' }),
    peak_current: pr.quantity((c) => c.peakCurrentA, ['mass', 'fm', 'eta'], { unit: 'A', label: 'Peak battery current', explain: 'The highest current drawn from the battery, during transition. The battery and wiring must handle it without overheating.', source: 'Transition power / nominal pack voltage.' }),
    battery_c_rate: pr.quantity((c) => c.cRate, ['mass', 'fm', 'eta'], { unit: 'C', label: 'Peak battery C-rate', explain: 'Peak current divided by the battery capacity. A pack rated below this number will sag and heat up.', source: 'Peak current / pack capacity in Ah.' }),
    battery_energy: qRel(pack.energyWh, UNCERTAINTY.battery_energy, { unit: 'Wh', label: 'Battery energy', explain: 'The energy stored in the pack when full (voltage x capacity).', source: `${p.battery.cells_series} cells x ${perfIn.packVoltage / p.battery.cells_series} V x ${pack.capacityAh.toFixed(2)} Ah (nominal); ±5 %.` }),
    usable_energy: pr.quantity((c) => c.usableWh, ['energy'], { unit: 'Wh', label: 'Usable energy', explain: 'Energy you can use before landing with the reserve still in the pack.', source: `Nominal energy x (1 − ${settings.checks.battery_reserve_fraction} reserve) x ${mp.usable_energy_factor} (stated usable fraction).` }),
    vtol_energy: pr.quantity((c) => c.vtolWh, ['mass', 'fm', 'eta'], { unit: 'Wh', label: 'Energy for take-off, transitions and landing', explain: `Energy for ${mp.takeoff_hover_s} s hover at take-off, two ${mp.transition_s} s transitions and ${mp.landing_hover_s} s hover at landing.`, source: 'Mission profile (Phase 2 contract) x hover and transition power.' }),
    endurance_cruise: pr.quantity((c) => c.cruiseTimeS / 60, ['drag', 'mass', 'energy', 'fm', 'eta', 'ct', 'cp'], { unit: 'min', label: 'Wing-flight endurance', explain: 'Minutes of cruise on the wing after take-off, transitions and landing are paid for, keeping the battery reserve. This is the number to compare with your target.', source: 'Usable energy minus VTOL energy, / cruise power; range combines ±15 % drag, mass, ±5 % energy, figure of merit ±0.05, ±7 % motor x ESC efficiency and ±15 % on the cruise propeller CT and CP (root-sum-square).' }),
    endurance_total: pr.quantity((c) => c.totalTimeS / 60, ['drag', 'mass', 'energy', 'fm', 'eta', 'ct', 'cp'], { unit: 'min', label: 'Total flight time', explain: 'Wing-flight time plus the take-off, transition and landing phases.', source: 'Cruise time + mission-profile VTOL time.' }),
    range: pr.quantity((c) => c.rangeM / 1000, ['drag', 'mass', 'energy', 'fm', 'eta', 'ct', 'cp'], { unit: 'km', label: 'Range (still air)', explain: 'Distance covered in the wing-flight time at cruise speed with no wind. Flying beyond visual line of sight needs IAA authorisation.', source: 'Cruise speed x wing-flight endurance.' }),
    endurance_cruise_min_payload: prMin.quantity((c) => c.cruiseTimeS / 60, ['drag', 'mass', 'energy', 'fm', 'eta', 'ct', 'cp'], { unit: 'min', label: 'Wing-flight endurance (lightest camera)', explain: 'Wing-flight endurance with the lightest camera fitted.', source: 'Same method at the minimum payload.' }),
    hover_thrust_to_weight: qExact(settings.checks.hover_thrust_to_weight_min, { unit: '', label: 'Hover thrust-to-weight', explain: 'Maximum motor thrust divided by weight. It is assumed equal to the Settings minimum because the motors are sized to it; real parts are checked in Phase 4.', source: 'Assumed (motors sized to settings.checks.hover_thrust_to_weight_min).' }),
    mission: n.segments,
  };

  const checks = buildChecks({
    input,
    p,
    g,
    mass,
    balance,
    aero,
    perf,
    frontShareMax: ms.frontShareMax,
    frontShareMin: ms.frontShareMin,
    vtolExceedsUsable: n.vtolWh >= n.usableWh,
  });
  statuses.push(...checks);

  const assumptions = [
    'Sea-level standard air (1.225 kg/m³, 15 °C), still air.',
    `Structure weights use ${scale === 'final' ? 'carbon-composite' : '3D-printed'} densities that are first estimates; Phase 6 built weights will calibrate them.`,
    mass.parts_used.length > 0
      ? `Masses of the parts chosen on the Parts tab replace the statistical ones for: ${mass.parts_used.join('; ')}. Motor and propeller performance still uses the generic Tier 1 models; the full analysis uses the parts' own data.`
      : `Motors are sized so the four lift motors give ${settings.checks.hover_thrust_to_weight_min} x the weight; motor, ESC and propeller weights come from statistical relations until parts are chosen on the Parts tab.`,
    `Hover figure of merit ${FIGURE_OF_MERIT_HOVER}, motor efficiency 0.85, ESC 0.95. Cruise on ${cruisePropName}: propeller efficiency ${etaPropText} from the generic CT(J), CP(J) model (the same curves as the server analysis).`,
    `Mission: ${mp.takeoff_hover_s} s take-off hover, ${mp.transition_s} s transitions at ${TRANSITION_POWER_FACTOR} x hover power, ${mp.landing_hover_s} s landing hover, ${settings.checks.battery_reserve_fraction * 100} % reserve, ${mp.usable_energy_factor * 100} % of nominal pack energy usable.`,
    'Airfoil data from the XFOIL tables at the cruise and stall Reynolds numbers; Phase 3 replaces the whole-aircraft numbers with AVL and XFOIL runs.',
    'Section lift-curve slopes above 2π per radian (XFOIL fits distorted by laminar separation bubbles at low Reynolds numbers) are capped at 2π for the wing and tail lift slopes.',
  ];
  if (clMaxLowerBound) {
    assumptions.push(
      `The XFOIL sweep for the ${p.wing.airfoil} wing airfoil ended before it stalled near the stall Reynolds number (${Math.round(reStall).toLocaleString('en-IE')}), so its cl_max is a lower bound: the real stall speed may be lower than shown, and its range is widened on that side.`,
    );
  }

  const t1 = now();
  return {
    valid: true,
    layout: p.layout,
    geometry: g,
    mass,
    balance,
    aero,
    performance: perf,
    statuses: sortStatuses(statuses),
    assumptions,
    elapsed_ms: t1 - t0,
  };
}
