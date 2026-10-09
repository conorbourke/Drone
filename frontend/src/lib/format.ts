/**
 * Metric formatting helpers (pure; unit-tested).
 *
 * Numbers are formatted with the en-IE locale so grouping and decimal marks are stable
 * regardless of the browser's locale.
 */

const LOCALE = 'en-IE';

/** Narrow no-break space placed between a number and its unit. */
const NARROW_NBSP = '\u202f';

export interface NumberFormatOptions {
  /** Maximum fraction digits (default 2). Trailing zeros are dropped. */
  maxFractionDigits?: number;
  /** Minimum fraction digits (default 0). */
  minFractionDigits?: number;
}

const formatterCache = new Map<string, Intl.NumberFormat>();

function numberFormatter(options: NumberFormatOptions): Intl.NumberFormat {
  const max = options.maxFractionDigits ?? 2;
  const min = Math.min(options.minFractionDigits ?? 0, max);
  const key = `${min}:${max}`;
  let formatter = formatterCache.get(key);
  if (!formatter) {
    formatter = new Intl.NumberFormat(LOCALE, {
      minimumFractionDigits: min,
      maximumFractionDigits: max,
      useGrouping: true,
    });
    formatterCache.set(key, formatter);
  }
  return formatter;
}

/** "1,800" or "2.5"; non-finite values become an em dash. */
export function formatNumber(value: number | null | undefined, options: NumberFormatOptions = {}): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return numberFormatter(options).format(value);
}

/** Append a unit with a thin space: "1,800 mm". A null unit gives the bare number. */
export function formatWithUnit(
  value: number | null | undefined,
  unit: string | null | undefined,
  options: NumberFormatOptions = {},
): string {
  const text = formatNumber(value, options);
  if (text === '—' || !unit) return text;
  // Percent and degree symbols sit flush against the number.
  if (unit === '%' || unit === '°') return `${text}${unit}`;
  return `${text}${NARROW_NBSP}${unit}`;
}

/** Mass in grams, promoted to kilograms from 1,000 g: "150 g", "2.5 kg". */
export function formatMassG(grams: number | null | undefined): string {
  if (grams === null || grams === undefined || !Number.isFinite(grams)) return '—';
  if (Math.abs(grams) >= 1000) return formatWithUnit(grams / 1000, 'kg', { maxFractionDigits: 2 });
  return formatWithUnit(grams, 'g', { maxFractionDigits: 1 });
}

/** Mass in kilograms: "2.5 kg". */
export function formatMassKg(kg: number | null | undefined): string {
  return formatWithUnit(kg, 'kg', { maxFractionDigits: 2 });
}

/** Length in millimetres, promoted to metres from 1,000 mm: "260 mm", "1.8 m". */
export function formatLengthMm(mm: number | null | undefined): string {
  if (mm === null || mm === undefined || !Number.isFinite(mm)) return '—';
  if (Math.abs(mm) >= 1000) return formatWithUnit(mm / 1000, 'm', { maxFractionDigits: 3 });
  return formatWithUnit(mm, 'mm', { maxFractionDigits: 1 });
}

/** Speed in metres per second with the km/h equivalent: "16 m/s (57.6 km/h)". */
export function formatSpeedMps(mps: number | null | undefined): string {
  if (mps === null || mps === undefined || !Number.isFinite(mps)) return '—';
  const kmh = formatWithUnit(mps * 3.6, 'km/h', { maxFractionDigits: 1 });
  return `${formatWithUnit(mps, 'm/s', { maxFractionDigits: 1 })} (${kmh})`;
}

/** Euro amount: "€1,234.50"; null when unknown. */
export function formatEur(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return `€${formatNumber(value, { minFractionDigits: 2, maxFractionDigits: 2 })}`;
}

/** File size with binary prefixes: "512 B", "1.5 KB", "2.3 MB". */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  const digits = index === 0 ? 0 : value < 10 ? 2 : 1;
  return `${formatNumber(value, { maxFractionDigits: digits })}${NARROW_NBSP}${units[index]}`;
}

export interface DateFormatOptions {
  /** IANA time zone; defaults to the browser's. Tests pass "UTC" for stable output. */
  timeZone?: string;
}

/** ISO timestamp to "8 Oct 2026, 14:05" in the given (or local) time zone. */
export function formatDateTime(iso: string | null | undefined, options: DateFormatOptions = {}): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(LOCALE, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: options.timeZone,
  }).format(date);
}

/** ISO timestamp to "8 Oct 2026". */
export function formatDate(iso: string | null | undefined, options: DateFormatOptions = {}): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(LOCALE, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: options.timeZone,
  }).format(date);
}

/**
 * Unit suffixes recognised at the end of a field name, mapped to display symbols. A trailing
 * "min" is deliberately absent: in this app it means "minimum" (static_margin_min); minutes are
 * declared explicitly through the schema endpoint.
 */
const UNIT_SUFFIXES: Record<string, string> = {
  mm: 'mm',
  m: 'm',
  g: 'g',
  kg: 'kg',
  deg: '°',
  mps: 'm/s',
  kmh: 'km/h',
  s: 's',
  w: 'W',
  wh: 'Wh',
  v: 'V',
  a: 'A',
  mah: 'mAh',
  ohm: 'Ω',
  hz: 'Hz',
  mhz: 'MHz',
  kbps: 'kbit/s',
  km: 'km',
  rpm: 'rpm',
  eur: '€',
  pct: '%',
  gpa: 'GPa',
  mpa: 'MPa',
};

const ACRONYMS: Record<string, string> = {
  mtow: 'MTOW',
  cg: 'CG',
  esc: 'ESC',
  gps: 'GPS',
  rtk: 'RTK',
  pwm: 'PWM',
  can: 'CAN',
  imu: 'IMU',
  bec: 'BEC',
  rc: 'RC',
  los: 'LOS',
  kv: 'Kv',
  id: 'ID',
  xyz: 'XYZ',
};

/** Split "design_mtow_kg" into { base: "design_mtow", unit: "kg" }; unit is null when none. */
export function splitUnitSuffix(key: string): { base: string; unit: string | null } {
  const parts = key.split('_');
  if (parts.length > 1) {
    const last = parts[parts.length - 1]!.toLowerCase();
    const unit = UNIT_SUFFIXES[last];
    if (unit) return { base: parts.slice(0, -1).join('_'), unit };
  }
  return { base: key, unit: null };
}

/** "design_mtow_kg" → "Design MTOW"; "usable_envelope_mm" → "Usable envelope". */
export function humanizeKey(key: string): string {
  const last = key.split('.').pop() ?? key;
  const { base } = splitUnitSuffix(last);
  const words = base
    .split('_')
    .filter(Boolean)
    .map((word) => ACRONYMS[word.toLowerCase()] ?? word.toLowerCase());
  if (words.length === 0) return key;
  const first = words[0]!;
  words[0] = ACRONYMS[first.toLowerCase()] ?? first.charAt(0).toUpperCase() + first.slice(1);
  return words.join(' ');
}

/** Display unit for a field name, or null: "warn_mtow_kg" → "kg". */
export function unitForKey(key: string): string | null {
  const last = key.split('.').pop() ?? key;
  return splitUnitSuffix(last).unit;
}

/** "1 version" / "3 versions". */
export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${formatNumber(count, { maxFractionDigits: 0 })} ${count === 1 ? singular : plural}`;
}

/** Trim a string for display, adding an ellipsis past the limit. */
export function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
}
