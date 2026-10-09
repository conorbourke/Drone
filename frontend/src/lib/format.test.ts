import { describe, expect, it } from 'vitest';
import {
  formatBytes,
  formatDate,
  formatDateTime,
  formatEur,
  formatLengthMm,
  formatMassG,
  formatMassKg,
  formatNumber,
  formatSpeedMps,
  formatWithUnit,
  humanizeKey,
  pluralize,
  splitUnitSuffix,
  truncate,
  unitForKey,
} from './format';

describe('formatNumber', () => {
  it('groups thousands and trims trailing zeros', () => {
    expect(formatNumber(1800)).toBe('1,800');
    expect(formatNumber(2.5)).toBe('2.5');
    expect(formatNumber(2.123456)).toBe('2.12');
  });

  it('honours fraction digit options', () => {
    expect(formatNumber(2, { minFractionDigits: 2 })).toBe('2.00');
    expect(formatNumber(0.123456, { maxFractionDigits: 4 })).toBe('0.1235');
  });

  it('renders missing values as an em dash', () => {
    expect(formatNumber(null)).toBe('—');
    expect(formatNumber(undefined)).toBe('—');
    expect(formatNumber(Number.NaN)).toBe('—');
    expect(formatNumber(Number.POSITIVE_INFINITY)).toBe('—');
  });
});

describe('formatWithUnit', () => {
  it('separates units with a narrow no-break space', () => {
    expect(formatWithUnit(1800, 'mm')).toBe('1,800 mm');
  });

  it('keeps degree and percent flush', () => {
    expect(formatWithUnit(3, '°')).toBe('3°');
    expect(formatWithUnit(80, '%')).toBe('80%');
  });

  it('omits a missing unit', () => {
    expect(formatWithUnit(2, null)).toBe('2');
    expect(formatWithUnit(null, 'mm')).toBe('—');
  });
});

describe('metric helpers', () => {
  it('promotes grams to kilograms from 1000 g', () => {
    expect(formatMassG(150)).toBe('150 g');
    expect(formatMassG(2500)).toBe('2.5 kg');
    expect(formatMassG(null)).toBe('—');
  });

  it('formats kilograms', () => {
    expect(formatMassKg(24)).toBe('24 kg');
  });

  it('promotes millimetres to metres from 1000 mm', () => {
    expect(formatLengthMm(260)).toBe('260 mm');
    expect(formatLengthMm(1800)).toBe('1.8 m');
    expect(formatLengthMm(1234)).toBe('1.234 m');
  });

  it('shows speed with the km/h equivalent', () => {
    expect(formatSpeedMps(16)).toBe('16 m/s (57.6 km/h)');
  });

  it('formats euro amounts with two decimals', () => {
    expect(formatEur(1234.5)).toBe('€1,234.50');
    expect(formatEur(null)).toBe('—');
  });
});

describe('formatBytes', () => {
  it('uses binary prefixes', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(formatBytes(2.3 * 1024 * 1024)).toBe('2.3 MB');
    expect(formatBytes(15 * 1024 * 1024)).toBe('15 MB');
  });

  it('rejects negatives and missing values', () => {
    expect(formatBytes(-1)).toBe('—');
    expect(formatBytes(undefined)).toBe('—');
  });
});

describe('dates', () => {
  it('formats ISO timestamps in the requested zone', () => {
    expect(formatDateTime('2026-10-08T14:05:00Z', { timeZone: 'UTC' })).toBe('8 Oct 2026, 14:05');
    expect(formatDate('2026-10-08T14:05:00Z', { timeZone: 'UTC' })).toBe('8 Oct 2026');
  });

  it('handles empty and invalid input', () => {
    expect(formatDateTime(null)).toBe('—');
    expect(formatDateTime('not a date')).toBe('—');
    expect(formatDate('')).toBe('—');
  });
});

describe('field-name helpers', () => {
  it('splits unit suffixes', () => {
    expect(splitUnitSuffix('design_mtow_kg')).toEqual({ base: 'design_mtow', unit: 'kg' });
    expect(splitUnitSuffix('cruise_speed_mps')).toEqual({ base: 'cruise_speed', unit: 'm/s' });
    expect(splitUnitSuffix('sweep_deg')).toEqual({ base: 'sweep', unit: '°' });
    expect(splitUnitSuffix('name')).toEqual({ base: 'name', unit: null });
    // A trailing "min" means minimum, never minutes.
    expect(splitUnitSuffix('static_margin_min')).toEqual({ base: 'static_margin_min', unit: null });
  });

  it('humanizes keys and keeps acronyms', () => {
    expect(humanizeKey('design_mtow_kg')).toBe('Design MTOW');
    expect(humanizeKey('printer.usable_envelope_mm')).toBe('Usable envelope');
    expect(humanizeKey('hover_thrust_to_weight_min')).toBe('Hover thrust to weight min');
    expect(humanizeKey('static_margin_min')).toBe('Static margin min');
    expect(humanizeKey('static_margin_max')).toBe('Static margin max');
    expect(humanizeKey('battery_reserve_fraction')).toBe('Battery reserve fraction');
  });

  it('extracts units from dotted keys', () => {
    expect(unitForKey('limits.warn_mtow_kg')).toBe('kg');
    expect(unitForKey('printer.name')).toBeNull();
    expect(unitForKey('checks.static_margin_min')).toBeNull();
    expect(unitForKey('mission.target_endurance_min')).toBeNull();
  });
});

describe('text helpers', () => {
  it('pluralizes', () => {
    expect(pluralize(1, 'version')).toBe('1 version');
    expect(pluralize(3, 'version')).toBe('3 versions');
    expect(pluralize(0, 'entry', 'entries')).toBe('0 entries');
  });

  it('truncates with an ellipsis', () => {
    expect(truncate('short', 10)).toBe('short');
    expect(truncate('a fairly long sentence', 10)).toBe('a fairly…');
  });
});
