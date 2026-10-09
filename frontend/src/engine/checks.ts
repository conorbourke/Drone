/**
 * Tier 1 checks (informative) and input validation. Every status carries a plain message that
 * tells the owner what the result means and what to change.
 */

import type { Settings } from '../api/types';
import {
  CL_CRUISE_MAX_FRACTION,
  TAIL_VOLUME_H_RANGE,
  TAIL_VOLUME_V_MIN,
  TYPICAL_C_RATING,
} from './constants';
import type { ResolvedParameters } from './geometry';
import type { AeroResult, BalanceResult, EngineInput, Geometry, MassResult, PerformanceResult, Status, StatusLevel } from './types';

const ORDER: Record<StatusLevel, number> = { fail: 0, warn: 1, ok: 2, info: 3 };

/** Checks first (fail, warn, ok), then notes; stable within a level. */
export function sortStatuses(list: Status[]): Status[] {
  return list.map((s, i) => ({ s, i })).sort((a, b) => ORDER[a.s.level] - ORDER[b.s.level] || a.i - b.i).map((x) => x.s);
}

const f1 = (v: number) => (Number.isFinite(v) ? v.toFixed(1) : '?');
const f0 = (v: number) => (Number.isFinite(v) ? Math.round(v).toString() : '?');
const f2 = (v: number) => (Number.isFinite(v) ? v.toFixed(2) : '?');

/**
 * Shape-and-sanity validation of the engine input. Any `fail` here means the engine cannot
 * produce numbers; the estimate then returns valid = false with these statuses.
 */
export function validateInput(input: EngineInput): Status[] {
  const out: Status[] = [];
  const fail = (key: string, label: string, message: string) => out.push({ key: `input.${key}`, label, level: 'fail', message });
  const p = input?.parameters;
  const m = input?.mission;
  const s = input?.settings;
  if (!p || typeof p !== 'object' || !p.wing || !p.fuselage || !p.booms || !p.motors || !p.tail || !p.nose_bay || !p.landing_gear || !p.tilt || !p.pusher) {
    fail('parameters', 'Design parameters', 'The design parameters are incomplete, so nothing can be estimated. Reload the project or restore a saved version.');
    return out;
  }
  if (!m || typeof m !== 'object') {
    fail('mission', 'Mission', 'The mission is missing, so nothing can be estimated. Fill in the mission form on the Inputs tab.');
    return out;
  }
  if (!s || !s.checks || !s.limits) {
    fail('settings', 'Settings', 'The settings (check thresholds and limits) are not loaded yet.');
    return out;
  }
  const positive: [string, string, unknown][] = [
    ['wing.span_mm', 'Wingspan', p.wing.span_mm],
    ['wing.root_chord_mm', 'Root chord', p.wing.root_chord_mm],
    ['fuselage.length_mm', 'Fuselage length', p.fuselage.length_mm],
    ['fuselage.width_mm', 'Fuselage width', p.fuselage.width_mm],
    ['fuselage.height_mm', 'Fuselage height', p.fuselage.height_mm],
    ['booms.lateral_offset_mm', 'Boom offset', p.booms.lateral_offset_mm],
    ['booms.length_mm', 'Boom length', p.booms.length_mm],
    ['tail.span_mm', 'Tail span', p.tail.span_mm],
    ['tail.chord_mm', 'Tail chord', p.tail.chord_mm],
    ['tail.arm_mm', 'Tail arm', p.tail.arm_mm],
    ['nose_bay.length_mm', 'Nose bay length', p.nose_bay.length_mm],
    ['cruise_speed_mps', 'Cruise speed', m.cruise_speed_mps],
  ];
  if (p.booms.diameter_mm !== undefined) positive.push(['booms.diameter_mm', 'Boom diameter', p.booms.diameter_mm]);
  if (p.propulsion) {
    positive.push(['propulsion.prop_diameter_mm', 'Propeller diameter', p.propulsion.prop_diameter_mm]);
    positive.push(['propulsion.prop_blades', 'Propeller blades', p.propulsion.prop_blades]);
  }
  if (p.battery) {
    positive.push(['battery.cells_series', 'Cells in series', p.battery.cells_series]);
    positive.push(['battery.cells_parallel', 'Cells in parallel', p.battery.cells_parallel]);
    positive.push(['battery.capacity_mah', 'Battery capacity', p.battery.capacity_mah]);
  }
  if (p.layout === 'quad_pusher') positive.push(['pusher.prop_diameter_mm', 'Pusher propeller diameter', p.pusher.prop_diameter_mm]);
  for (const [key, label, v] of positive) {
    if (typeof v !== 'number' || !Number.isFinite(v) || v <= 0) {
      fail(key, label, `${label} must be a number above zero (it is ${String(v)}). Enter a positive value to get estimates.`);
    }
  }
  const nonNegative: [string, string, unknown][] = [
    ['wing.tip_chord_mm', 'Tip chord', p.wing.tip_chord_mm],
    ['payload_min_g', 'Lightest payload', m.payload_min_g],
    ['payload_max_g', 'Heaviest payload', m.payload_max_g],
    ['landing_gear.height_mm', 'Landing gear height', p.landing_gear.height_mm],
  ];
  for (const [key, label, v] of nonNegative) {
    if (typeof v !== 'number' || !Number.isFinite(v) || v < 0) fail(key, label, `${label} must be zero or more (it is ${String(v)}).`);
  }
  const finite: [string, string, unknown][] = [
    ['wing.sweep_deg', 'Sweep', p.wing.sweep_deg],
    ['wing.dihedral_deg', 'Dihedral', p.wing.dihedral_deg],
    ['wing.incidence_deg', 'Incidence', p.wing.incidence_deg],
    ['wing.x_le_mm', 'Wing position', p.wing.x_le_mm],
    ['wing.z_mm', 'Wing height', p.wing.z_mm],
    ['booms.x_offset_mm', 'Boom position', p.booms.x_offset_mm],
    ['motors.front_x_mm', 'Front motor station', p.motors.front_x_mm],
    ['motors.rear_x_mm', 'Rear motor station', p.motors.rear_x_mm],
    ['motors.height_mm', 'Motor height', p.motors.height_mm],
    ['tail.height_mm', 'Tail height', p.tail.height_mm],
  ];
  if (p.battery) finite.push(['battery.x_mm', 'Battery position', p.battery.x_mm]);
  for (const [key, label, v] of finite) {
    if (typeof v !== 'number' || !Number.isFinite(v)) fail(key, label, `${label} must be a number (it is ${String(v)}).`);
  }
  if (Number.isFinite(p.wing.sweep_deg) && Math.abs(p.wing.sweep_deg) >= 60) {
    fail('wing.sweep_deg', 'Sweep', 'Sweep of 60 degrees or more is outside what these methods (and this kind of aircraft) can handle. Use less than 45 degrees.');
  }
  if (Number.isFinite(p.wing.dihedral_deg) && Math.abs(p.wing.dihedral_deg) >= 45) {
    fail('wing.dihedral_deg', 'Dihedral', 'Dihedral of 45 degrees or more is outside what these methods can handle. Typical values are 0-6 degrees.');
  }
  if ((p.tail.type === 'v_tail' || p.tail.type === 'inverted_v') && p.tail.v_angle_deg !== undefined && !(p.tail.v_angle_deg > 0 && p.tail.v_angle_deg <= 85)) {
    fail('tail.v_angle_deg', 'V-tail angle', 'The V-tail angle must be between 0 and 85 degrees (30-45 is usual).');
  }
  if (Number.isFinite(p.wing.span_mm) && Number.isFinite(p.fuselage.width_mm) && p.fuselage.width_mm >= p.wing.span_mm) {
    fail('wing.span_mm', 'Wingspan', 'The fuselage is as wide as the wingspan, so there is no wing left outside it. Increase the span.');
  }
  if (Number.isFinite(p.motors.rear_x_mm) && Number.isFinite(p.motors.front_x_mm) && p.motors.rear_x_mm <= p.motors.front_x_mm) {
    fail('motors.rear_x_mm', 'Motor stations', 'The rear motors must be behind the front motors along the boom.');
  }
  if (Number.isFinite(m.payload_max_g) && Number.isFinite(m.payload_min_g) && m.payload_max_g < m.payload_min_g) {
    fail('payload_max_g', 'Payload range', 'The heaviest payload is lighter than the lightest payload. Swap the two values.');
  }
  if (p.allowances && (!Number.isFinite(p.allowances.wiring_fraction) || p.allowances.wiring_fraction < 0 || p.allowances.wiring_fraction >= 0.5)) {
    fail('allowances.wiring_fraction', 'Wiring fraction', 'The wiring fraction must be between 0 and 0.5 (typically 0.04-0.08).');
  }
  if (p.allowances && (!Number.isFinite(p.allowances.avionics_g) || p.allowances.avionics_g < 0)) {
    fail('allowances.avionics_g', 'Avionics allowance', 'The avionics allowance must be zero or more grams.');
  }
  if (Number.isFinite(p.wing.tip_chord_mm) && Number.isFinite(p.wing.root_chord_mm) && p.wing.tip_chord_mm > p.wing.root_chord_mm) {
    out.push({ key: 'input.tip_chord', label: 'Tip chord', level: 'warn', message: 'The tip chord is larger than the root chord (reverse taper). The numbers are computed, but this is unusual and weakens the wing root.' });
  }
  const c = s.checks;
  if (!(c.hover_thrust_to_weight_min > 1) || !(c.battery_reserve_fraction >= 0 && c.battery_reserve_fraction < 1)) {
    fail('settings.checks', 'Settings', 'The hover thrust-to-weight minimum must be above 1 and the battery reserve between 0 and 1. Fix them on the Settings page.');
  }
  return out;
}

export interface CheckContext {
  input: EngineInput;
  p: ResolvedParameters;
  g: Geometry;
  mass: MassResult;
  balance: BalanceResult;
  aero: AeroResult;
  perf: PerformanceResult;
  frontShareMax: number;
  frontShareMin: number;
  vtolExceedsUsable: boolean;
}

/** The Tier 1 checks with pass/warn/fail and plain messages (docs/phases/PHASE2.md section 5). */
export function buildChecks(ctx: CheckContext): Status[] {
  const { input, p, mass, balance, aero, perf } = ctx;
  const settings: Settings = input.settings;
  const lim = settings.limits;
  const ch = settings.checks;
  const out: Status[] = [];
  const push = (key: string, label: string, level: StatusLevel, message: string) => out.push({ key, label, level, message });

  // Mass against the limits.
  const mtow = mass.takeoff_max_payload;
  if (mtow.value > lim.legal_mtow_kg) {
    push('check.mtow', 'Take-off mass', 'fail', `Estimated ${f2(mtow.value)} kg is over the ${lim.legal_mtow_kg} kg legal limit for the EU Open category (A3). Reduce the battery, payload or structure.`);
  } else if (mtow.value > lim.design_mtow_kg) {
    push('check.mtow', 'Take-off mass', 'fail', `Estimated ${f2(mtow.value)} kg is over your ${lim.design_mtow_kg} kg design limit. Reduce the battery, payload or structure.`);
  } else if (mtow.value >= lim.warn_mtow_kg || mtow.high > lim.design_mtow_kg) {
    push('check.mtow', 'Take-off mass', 'warn', `Estimated ${f2(mtow.value)} kg (range ${f2(mtow.low)}-${f2(mtow.high)} kg) is close to the ${lim.design_mtow_kg} kg design limit. Keep a margin: real builds usually come out heavier.`);
  } else {
    push('check.mtow', 'Take-off mass', 'ok', `Estimated ${f2(mtow.value)} kg (range ${f2(mtow.low)}-${f2(mtow.high)} kg), under the ${lim.design_mtow_kg} kg design limit.`);
  }
  const target = input.mission.target_takeoff_mass_kg;
  if (Number.isFinite(target) && target > 0) {
    const diff = (mtow.value - target) / target;
    push('note.mass_target', 'Mass against target', Math.abs(diff) > 0.15 ? 'warn' : 'info',
      `The build-up gives ${f2(mtow.value)} kg against your ${f2(target)} kg target (${diff >= 0 ? '+' : ''}${f0(diff * 100)} %).${Math.abs(diff) > 0.15 ? (diff > 0 ? ' Lighten the design (smaller battery, lighter structure) or raise the target.' : ' There is room for a bigger battery or payload.') : ''}`);
  }
  if (!mass.converged) {
    push('check.mass_convergence', 'Mass estimate', 'warn', `The weight estimate did not settle after ${mass.iterations} passes (motors sized for the weight keep adding weight). Treat the numbers with caution; usually the propellers are too small for the weight.`);
  }
  const sf = mass.structure_fraction.value;
  if (sf < 0.18 || sf > 0.45) {
    push('note.structure_fraction', 'Structure fraction', 'info', `The airframe is ${f0(sf * 100)} % of the take-off weight; small drones are usually 25-35 %. Check the structure estimate against real parts when you have them.`);
  }

  // Static margin at both payloads.
  const smMin = ch.static_margin_min * 100;
  const smMax = ch.static_margin_max * 100;
  for (const [key, q, which] of [
    ['check.static_margin_max_payload', balance.static_margin_max_payload, 'heaviest'],
    ['check.static_margin_min_payload', balance.static_margin_min_payload, 'lightest'],
  ] as const) {
    const v = q.value;
    const label = `Static margin (${which} camera)`;
    if (!Number.isFinite(v)) push(key, label, 'warn', 'The static margin could not be computed.');
    else if (v < 0) push(key, label, 'fail', `The balance point is ${f1(-v)} % of the wing chord behind the neutral point with the ${which} camera: the aircraft would be unstable in pitch. Move the battery forward, move the wing back or enlarge the tail.`);
    else if (v < smMin) push(key, label, 'warn', `Static margin ${f1(v)} % with the ${which} camera is below the ${f0(smMin)} % minimum: the aircraft will be twitchy in pitch. Move the battery forward or enlarge the tail.`);
    else if (v > smMax) push(key, label, 'warn', `Static margin ${f1(v)} % with the ${which} camera is above the ${f0(smMax)} % maximum: very stable but nose-heavy, needing more elevator and some extra drag to trim. Move the battery back.`);
    else push(key, label, 'ok', `Static margin ${f1(v)} % (range ${f1(q.low)}-${f1(q.high)} %) with the ${which} camera, inside the ${f0(smMin)}-${f0(smMax)} % range.`);
  }

  // Hover balance: the CG must lie between the front and rear motors.
  for (const [share, which] of [[ctx.frontShareMax, 'heaviest'], [ctx.frontShareMin, 'lightest']] as const) {
    const key = `check.hover_balance_${which === 'heaviest' ? 'max' : 'min'}_payload`;
    const label = `Hover balance (${which} camera)`;
    if (!(share >= 0 && share <= 1)) {
      push(key, label, 'fail', `With the ${which} camera the balance point is outside the motors, so the drone cannot hover level. Move the battery or the motors so the balance point sits between the front and rear motors.`);
    } else if (share < 0.35 || share > 0.65) {
      push(key, label, 'warn', `With the ${which} camera the ${share > 0.5 ? 'front' : 'rear'} motors carry ${f0(Math.max(share, 1 - share) * 100)} % of the weight in hover, leaving little control margin. Move the battery to centre the balance point between the motors.`);
    } else {
      push(key, label, 'ok', `With the ${which} camera the front motors carry ${f0(share * 100)} % of the hover load.`);
    }
  }

  // Cruise to stall.
  const ratio = aero.cruise_to_stall.value;
  if (ratio < 1) {
    push('check.cruise_to_stall', 'Cruise speed against stall', 'fail', `The cruise speed is below the stall speed (${f1(aero.stall_speed.value)} m/s): the wing cannot hold the aircraft up. Increase the wing area or the cruise speed.`);
  } else if (ratio < ch.cruise_to_stall_speed_ratio_min) {
    push('check.cruise_to_stall', 'Cruise speed against stall', 'warn', `Cruise is only ${f2(ratio)} x the stall speed (minimum ${ch.cruise_to_stall_speed_ratio_min}). Increase the wing area, reduce weight or fly faster.`);
  } else {
    push('check.cruise_to_stall', 'Cruise speed against stall', 'ok', `Cruise is ${f2(ratio)} x the stall speed of ${f1(aero.stall_speed.value)} m/s (minimum ${ch.cruise_to_stall_speed_ratio_min}).`);
  }
  if (aero.cl_cruise.value > CL_CRUISE_MAX_FRACTION * aero.cl_max.value && ratio >= 1) {
    push('check.cl_cruise', 'Cruise lift coefficient', 'warn', `The wing works at ${f0((aero.cl_cruise.value / aero.cl_max.value) * 100)} % of its maximum lift in cruise, leaving little margin for turns and gusts. A bigger wing or a faster cruise helps.`);
  }

  // Tail size.
  const vh = aero.tail_volume_h.value;
  const vv = aero.tail_volume_v.value;
  if (vh < TAIL_VOLUME_H_RANGE[0] || vh > TAIL_VOLUME_H_RANGE[1]) {
    push('check.tail_volume_h', 'Horizontal tail size', 'warn', `The horizontal tail volume is ${f2(vh)}; ${TAIL_VOLUME_H_RANGE[0]}-${TAIL_VOLUME_H_RANGE[1]} is usual (Raymer). ${vh < TAIL_VOLUME_H_RANGE[0] ? 'Enlarge the tail or lengthen the tail arm.' : 'The tail is larger than needed; it adds weight and drag.'}`);
  } else {
    push('check.tail_volume_h', 'Horizontal tail size', 'ok', `Horizontal tail volume ${f2(vh)} is in the usual ${TAIL_VOLUME_H_RANGE[0]}-${TAIL_VOLUME_H_RANGE[1]} band.`);
  }
  if (vv < TAIL_VOLUME_V_MIN) {
    push('check.tail_volume_v', 'Vertical tail size', 'warn', `The vertical tail volume is ${f2(vv * 100)}/100; at least ${TAIL_VOLUME_V_MIN} is usual (Raymer). Increase the fin size, the V-tail angle or the tail arm.`);
  } else {
    push('check.tail_volume_v', 'Vertical tail size', 'ok', `Vertical tail volume ${f2(vv)} (at least ${TAIL_VOLUME_V_MIN} is usual).`);
  }

  // Endurance.
  const end = perf.endurance_cruise;
  const tgt = input.mission.target_endurance_min;
  if (ctx.vtolExceedsUsable) {
    push('check.endurance', 'Endurance', 'fail', 'The usable battery energy is not even enough for take-off, transitions and landing. Fit a bigger battery or reduce weight.');
  } else if (Number.isFinite(tgt) && tgt > 0) {
    if (end.value >= tgt) {
      push('check.endurance', 'Endurance', 'ok', `Estimated ${f0(end.low)}-${f0(end.high)} min of wing flight (best estimate ${f0(end.value)}) against your ${f0(tgt)} min target.${end.low < tgt ? ' The low end of the range falls short, so keep some margin.' : ''}`);
    } else {
      push('check.endurance', 'Endurance', 'warn', `Estimated ${f0(end.low)}-${f0(end.high)} min of wing flight (best estimate ${f0(end.value)}), short of your ${f0(tgt)} min target. A bigger battery, less weight, less drag or a longer span would help.`);
    }
  }

  // Battery current.
  const chem = p.battery.chemistry;
  const rating = TYPICAL_C_RATING[chem];
  const allowed = rating * ch.battery_current_max_fraction_of_rating;
  const c = perf.battery_c_rate.value;
  const chemLabel = chem === 'lipo' ? 'LiPo' : 'Li-ion';
  if (c > rating) {
    push('check.battery_c_rate', 'Battery current', 'fail', `Transition draws about ${f0(perf.peak_current.value)} A, ${f1(c)} C, more than a typical ${chemLabel} pack's ${rating} C continuous rating (placeholder until real packs in Phase 4). Add cells in parallel or choose a higher-rated pack.`);
  } else if (c > allowed) {
    push('check.battery_c_rate', 'Battery current', 'warn', `Transition draws about ${f0(perf.peak_current.value)} A, ${f1(c)} C, above ${f0(ch.battery_current_max_fraction_of_rating * 100)} % of a typical ${chemLabel} pack's ${rating} C rating (placeholder). Consider more cells in parallel.`);
  } else {
    push('check.battery_c_rate', 'Battery current', 'ok', `Transition draws about ${f0(perf.peak_current.value)} A, ${f1(c)} C, within ${f0(ch.battery_current_max_fraction_of_rating * 100)} % of a typical ${chemLabel} pack's ${rating} C rating (placeholder until Phase 4).`);
  }

  push('check.hover_thrust_to_weight', 'Hover thrust-to-weight', 'info', `Assumed ${ch.hover_thrust_to_weight_min}: the motors are sized to the Settings minimum (on the more heavily loaded pair). Real motors and propellers are checked in Phase 4.`);

  if (p.battery.x_mm < 0 || p.battery.x_mm > p.fuselage.length_mm) {
    push('check.battery_position', 'Battery position', 'warn', `The battery centre (${f0(p.battery.x_mm)} mm) is outside the fuselage (0-${f0(p.fuselage.length_mm)} mm). Move it inside.`);
  }
  push('note.range_rules', 'Range and the rules', 'info', `Range ${f1(perf.range.low)}-${f1(perf.range.high)} km in still air. In the EU Open category A3 you must keep the drone in visual line of sight, at least 150 m from residential, commercial, industrial or recreational areas; flying beyond visual line of sight needs IAA authorisation.`);
  if (input.mission.scale === 'prototype' && mtow.value > 6) {
    push('note.scale', 'Construction scale', 'info', 'The mission is set to the printed prototype, but the aircraft is heavy for printed structure. Switch the mission scale to final (carbon) for a heavier aircraft.');
  }
  return out;
}
