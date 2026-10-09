/**
 * Slider ranges for design and mission fields. The server schema gives hard limits
 * (min/max, often only "positive"); this table narrows them to a range that is useful to drag
 * across, from a small printed prototype to a 24 kg carbon aircraft. The range always widens
 * to include the current value so a slider never misrepresents it.
 */
import type { FieldMeta } from '../api/types';

export interface SliderRange {
  min: number;
  max: number;
  step: number;
}

/** Useful ranges by dotted path (without the "parameters." / "mission." root). */
export const SENSIBLE_RANGES: Record<string, [number, number]> = {
  'wing.span_mm': [300, 6000],
  'wing.root_chord_mm': [50, 800],
  'wing.tip_chord_mm': [20, 800],
  'wing.sweep_deg': [-10, 40],
  'wing.dihedral_deg': [-10, 15],
  'wing.incidence_deg': [-5, 10],
  'wing.twist_deg': [-8, 4],
  'wing.x_le_mm': [0, 2000],
  'wing.z_mm': [-200, 200],
  'fuselage.length_mm': [200, 3000],
  'fuselage.width_mm': [30, 500],
  'fuselage.height_mm': [30, 500],
  'booms.count': [1, 4],
  'booms.lateral_offset_mm': [50, 2000],
  'booms.length_mm': [100, 3000],
  'booms.x_offset_mm': [-1500, 500],
  'booms.diameter_mm': [5, 60],
  'motors.front_x_mm': [0, 1500],
  'motors.rear_x_mm': [0, 3000],
  'motors.height_mm': [-100, 200],
  'tilt.axis_x_mm': [0, 1500],
  'tilt.max_angle_deg': [0, 120],
  'pusher.prop_diameter_mm': [100, 800],
  'pusher.x_mm': [100, 3000],
  'tail.span_mm': [100, 2000],
  'tail.chord_mm': [30, 500],
  'tail.arm_mm': [100, 3000],
  'tail.height_mm': [0, 800],
  'tail.v_angle_deg': [0, 90],
  'nose_bay.length_mm': [30, 800],
  'nose_bay.width_mm': [30, 500],
  'nose_bay.height_mm': [30, 500],
  'landing_gear.height_mm': [0, 600],
  'propulsion.prop_diameter_mm': [100, 1200],
  'propulsion.prop_pitch_mm': [50, 800],
  'propulsion.prop_blades': [2, 6],
  'battery.cells_series': [1, 14],
  'battery.cells_parallel': [1, 10],
  'battery.capacity_mah': [500, 50000],
  'battery.x_mm': [0, 3000],
  'allowances.avionics_g': [0, 2000],
  'allowances.wiring_fraction': [0, 0.2],
  target_takeoff_mass_kg: [0.5, 25],
  target_endurance_min: [5, 180],
  cruise_speed_mps: [8, 40],
  payload_min_g: [0, 3000],
  payload_max_g: [0, 5000],
};

function stepFor(path: string, meta: FieldMeta): number {
  if (meta.type === 'integer') return 1;
  if (path.endsWith('_mm') || path.endsWith('_g') || path.endsWith('_mah')) return 1;
  if (path.endsWith('_deg')) return 0.5;
  if (path.endsWith('_fraction')) return 0.005;
  if (path.endsWith('_kg')) return 0.1;
  if (path.endsWith('_mps')) return 0.5;
  if (path.endsWith('_min')) return 1;
  return 0.01;
}

/**
 * The slider range for a field: the sensible range clipped to the schema limits and widened to
 * include the current value. Returns null for fields with no numeric meaning.
 */
export function sliderRange(path: string, meta: FieldMeta, value: number): SliderRange | null {
  if (meta.type !== 'number' && meta.type !== 'integer') return null;
  const step = stepFor(path, meta);
  const sensible = SENSIBLE_RANGES[path];
  let min: number;
  let max: number;
  if (sensible) {
    [min, max] = sensible;
  } else {
    min = meta.min ?? 0;
    max = meta.max ?? Math.max(1, Number.isFinite(value) ? Math.abs(value) * 2 : 1);
  }
  if (meta.min !== undefined) min = Math.max(min, meta.min);
  if (meta.max !== undefined) max = Math.min(max, meta.max);
  if (Number.isFinite(value)) {
    min = Math.min(min, value);
    max = Math.max(max, value);
  }
  if (!(max > min)) max = min + step;
  return { min, max, step };
}

/** Clamp to the schema limits; a "positive" field (min 0 on a length) stays at least `floor`. */
export function clampToSchema(value: number, meta: FieldMeta | undefined, floor = 0): number {
  let v = value;
  if (meta?.min !== undefined) v = Math.max(v, meta.min === 0 ? floor : meta.min);
  if (meta?.max !== undefined) v = Math.min(v, meta.max);
  return v;
}
