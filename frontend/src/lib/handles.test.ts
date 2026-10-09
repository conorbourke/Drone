import { describe, expect, it } from 'vitest';
import { defaultParameters } from '../engine/__fixtures__/designs';
import type { SchemaMap } from '../api/types';
import { applyHandle, snap } from './handles';
import { sliderRange } from './ranges';

const schema: SchemaMap = {
  'wing.span_mm': { label: 'Wingspan', unit: 'mm', type: 'number', description: '', min: 0 },
  'wing.root_chord_mm': { label: 'Root chord', unit: 'mm', type: 'number', description: '', min: 0 },
  'wing.tip_chord_mm': { label: 'Tip chord', unit: 'mm', type: 'number', description: '', min: 0 },
  'wing.sweep_deg': { label: 'Sweep', unit: '°', type: 'number', description: '', min: -45, max: 60 },
};
const zero = { dx: 0, dy: 0, dz: 0 };

describe('snap', () => {
  it('snaps to 1 mm, or 10 mm with Shift', () => {
    expect(snap(123.4, false)).toBe(123);
    expect(snap(123.6, false)).toBe(124);
    expect(snap(126, true)).toBe(130);
  });
});

describe('applyHandle', () => {
  it('span grows by twice the tip movement', () => {
    const r = applyHandle('span', defaultParameters(), { ...zero, dy: 50.4 }, false, schema);
    expect(r.updates).toEqual([['wing.span_mm', 1901]]);
    expect(r.value).toBe(1901);
  });

  it('tip chord never exceeds the root chord, root never below the tip', () => {
    const p = defaultParameters();
    expect(applyHandle('tip_chord', p, { ...zero, dx: 500 }, false, schema).value).toBe(260);
    expect(applyHandle('root_chord', p, { ...zero, dx: -500 }, false, schema).value).toBe(180);
  });

  it('keeps the wing root inside the fuselage', () => {
    const p = defaultParameters();
    expect(applyHandle('wing_x', p, { ...zero, dx: 1000 }, false, schema).value).toBe(900 - 260);
    expect(applyHandle('wing_x', p, { ...zero, dx: -1000 }, false, schema).value).toBe(0);
    expect(applyHandle('root_chord', p, { ...zero, dx: 1000 }, false, schema).value).toBe(600);
  });

  it('positive lengths stay at least 1 mm', () => {
    const r = applyHandle('boom_offset', defaultParameters(), { ...zero, dy: -5000 }, false, schema);
    expect(r.value).toBe(1);
  });

  it('pulling the nose forward lengthens the fuselage and shifts parts measured from the nose', () => {
    const r = applyHandle('fuselage_length', defaultParameters(), { ...zero, dx: -100 }, true, schema);
    expect(r.noseShift).toBe(100);
    expect(Object.fromEntries(r.updates)).toEqual({
      'fuselage.length_mm': 1000,
      'wing.x_le_mm': 400,
      'pusher.x_mm': 980,
      'battery.x_mm': 480,
    });
  });

  it('the nose cannot be pushed behind the wing leading edge or the battery', () => {
    const r = applyHandle('fuselage_length', defaultParameters(), { ...zero, dx: 2000 }, false, schema);
    expect(r.value).toBe(600); // x_le 300 is the smallest distance from the nose
    expect(Object.fromEntries(r.updates)['wing.x_le_mm']).toBe(0);
  });
});

describe('sliderRange', () => {
  it('uses the sensible range clipped to the schema and widened to the value', () => {
    const meta = schema['wing.sweep_deg'];
    expect(sliderRange('wing.sweep_deg', meta, 0)).toEqual({ min: -10, max: 40, step: 0.5 });
    expect(sliderRange('wing.sweep_deg', meta, 55)).toEqual({ min: -10, max: 55, step: 0.5 });
    expect(sliderRange('wing.span_mm', schema['wing.span_mm'], 9000)?.max).toBe(9000);
  });

  it('falls back to the schema limits for unknown fields', () => {
    const r = sliderRange('x.count', { label: 'n', unit: null, type: 'integer', description: '', min: 1, max: 4 }, 2);
    expect(r).toEqual({ min: 1, max: 4, step: 1 });
    expect(sliderRange('wing.airfoil', { label: 'a', unit: null, type: 'string', description: '' }, 0)).toBeNull();
  });
});
