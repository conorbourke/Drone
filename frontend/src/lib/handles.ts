/**
 * Drag handles shared by the SVG drawings and the 3D view. A drag is described by the
 * parameters at drag start plus the pointer displacement in aircraft coordinates (mm; x aft,
 * y starboard, z up). Working from the start snapshot keeps a drag stable while the view
 * re-renders underneath it.
 */
import type { DesignParameters, SchemaMap } from '../api/types';
import { getAtPath } from './draft';
import { clampToSchema } from './ranges';

export type HandleName =
  | 'span'
  | 'root_chord'
  | 'tip_chord'
  | 'wing_x'
  | 'boom_offset'
  | 'tail_arm'
  | 'fuselage_length';

export const HANDLE_NAMES: HandleName[] = [
  'span',
  'root_chord',
  'tip_chord',
  'wing_x',
  'boom_offset',
  'tail_arm',
  'fuselage_length',
];

/** The parameter each handle edits (fuselage_length also shifts the parts measured from the nose). */
export const HANDLE_PATH: Record<HandleName, string> = {
  span: 'wing.span_mm',
  root_chord: 'wing.root_chord_mm',
  tip_chord: 'wing.tip_chord_mm',
  wing_x: 'wing.x_le_mm',
  boom_offset: 'booms.lateral_offset_mm',
  tail_arm: 'tail.arm_mm',
  fuselage_length: 'fuselage.length_mm',
};

export const HANDLE_LABEL: Record<HandleName, string> = {
  span: 'Wingspan',
  root_chord: 'Root chord',
  tip_chord: 'Tip chord',
  wing_x: 'Wing position',
  boom_offset: 'Boom offset',
  tail_arm: 'Tail arm',
  fuselage_length: 'Fuselage length',
};

export interface Delta {
  dx: number;
  dy: number;
  dz: number;
}

export interface HandleResult {
  /** Dotted parameter paths (without "parameters.") and their new values. */
  updates: [string, number][];
  /** The edited value for the label shown while dragging. */
  value: number;
  /** How far the nose moved forward (mm); the views shift by this so the rest stays put. */
  noseShift: number;
}

/** Round to the 1 mm grid, or 10 mm with Shift. */
export function snap(value: number, coarse: boolean): number {
  const grid = coarse ? 10 : 1;
  return Math.round(value / grid) * grid;
}

function num(p: DesignParameters, path: string, fallback = 0): number {
  const v = getAtPath(p, path);
  return typeof v === 'number' && Number.isFinite(v) ? v : fallback;
}

/**
 * New parameter values for a handle dragged by `delta` from the `start` parameters. Values are
 * snapped, kept inside the schema limits and inside the rules the server enforces (tip chord at
 * most the root chord, wing root inside the fuselage).
 */
export function applyHandle(
  name: HandleName,
  start: DesignParameters,
  delta: Delta,
  coarse: boolean,
  schema: SchemaMap | null | undefined,
): HandleResult {
  const meta = (path: string) => schema?.[path];
  const path = HANDLE_PATH[name];
  const s = num(start, path);
  const root = num(start, 'wing.root_chord_mm');
  const tip = num(start, 'wing.tip_chord_mm');
  const xle = num(start, 'wing.x_le_mm');
  const length = num(start, 'fuselage.length_mm');
  const fit = (raw: number, lo: number, hi: number) => {
    const snapped = snap(raw, coarse);
    return Math.min(Math.max(clampToSchema(snapped, meta(path), 1), lo), hi);
  };

  let value: number;
  switch (name) {
    case 'span':
      value = fit(s + 2 * delta.dy, 1, Infinity);
      break;
    case 'root_chord':
      value = fit(s + delta.dx, Math.max(tip, 1), length - xle);
      break;
    case 'tip_chord':
      value = fit(s + delta.dx, 1, root);
      break;
    case 'wing_x':
      value = fit(s + delta.dx, 0, length - root);
      break;
    case 'boom_offset':
      value = fit(s + delta.dy, 1, Infinity);
      break;
    case 'tail_arm':
      value = fit(s + delta.dx, 1, Infinity);
      break;
    case 'fuselage_length': {
      // Pulling the nose forward lengthens the fuselage; everything measured from the nose
      // moves back by the same amount so the rest of the aircraft stays where it is.
      const battery = num(start, 'battery.x_mm', 380);
      const pusher = num(start, 'pusher.x_mm', 1);
      const minShift = -Math.min(xle, battery, pusher - 1);
      value = fit(s - delta.dx, Math.max(1, s + minShift), Infinity);
      const shift = value - s;
      const updates: [string, number][] = [
        ['fuselage.length_mm', value],
        ['wing.x_le_mm', xle + shift],
        ['pusher.x_mm', num(start, 'pusher.x_mm') + shift],
      ];
      if (start.battery) updates.push(['battery.x_mm', battery + shift]);
      return { updates, value, noseShift: shift };
    }
  }
  return { updates: [[path, value]], value, noseShift: 0 };
}
