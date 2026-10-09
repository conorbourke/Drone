import { describe, expect, it } from 'vitest';
import {
  changedLeaves,
  describeChange,
  isAnalysisStale,
  quantityShort,
  recommendationVersionName,
  thresholdText,
  uniqueName,
} from './analysis';

describe('isAnalysisStale', () => {
  const item = { source: 'draft' as const, created_at: '2026-10-09T10:00:00Z' };
  it('is current when the draft was last saved before the analysis was queued', () => {
    expect(isAnalysisStale(item, { status: 'saved', savedAt: '2026-10-09T09:59:59Z' })).toBe(false);
    expect(isAnalysisStale(item, { status: 'saved', savedAt: null })).toBe(false);
  });
  it('is stale after a later save or while edits are pending', () => {
    expect(isAnalysisStale(item, { status: 'saved', savedAt: '2026-10-09T10:00:05Z' })).toBe(true);
    expect(isAnalysisStale(item, { status: 'unsaved', savedAt: '2026-10-09T09:00:00Z' })).toBe(true);
    expect(isAnalysisStale(item, { status: 'saving', savedAt: '2026-10-09T09:00:00Z' })).toBe(true);
  });
  it('never marks a version analysis stale', () => {
    expect(isAnalysisStale({ ...item, source: 'version' }, { status: 'unsaved', savedAt: '2026-10-09T11:00:00Z' })).toBe(false);
  });
  it('lists changed leaves', () => {
    expect(changedLeaves({ a: { b: 1, c: 'x' } }, { a: { b: 2, c: 'x' } })).toEqual(['a.b']);
    expect(changedLeaves({ a: 1800 }, { a: 1800.0, extra: 1 })).toEqual([]);
  });
});

describe('recommendation names', () => {
  it('uses inches for propeller sizes', () => {
    expect(
      recommendationVersionName({
        key: 'prop_pitch+',
        patch: { 'propulsion.prop_pitch_mm': 165.4 },
        change: { 'propulsion.prop_pitch_mm': { from: 140, to: 165.4 } },
      }),
    ).toBe('Rec: +1 inch pitch');
  });
  it('describes other changes in their units', () => {
    expect(describeChange('wing.span_mm', 1800, 1890)).toBe('+90 mm span');
    expect(describeChange('fuselage.height_mm', 120, 108)).toBe('−12 mm fuselage height');
    expect(describeChange('battery.capacity_mah', 5000, 6000)).toBe('+1000 mAh battery');
    expect(describeChange('wing.airfoil', 'sd7037', 'sd7032')).toBe('airfoil sd7032');
    expect(describeChange('battery.cells_parallel', 1, 2)).toBe('+1 parallel cells');
  });
  it('falls back to the patch when there is no change block', () => {
    expect(recommendationVersionName({ key: 'k', patch: { 'battery.x_mm': 250 } }, 'Fix')).toBe('Fix: battery position 250');
  });
  it('picks a free name', () => {
    expect(uniqueName('Rec: +1 inch pitch', ['v1'])).toBe('Rec: +1 inch pitch');
    expect(uniqueName('Rec: +1 inch pitch', ['rec: +1 inch pitch', 'Rec: +1 inch pitch (2)'])).toBe('Rec: +1 inch pitch (3)');
  });
});

describe('formatting', () => {
  it('formats thresholds', () => {
    expect(thresholdText(2, '')).toBe('2');
    expect(thresholdText([5, 20], '% MAC')).toBe('5–20 % MAC');
    expect(thresholdText({ static_margin: [5, 20], max_pair_share: 0.65 })).toBe('static margin 5–20 %; max pair share 0.65');
    expect(thresholdText(null)).toBe('');
    expect(thresholdText([23, 24, 25], 'kg')).toBe('23 / 24 / 25 kg');
  });
  it('formats a quantity with its range', () => {
    expect(quantityShort({ value: 20.77, low: 16.1, high: 25.6, unit: 'min', label: '', explain: '', source: '' })).toBe(
      '20.8 min (16.1–25.6)',
    );
    expect(quantityShort(null)).toBe('–');
  });
});
