/**
 * Pure helpers for the Phase 3 analysis panel: staleness against the draft, default names for
 * "Try as new version", threshold text and small number formatting.
 */
import type { AnalysisListItem, Recommendation, ServerQuantity } from '../api/analysis';

type Json = unknown;

function isObject(value: Json): value is Record<string, Json> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function sameLeaf(a: Json, b: Json): boolean {
  if (typeof a === 'number' && typeof b === 'number') return Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(a), Math.abs(b));
  return a === b;
}

/**
 * Dotted paths of every leaf present in both documents whose values differ. Leaves present in
 * only one (a schema default the server filled in, a field a later schema added) are ignored,
 * so only real edits count.
 */
export function changedLeaves(a: Json, b: Json, prefix = ''): string[] {
  if (isObject(a) && isObject(b)) {
    return Object.keys(a)
      .filter((key) => key !== 'schema_version' && key in b)
      .flatMap((key) => changedLeaves(a[key], b[key], prefix ? `${prefix}.${key}` : key));
  }
  if (isObject(a) || isObject(b)) return [prefix];
  if (Array.isArray(a) || Array.isArray(b)) return JSON.stringify(a) === JSON.stringify(b) ? [] : [prefix];
  return sameLeaf(a, b) ? [] : [prefix];
}

/**
 * True when the draft has changed since a draft analysis was queued: it has edits not yet saved,
 * or the server saved it after the analysis was created. (Comparing with `result.inputs` is not
 * reliable: the server fills in defaults and, from Phase 4, the selected parts.) Analyses of
 * saved versions never go stale (versions are immutable). "Analyse" saves pending edits first,
 * so a fresh analysis starts out current.
 */
export function isAnalysisStale(
  item: Pick<AnalysisListItem, 'source' | 'created_at'>,
  draft: { status: 'saved' | 'saving' | 'unsaved' | 'error'; savedAt: string | null },
): boolean {
  if (item.source !== 'draft') return false;
  if (draft.status !== 'saved') return true;
  if (!draft.savedAt) return false;
  const saved = Date.parse(draft.savedAt);
  const created = Date.parse(item.created_at);
  return Number.isFinite(saved) && Number.isFinite(created) && saved > created;
}

/** Short nouns for the parameters recommendations change. */
const SHORT_NAMES: Record<string, string> = {
  'wing.span_mm': 'span',
  'wing.root_chord_mm': 'root chord',
  'wing.tip_chord_mm': 'tip chord',
  'wing.airfoil': 'airfoil',
  'wing.x_le_mm': 'wing position',
  'fuselage.width_mm': 'fuselage width',
  'fuselage.height_mm': 'fuselage height',
  'fuselage.length_mm': 'fuselage length',
  'battery.capacity_mah': 'battery',
  'battery.cells_parallel': 'parallel cells',
  'battery.cells_series': 'series cells',
  'battery.x_mm': 'battery position',
  'propulsion.prop_diameter_mm': 'prop diameter',
  'propulsion.prop_pitch_mm': 'pitch',
  'propulsion.prop_blades': 'blades',
  'tail.span_mm': 'tail span',
  'tail.chord_mm': 'tail chord',
  'tail.arm_mm': 'tail arm',
};

function signed(n: number, digits = 0): string {
  const text = Math.abs(n).toFixed(digits).replace(/\.0+$/, '');
  return `${n >= 0 ? '+' : '−'}${text}`;
}

/** "+1 inch pitch", "+90 mm span", "+1000 mAh battery", "airfoil sd7032". */
export function describeChange(path: string, from: unknown, to: unknown): string {
  const noun =
    SHORT_NAMES[path] ??
    path
      .split('.')
      .pop()!
      .replace(/_(mm|mah|deg|g)$/, '')
      .replace(/_/g, ' ');
  if (typeof from === 'number' && typeof to === 'number') {
    const d = to - from;
    if (path.startsWith('propulsion.prop_') && path.endsWith('_mm')) {
      const inches = d / 25.4;
      const rounded = Math.abs(inches - Math.round(inches)) < 0.05 ? Math.round(inches) : Number(inches.toFixed(1));
      return `${signed(rounded, Number.isInteger(rounded) ? 0 : 1)} inch ${noun}`;
    }
    if (path.endsWith('_mm')) return `${signed(d)} mm ${noun}`;
    if (path.endsWith('_mah')) return `${signed(d)} mAh ${noun}`;
    if (path.endsWith('_deg')) return `${signed(d, 1)}° ${noun}`;
    return `${signed(d, Number.isInteger(d) ? 0 : 2)} ${noun}`;
  }
  return `${noun} ${String(to)}`;
}

/** Default version name for "Try as new version" on a recommendation: "Rec: +1 inch pitch". */
export function recommendationVersionName(
  rec: Pick<Recommendation, 'change' | 'patch' | 'label' | 'key'>,
  prefix = 'Rec',
): string {
  const parts = rec.change
    ? Object.entries(rec.change).map(([path, c]) => describeChange(path, c.from, c.to))
    : Object.entries(rec.patch).map(([path, value]) => describeChange(path, undefined, value));
  const text = parts.length ? parts.join(', ') : (rec.label ?? rec.key);
  return truncateName(`${prefix}: ${text}`);
}

/** Version names are at most 200 characters; keep generated ones well under. */
export function truncateName(name: string, max = 80): string {
  return name.length <= max ? name : `${name.slice(0, max - 1).trimEnd()}…`;
}

/** `base`, else `base (2)`, `base (3)`… avoiding the names already taken. */
export function uniqueName(base: string, taken: Iterable<string>): string {
  const used = new Set([...taken].map((n) => n.trim().toLowerCase()));
  if (!used.has(base.toLowerCase())) return base;
  for (let n = 2; n < 1000; n++) {
    const candidate = `${base} (${n})`;
    if (!used.has(candidate.toLowerCase())) return candidate;
  }
  return `${base} (${Date.now()})`;
}

function fmt(n: number, digits?: number): string {
  if (!Number.isFinite(n)) return '–';
  if (digits === undefined && n !== 0 && Math.abs(n) < 1) {
    // Small coefficients (drag 0.0113) keep three significant figures.
    return n.toLocaleString('en-IE', { maximumSignificantDigits: 3 });
  }
  const d = digits ?? (Math.abs(n) >= 100 ? 0 : Math.abs(n) >= 10 ? 1 : 2);
  return n.toLocaleString('en-IE', { minimumFractionDigits: 0, maximumFractionDigits: d });
}

/** Plain text for a check threshold: a number, a [low, high] band, or an object of named parts. */
export function thresholdText(threshold: unknown, unit = ''): string {
  const u = unit ? ` ${unit}` : '';
  if (typeof threshold === 'number') return `${fmt(threshold)}${u}`;
  if (Array.isArray(threshold) && threshold.length === 2 && threshold.every((t) => typeof t === 'number')) {
    return `${fmt(threshold[0] as number)}–${fmt(threshold[1] as number)}${u}`;
  }
  // Several thresholds in rising order (for example the 23 / 24 / 25 kg mass limits).
  if (Array.isArray(threshold) && threshold.length > 2 && threshold.every((t) => typeof t === 'number')) {
    return `${threshold.map((t) => fmt(t as number)).join(' / ')}${u}`;
  }
  if (isObject(threshold)) {
    return Object.entries(threshold)
      .map(([key, value]) => `${key.replace(/_/g, ' ')} ${thresholdText(value, key.includes('margin') ? '%' : '')}`)
      .join('; ');
  }
  if (threshold === null || threshold === undefined) return '';
  return String(threshold);
}

/** "20.8 min (16–26)": value with the range, for a server Quantity. */
export function quantityShort(q: ServerQuantity | undefined | null, digits?: number): string {
  if (!q || q.value === null || !Number.isFinite(q.value)) return '–';
  const unit = q.unit ? ` ${q.unit}` : '';
  const hasRange = q.low !== null && q.high !== null && Math.abs(q.high - q.low) > 1e-9;
  const range = hasRange ? ` (${fmt(q.low as number, digits)}–${fmt(q.high as number, digits)})` : '';
  return `${fmt(q.value, digits)}${unit}${range}`;
}

export { fmt as formatSig };
