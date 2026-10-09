/**
 * Test designs: the default 2.5 kg front-tilt prototype (backend app/defaults.py plus the schema
 * v2 defaults), a 24 kg final-scale version and a quad + pusher variant. The final
 * scale case is a test starting point (lengths x (24/2.5)^(1/3), then battery, boom tube,
 * payload and speed chosen by hand); the engine re-runs the full analysis on it.
 */

import type { DesignParameters, Mission, Settings } from '../../api/types';
import type { EngineInput } from '../types';
import { TEST_AIRFOILS } from './airfoils';

/**
 * The new-project default battery position (backend app/defaults.py, DESIGN_V2_DEFAULTS): 290 mm,
 * moved 90 mm forward from the Phase 1/2 value of 380 mm after the Phase 3 AVL analysis found the
 * default design unstable with the lightest camera (static margin -2.5 % MAC at 380 mm).
 */
export const DEFAULT_BATTERY_X_MM = 290;

/** The current new-project default design (backend app/defaults.py). Engine tests and the golden fixture use this. */
export function newProjectParameters(): DesignParameters {
  const p = defaultParameters();
  return { ...p, battery: { ...p.battery!, x_mm: DEFAULT_BATTERY_X_MM } };
}

/**
 * The Phase 1/2 default document, with the battery at 380 mm. Kept unchanged because the drawing
 * and handle tests outside the engine (src/lib) are pinned to its numbers; engine tests use
 * newProjectParameters().
 */
export function defaultParameters(): DesignParameters {
  return {
    schema_version: 2,
    layout: 'front_tilt',
    wing: { span_mm: 1800, root_chord_mm: 260, tip_chord_mm: 180, sweep_deg: 0, dihedral_deg: 3, incidence_deg: 2, airfoil: 'sd7037', x_le_mm: 300, z_mm: 0, twist_deg: 0 },
    fuselage: { length_mm: 900, width_mm: 110, height_mm: 120, cross_section: 'rounded_rect' },
    booms: { count: 2, lateral_offset_mm: 300, length_mm: 700, x_offset_mm: -250, diameter_mm: 20 },
    motors: { front_x_mm: 40, rear_x_mm: 660, height_mm: 25 },
    tilt: { axis_x_mm: 40, max_angle_deg: 90 },
    pusher: { prop_diameter_mm: 254, x_mm: 880 },
    tail: { type: 'inverted_v', span_mm: 500, chord_mm: 140, arm_mm: 620, height_mm: 180, v_angle_deg: 40, airfoil: 'naca0009' },
    nose_bay: { length_mm: 180, width_mm: 100, height_mm: 100 },
    landing_gear: { type: 'skids', height_mm: 90 },
    propulsion: { prop_diameter_mm: 330, prop_pitch_mm: 140, prop_blades: 2 },
    battery: { chemistry: 'lipo', cells_series: 6, cells_parallel: 1, capacity_mah: 5000, x_mm: 380 },
    allowances: { avionics_g: 220, wiring_fraction: 0.06 },
  };
}

export function defaultMission(): Mission {
  return {
    schema_version: 1,
    scale: 'prototype',
    target_takeoff_mass_kg: 2.5,
    target_endurance_min: 45,
    cruise_speed_mps: 16,
    payload_min_g: 150,
    payload_max_g: 400,
  };
}

export function defaultSettings(): Settings {
  return {
    schema_version: 1,
    printer: { name: 'Bambu Lab P2S', build_volume_mm: { x: 256, y: 256, z: 256 }, usable_envelope_mm: { x: 240, y: 240, z: 240 } },
    limits: { design_mtow_kg: 24, legal_mtow_kg: 25, warn_mtow_kg: 23 },
    checks: {
      hover_thrust_to_weight_min: 2,
      static_margin_min: 0.05,
      static_margin_max: 0.2,
      cruise_to_stall_speed_ratio_min: 1.3,
      battery_reserve_fraction: 0.2,
      battery_current_max_fraction_of_rating: 0.8,
    },
    units: { system: 'metric' },
  };
}

const r1 = (v: number) => Math.round(v * 10) / 10;

/** Multiply every length by k (rounded to 0.1 mm); angles, counts and allowances unchanged. */
export function scaleLengths(p: DesignParameters, k: number): DesignParameters {
  const s = (v: number) => r1(v * k);
  return {
    ...p,
    wing: { ...p.wing, span_mm: s(p.wing.span_mm), root_chord_mm: s(p.wing.root_chord_mm), tip_chord_mm: s(p.wing.tip_chord_mm), x_le_mm: s(p.wing.x_le_mm), z_mm: s(p.wing.z_mm) },
    fuselage: { ...p.fuselage, length_mm: s(p.fuselage.length_mm), width_mm: s(p.fuselage.width_mm), height_mm: s(p.fuselage.height_mm) },
    booms: { ...p.booms, lateral_offset_mm: s(p.booms.lateral_offset_mm), length_mm: s(p.booms.length_mm), x_offset_mm: s(p.booms.x_offset_mm) },
    motors: { front_x_mm: s(p.motors.front_x_mm), rear_x_mm: s(p.motors.rear_x_mm), height_mm: s(p.motors.height_mm) },
    tilt: { ...p.tilt, axis_x_mm: s(p.tilt.axis_x_mm) },
    pusher: { prop_diameter_mm: s(p.pusher.prop_diameter_mm), x_mm: s(p.pusher.x_mm) },
    tail: { ...p.tail, span_mm: s(p.tail.span_mm), chord_mm: s(p.tail.chord_mm), arm_mm: s(p.tail.arm_mm), height_mm: s(p.tail.height_mm) },
    nose_bay: { length_mm: s(p.nose_bay.length_mm), width_mm: s(p.nose_bay.width_mm), height_mm: s(p.nose_bay.height_mm) },
    landing_gear: { ...p.landing_gear, height_mm: s(p.landing_gear.height_mm) },
    propulsion: p.propulsion && { ...p.propulsion, prop_diameter_mm: s(p.propulsion.prop_diameter_mm), prop_pitch_mm: s(p.propulsion.prop_pitch_mm) },
    battery: p.battery && { ...p.battery, x_mm: s(p.battery.x_mm) },
  };
}

/** Length scale for the 24 kg case: (24 / 2.5)^(1/3), constant-density scaling as a starting point. */
export const FINAL_SCALE_FACTOR = Math.cbrt(24 / 2.5);

export function finalScaleParameters(): DesignParameters {
  const p = scaleLengths(defaultParameters(), FINAL_SCALE_FACTOR); // the Phase 2 document (battery 380 mm scaled), unchanged test case
  return {
    ...p,
    booms: { ...p.booms, diameter_mm: 35 },
    battery: { chemistry: 'li-ion', cells_series: 12, cells_parallel: 8, capacity_mah: 4500, x_mm: p.battery?.x_mm ?? 800 },
    allowances: { avionics_g: 600, wiring_fraction: 0.06 },
  };
}

export function finalScaleMission(): Mission {
  return {
    schema_version: 1,
    scale: 'final',
    target_takeoff_mass_kg: 24,
    target_endurance_min: 120,
    cruise_speed_mps: 20,
    payload_min_g: 1500,
    payload_max_g: 4000,
  };
}

export function quadPusherParameters(): DesignParameters {
  return { ...newProjectParameters(), layout: 'quad_pusher' };
}

export function defaultInput(): EngineInput {
  return { parameters: newProjectParameters(), mission: defaultMission(), settings: defaultSettings(), airfoils: TEST_AIRFOILS };
}

export function finalScaleInput(): EngineInput {
  return { parameters: finalScaleParameters(), mission: finalScaleMission(), settings: defaultSettings(), airfoils: TEST_AIRFOILS };
}

export function quadPusherInput(): EngineInput {
  return { ...defaultInput(), parameters: quadPusherParameters() };
}
