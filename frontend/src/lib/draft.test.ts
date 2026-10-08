import { describe, expect, it } from 'vitest';
import type { DesignParameters, DraftDocument, Mission } from '../api/types';
import {
  AUTOSAVE_DELAY_MS,
  cloneJson,
  deepEqual,
  draftBasisLabel,
  draftDocument,
  getAtPath,
  isDraftDirty,
  saveStatusLabel,
  setAtPath,
} from './draft';

function sampleParameters(): DesignParameters {
  return {
    schema_version: 1,
    layout: 'front_tilt',
    wing: {
      span_mm: 1800,
      root_chord_mm: 260,
      tip_chord_mm: 180,
      sweep_deg: 0,
      dihedral_deg: 3,
      incidence_deg: 2,
      airfoil: 'sd7037',
      x_le_mm: 300,
      z_mm: 0,
    },
    fuselage: { length_mm: 900, width_mm: 120, height_mm: 110, cross_section: 'ellipse' },
    booms: { count: 2, lateral_offset_mm: 300, length_mm: 700, x_offset_mm: -200 },
    motors: { front_x_mm: 20, rear_x_mm: 680, height_mm: 20 },
    tilt: { axis_x_mm: 20, max_angle_deg: 90 },
    pusher: { prop_diameter_mm: 254, x_mm: 900 },
    tail: { type: 'inverted_v', span_mm: 500, chord_mm: 120, arm_mm: 600, height_mm: 150 },
    nose_bay: { length_mm: 180, width_mm: 100, height_mm: 90 },
    landing_gear: { type: 'skids', height_mm: 120 },
  };
}

function sampleMission(): Mission {
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

function sampleDraft(): DraftDocument {
  return { parameters: sampleParameters(), mission: sampleMission() };
}

describe('deepEqual', () => {
  it('compares primitives', () => {
    expect(deepEqual(1, 1)).toBe(true);
    expect(deepEqual(1, '1')).toBe(false);
    expect(deepEqual(null, null)).toBe(true);
    expect(deepEqual(null, undefined)).toBe(false);
    expect(deepEqual(Number.NaN, Number.NaN)).toBe(true);
    expect(deepEqual(0, -0)).toBe(true);
  });

  it('ignores key order and undefined properties', () => {
    expect(deepEqual({ a: 1, b: 2 }, { b: 2, a: 1 })).toBe(true);
    expect(deepEqual({ a: 1, b: undefined }, { a: 1 })).toBe(true);
    expect(deepEqual({ a: 1 }, { a: 1, b: null })).toBe(false);
  });

  it('compares nested structures and arrays', () => {
    expect(deepEqual({ a: [1, { b: 2 }] }, { a: [1, { b: 2 }] })).toBe(true);
    expect(deepEqual({ a: [1, 2] }, { a: [2, 1] })).toBe(false);
    expect(deepEqual([1, 2], [1, 2, 3])).toBe(false);
    expect(deepEqual({ a: {} }, { a: [] })).toBe(false);
  });

  it('treats a whole draft as equal to its JSON round trip', () => {
    const draft = sampleDraft();
    expect(deepEqual(draft, JSON.parse(JSON.stringify(draft)))).toBe(true);
  });
});

describe('isDraftDirty', () => {
  it('is clean when the draft matches its basis', () => {
    expect(isDraftDirty(sampleDraft(), sampleDraft())).toBe(false);
  });

  it('ignores extra fields on the basis that a version carries', () => {
    const basis = { ...sampleDraft(), id: 7, number: 3, name: 'v3' };
    expect(isDraftDirty(sampleDraft(), basis)).toBe(false);
  });

  it('is dirty after any nested change', () => {
    const edited = setAtPath(sampleDraft(), 'parameters.wing.span_mm', 2000);
    expect(isDraftDirty(edited, sampleDraft())).toBe(true);
    const missionEdited = setAtPath(sampleDraft(), 'mission.target_takeoff_mass_kg', 3);
    expect(isDraftDirty(missionEdited, sampleDraft())).toBe(true);
  });

  it('is not dirty without a basis', () => {
    expect(isDraftDirty(sampleDraft(), null)).toBe(false);
    expect(isDraftDirty(sampleDraft(), undefined)).toBe(false);
  });

  it('is clean again when the change is reverted', () => {
    const edited = setAtPath(sampleDraft(), 'parameters.wing.span_mm', 2000);
    const reverted = setAtPath(edited, 'parameters.wing.span_mm', 1800);
    expect(isDraftDirty(reverted, sampleDraft())).toBe(false);
  });
});

describe('draftBasisLabel', () => {
  it('names the basis version and marks modifications', () => {
    expect(draftBasisLabel(1, false)).toBe('Draft based on v1');
    expect(draftBasisLabel(3, true)).toBe('Draft based on v3 (modified)');
  });

  it('explains when there is no basis', () => {
    expect(draftBasisLabel(null, false)).toBe('Draft not based on a saved version');
    expect(draftBasisLabel(undefined, true)).toBe('Draft not based on a saved version');
  });
});

describe('saveStatusLabel', () => {
  it('matches the contract wording', () => {
    expect(saveStatusLabel('saved')).toBe('Saved');
    expect(saveStatusLabel('saving')).toBe('Saving…');
    expect(saveStatusLabel('unsaved')).toBe('Unsaved changes');
    expect(saveStatusLabel('error', 'tip chord must not exceed root chord')).toBe(
      'Could not save: tip chord must not exceed root chord',
    );
    expect(saveStatusLabel('error')).toBe('Could not save');
  });

  it('uses the contract autosave delay', () => {
    expect(AUTOSAVE_DELAY_MS).toBe(800);
  });
});

describe('paths', () => {
  it('reads dotted paths', () => {
    const draft = sampleDraft();
    expect(getAtPath(draft, 'parameters.wing.span_mm')).toBe(1800);
    expect(getAtPath(draft, 'mission.scale')).toBe('prototype');
    expect(getAtPath(draft, 'parameters.nothing.here')).toBeUndefined();
    expect(getAtPath(null, 'a')).toBeUndefined();
  });

  it('writes immutably, copying only the touched branch', () => {
    const draft = sampleDraft();
    const next = setAtPath(draft, 'parameters.wing.span_mm', 2000);
    expect(next.parameters.wing.span_mm).toBe(2000);
    expect(draft.parameters.wing.span_mm).toBe(1800);
    expect(next.parameters.fuselage).toBe(draft.parameters.fuselage);
    expect(next.mission).toBe(draft.mission);
    expect(next.parameters).not.toBe(draft.parameters);
  });

  it('creates missing intermediate objects', () => {
    const next = setAtPath<Record<string, unknown>>({}, 'a.b.c', 1);
    expect(next).toEqual({ a: { b: { c: 1 } } });
  });

  it('extracts the document and clones it', () => {
    const draft = sampleDraft();
    const doc = draftDocument({ ...draft });
    expect(Object.keys(doc).sort()).toEqual(['mission', 'parameters']);
    const copy = cloneJson(draft);
    expect(copy).toEqual(draft);
    expect(copy).not.toBe(draft);
    expect(copy.parameters.wing).not.toBe(draft.parameters.wing);
  });
});
