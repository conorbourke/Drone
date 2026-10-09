/**
 * Layout comparison: the same design re-estimated for each of the three layouts, with a
 * complexity rating and the ArduPilot support note (docs/phases/PHASE2.md section 5).
 */

import type { DesignParameters, Layout } from '../api/types';
import { estimate } from './estimate';
import { withDefaults } from './geometry';
import { qNaN } from './quantity';
import type { EngineInput, LayoutComparison, Quantity } from './types';

export const LAYOUTS: Layout[] = ['front_tilt', 'rear_tilt', 'quad_pusher'];

export const LAYOUT_LABELS: Record<Layout, string> = {
  front_tilt: 'Front tilt',
  rear_tilt: 'Rear tilt',
  quad_pusher: 'Quad + pusher',
};

/** ArduPilot support notes (ArduPilot QuadPlane documentation: tiltrotor and standard quadplane frames). */
export const ARDUPILOT_NOTES: Record<Layout, string> = {
  front_tilt:
    'Supported by ArduPilot QuadPlane as a tiltrotor (the front motors are listed in Q_TILT_MASK). The common tiltrotor arrangement, with many reference builds.',
  rear_tilt:
    'Less common in ArduPilot than front tilt. QuadPlane tiltrotor support lets any motors tilt, but there are few reference builds and less tuning experience, so expect more setup work.',
  quad_pusher:
    'Standard ArduPilot QuadPlane (quad frame plus a forward-thrust motor). The best-supported layout and the simplest to set up and tune.',
};

const COMPLEXITY: Record<Layout, { rating: 'low' | 'medium' | 'high'; score: number; reasons: string[] }> = {
  front_tilt: {
    rating: 'medium',
    score: 2,
    reasons: [
      'Tilt mechanism: two servos and hinges that must be stiff, slop-free and strong enough for full thrust.',
      'Transition needs the tilt schedule tuned in ArduPilot.',
      'No extra motor: the front motors also pull in cruise, while the rear pair stops.',
    ],
  },
  rear_tilt: {
    rating: 'high',
    score: 3,
    reasons: [
      'Tilt mechanism: two servos and hinges that must be stiff, slop-free and strong enough for full thrust.',
      'Transition needs the tilt schedule tuned, with fewer reference builds to copy (less common in ArduPilot).',
      'No extra motor: the rear motors push in cruise, while the front pair stops.',
    ],
  },
  quad_pusher: {
    rating: 'low',
    score: 1,
    reasons: [
      'No tilt mechanism: every lift motor is fixed.',
      'An extra pusher motor, ESC and propeller (a little heavier).',
      'Simplest transition: the pusher accelerates the aircraft and the lift motors slow down as the wing takes over.',
      'All four lift propellers are stopped in cruise, adding some drag.',
    ],
  },
};

/**
 * Adapt the design to a layout the way a designer would before comparing: for rear tilt the tilt
 * axis keeps its offset from the tilting motors (so it moves to the rear pair), and the battery is
 * moved so the balance point (heaviest camera) matches the current design's. Without this the
 * comparison would mostly show a CG shift, not the layout. Positions are rounded to whole
 * millimetres so the numbers shown are exactly those of the design "Use this layout" produces.
 */
function adaptToLayout(
  input: EngineInput,
  layout: Layout,
  targetCgX: number,
): { input: EngineInput; batteryX: number; notes: string[]; changes: [string, unknown][] } {
  const base = withDefaults(input.parameters);
  const notes: string[] = [];
  const from = base.layout;
  let tilt = base.tilt;
  if (layout !== 'quad_pusher' && from !== layout) {
    const fromMotor = from === 'rear_tilt' ? base.motors.rear_x_mm : base.motors.front_x_mm;
    const toMotor = layout === 'rear_tilt' ? base.motors.rear_x_mm : base.motors.front_x_mm;
    tilt = { ...base.tilt, axis_x_mm: Math.round(toMotor + (base.tilt.axis_x_mm - fromMotor)) };
    notes.push(`Tilt axis moved to the ${layout === 'rear_tilt' ? 'rear' : 'front'} motors (${Math.round(tilt.axis_x_mm)} mm along the boom).`);
  }
  let params: DesignParameters = { ...base, layout, tilt };
  let batteryX = base.battery.x_mm;
  if (layout !== from && Number.isFinite(targetCgX)) {
    const lo = base.nose_bay.length_mm;
    const hi = base.fuselage.length_mm;
    for (let i = 0; i < 3; i++) {
      const est = estimate({ ...input, parameters: params });
      if (!est.balance || !est.mass) break;
      const cg = est.balance.cg_max_payload_x.value;
      const total = est.mass.takeoff_max_payload.value;
      const bat = est.mass.battery.value;
      if (!(bat > 0) || Math.abs(cg - targetCgX) < 0.5) break;
      batteryX = Math.round(Math.min(hi, Math.max(lo, batteryX + ((targetCgX - cg) * total) / bat)));
      params = { ...params, battery: { ...base.battery, x_mm: batteryX } };
    }
    if (Math.abs(batteryX - base.battery.x_mm) >= 1) {
      notes.push(`Battery moved from ${Math.round(base.battery.x_mm)} to ${Math.round(batteryX)} mm to keep the same balance point as your current design.`);
    } else {
      batteryX = base.battery.x_mm;
      params = { ...params, battery: base.battery };
    }
  }
  const changes: [string, unknown][] = [['layout', layout]];
  if (tilt.axis_x_mm !== base.tilt.axis_x_mm) changes.push(['tilt.axis_x_mm', tilt.axis_x_mm]);
  if (batteryX !== base.battery.x_mm) changes.push(['battery.x_mm', batteryX]);
  return { input: { ...input, parameters: params }, batteryX, notes, changes };
}

/** Re-estimate the design for each layout (re-balanced, see adaptToLayout). Never throws. */
export function compareLayouts(input: EngineInput): LayoutComparison[] {
  let targetCg = NaN;
  try {
    targetCg = estimate(input).balance?.cg_max_payload_x.value ?? NaN;
  } catch {
    targetCg = NaN;
  }
  return LAYOUTS.map((layout) => {
    let adapted: ReturnType<typeof adaptToLayout>;
    try {
      adapted = adaptToLayout(input, layout, targetCg);
    } catch {
      adapted = { input: { ...input, parameters: { ...input.parameters, layout } }, batteryX: NaN, notes: [], changes: [['layout', layout]] };
    }
    const est = estimate(adapted.input);
    const missing = (label: string, unit: string): Quantity => qNaN({ unit, label, explain: 'Not available: see the problems listed for this layout.', source: 'Tier 1 estimate.' });
    return {
      layout,
      label: LAYOUT_LABELS[layout],
      takeoff_mass: est.mass?.takeoff_max_payload ?? missing('Take-off mass', 'kg'),
      endurance: est.performance?.endurance_cruise ?? missing('Wing-flight endurance', 'min'),
      cruise_power: est.performance?.cruise_power ?? missing('Cruise power', 'W'),
      hover_power: est.performance?.hover_power ?? missing('Hover power', 'W'),
      complexity: { ...COMPLEXITY[layout], reasons: [...COMPLEXITY[layout].reasons] },
      has_tilt_mechanism: layout !== 'quad_pusher',
      ardupilot_note: ARDUPILOT_NOTES[layout],
      adjustments: adapted.notes,
      changes: adapted.changes,
      battery_x_mm: adapted.batteryX,
      problems: est.statuses.filter((s) => s.level === 'fail' || s.level === 'warn'),
    };
  });
}
