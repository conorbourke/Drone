import { describe, expect, it } from 'vitest';
import type { PhaseSummary } from '../api/flightData';
import {
  comparisonMeaning,
  formatClock,
  formatMeasure,
  formatSignedPct,
  linePath,
  parseGrams,
  phaseFamily,
  rowStatusLabel,
  splitRows,
  timelineSegments,
} from './flightData';

function phase(key: string, start: number, end: number): PhaseSummary {
  return {
    key,
    label: key,
    flight: 1,
    start_s: start,
    end_s: end,
    duration_s: end - start,
    energy_wh: null,
    power_mean_w: null,
    airspeed_median_mps: null,
    decided_by: null,
  };
}

describe('flight data helpers', () => {
  it('groups phases into families', () => {
    expect(phaseFamily('takeoff_hover')).toBe('hover');
    expect(phaseFamily('landing_hover')).toBe('hover');
    expect(phaseFamily('back_transition')).toBe('transition');
    expect(phaseFamily('cruise')).toBe('cruise');
    expect(phaseFamily('weird')).toBe('other');
  });

  it('positions timeline segments as percentages and clamps them', () => {
    const segs = timelineSegments([phase('takeoff_hover', 50, 100), phase('cruise', 100, 450)], 400);
    expect(segs[0].leftPct).toBeCloseTo(12.5);
    expect(segs[0].widthPct).toBeCloseTo(12.5);
    expect(segs[1].leftPct + segs[1].widthPct).toBeCloseTo(100);
  });

  it('formats clocks, measures and signed percentages', () => {
    expect(formatClock(383)).toBe('6:23');
    expect(formatClock(null)).toBe('—');
    expect(formatMeasure(530.15, 'W')).toBe('530 W');
    expect(formatMeasure(9.156, 'Wh')).toBe('9.16 Wh');
    expect(formatMeasure(28.63, 'min')).toBe('28.6 min');
    expect(formatSignedPct(-17.86)).toBe('−17.9 %');
    expect(formatSignedPct(4)).toBe('+4 %');
  });

  it('parses gram entries', () => {
    expect(parseGrams('')).toBeNull();
    expect(parseGrams(' 312,5 ')).toBe(312.5);
    expect(parseGrams('-3')).toBeNaN();
    expect(parseGrams('abc')).toBeNaN();
  });

  it('explains every comparison kind and labels statuses', () => {
    expect(comparisonMeaning({ key: 'hover_power', label: 'Hover power' })).toMatch(/hold the aircraft/);
    expect(comparisonMeaning({ key: 'energy_cruise_1_121', label: 'Energy' })).toMatch(/Energy taken/);
    expect(rowStatusLabel({ status: 'inside', kind: 'lower_bound' })).toBe('Consistent');
    expect(rowStatusLabel({ status: 'outside', kind: 'value' })).toBe('Outside range');
  });

  it('splits energy rows from the headline rows', () => {
    const rows = [{ key: 'hover_power' }, { key: 'energy_cruise_1_2' }] as Parameters<typeof splitRows>[0];
    const { main, energy } = splitRows(rows);
    expect(main).toHaveLength(1);
    expect(energy).toHaveLength(1);
  });

  it('breaks lines at gaps', () => {
    const d = linePath([0, 1, 2, 3], [1, null, 2, 3], (x) => x, (y) => y);
    expect(d).toBe('M0.0,1.0M2.0,2.0L3.0,3.0');
  });
});
