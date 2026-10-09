import { describe, expect, it } from 'vitest';
import { defaultParameters } from '../engine/__fixtures__/designs';
import type { DesignParameters, SchemaMap } from '../api/types';
import { getAtPath, setAtPath } from './draft';
import { mergeProposal } from './proposal';

const schema: SchemaMap = {
  'wing.span_mm': { label: 'Wingspan', unit: 'mm', type: 'number', description: '', min: 0 },
  'wing.root_chord_mm': { label: 'Root chord', unit: 'mm', type: 'number', description: '', min: 0 },
  'wing.tip_chord_mm': { label: 'Tip chord', unit: 'mm', type: 'number', description: '', min: 0 },
  'wing.x_le_mm': { label: 'Wing position', unit: 'mm', type: 'number', description: '', min: 0 },
  'wing.sweep_deg': { label: 'Sweep', unit: '°', type: 'number', description: '', min: -45, max: 60 },
  'fuselage.length_mm': { label: 'Fuselage length', unit: 'mm', type: 'number', description: '', min: 0 },
};

/** The server's cross-field rules (backend/app/schemas/design.py). */
function passesServerRules(p: DesignParameters): boolean {
  return (
    p.wing.tip_chord_mm <= p.wing.root_chord_mm &&
    p.wing.x_le_mm + p.wing.root_chord_mm <= p.fuselage.length_mm &&
    p.motors.rear_x_mm > p.motors.front_x_mm
  );
}

function applied(base: DesignParameters, updates: [string, unknown][]): DesignParameters {
  return updates.reduce((doc, [path, value]) => setAtPath(doc, path, value), base);
}

describe('mergeProposal', () => {
  it('applies a consistent selection unchanged, with no notes', () => {
    const base = defaultParameters();
    const r = mergeProposal(base, [{ path: 'wing.span_mm', value: 2000 }, { path: 'layout', value: 'quad_pusher' }], schema);
    expect(r.blocked).toBeNull();
    expect(r.adjustments).toEqual([]);
    expect(r.updates).toEqual([
      ['wing.span_mm', 2000],
      ['layout', 'quad_pusher'],
    ]);
  });

  it('cuts a selected tip chord that is wider than the current root chord, and says so', () => {
    const base = defaultParameters(); // root 260
    const r = mergeProposal(base, [{ path: 'wing.tip_chord_mm', value: 300 }], schema);
    expect(r.blocked).toBeNull();
    expect(r.updates).toContainEqual(['wing.tip_chord_mm', 260]);
    expect(r.adjustments).toHaveLength(1);
    expect(r.adjustments[0]).toMatch(/Tip chord.*300 mm.*260 mm/);
    expect(passesServerRules(applied(base, r.updates))).toBe(true);
  });

  it('cuts the current tip chord when only a narrower root chord is selected', () => {
    const base = defaultParameters(); // tip 180
    const r = mergeProposal(base, [{ path: 'wing.root_chord_mm', value: 150 }], schema);
    const after = applied(base, r.updates);
    expect(after.wing.tip_chord_mm).toBe(150);
    expect(passesServerRules(after)).toBe(true);
  });

  it('moves the wing forward when a selected root chord would run past the fuselage end', () => {
    const base = defaultParameters(); // x_le 300, length 900
    const r = mergeProposal(base, [{ path: 'wing.root_chord_mm', value: 700 }], schema);
    const after = applied(base, r.updates);
    expect(after.wing.x_le_mm).toBe(200);
    expect(after.wing.root_chord_mm).toBe(700);
    expect(r.adjustments.some((a) => a.startsWith('Wing position'))).toBe(true);
    expect(passesServerRules(after)).toBe(true);
  });

  it('shortens the root chord when even a wing at the nose would not fit a shorter fuselage', () => {
    const base = defaultParameters();
    const r = mergeProposal(base, [{ path: 'fuselage.length_mm', value: 200 }], schema);
    const after = applied(base, r.updates);
    expect(after.wing.x_le_mm).toBe(0);
    expect(after.wing.root_chord_mm).toBe(200);
    expect(after.wing.tip_chord_mm).toBe(180);
    expect(r.adjustments).toHaveLength(2);
    expect(passesServerRules(after)).toBe(true);
  });

  it('keeps numbers inside the schema limits', () => {
    const base = defaultParameters();
    const r = mergeProposal(base, [{ path: 'wing.sweep_deg', value: 80 }], schema);
    expect(r.updates).toContainEqual(['wing.sweep_deg', 60]);
    expect(r.adjustments[0]).toMatch(/Sweep/);
  });

  it('blocks a selection that puts the rear motors ahead of the front motors', () => {
    const base = defaultParameters();
    const rear = getAtPath(base, 'motors.rear_x_mm') as number;
    const r = mergeProposal(base, [{ path: 'motors.front_x_mm', value: rear + 10 }], schema);
    expect(r.blocked).toMatch(/rear motors/);
    expect(r.updates).toEqual([]);
  });

  it('does not change the input parameters', () => {
    const base = defaultParameters();
    const before = JSON.stringify(base);
    mergeProposal(base, [{ path: 'wing.tip_chord_mm', value: 400 }], schema);
    expect(JSON.stringify(base)).toBe(before);
  });
});
