/**
 * Pure helpers for the Flight data tab: phase colours and plain labels, timeline geometry,
 * comparison-row wording, and number formatting for the predicted-versus-measured table.
 */
import type { ComparisonRow, PhaseSummary } from '../api/flightData';

/** Phase families share a colour: hover (take-off, landing), transitions, wing flight. */
export type PhaseFamily = 'hover' | 'transition' | 'cruise' | 'other';

export function phaseFamily(key: string): PhaseFamily {
  if (key === 'takeoff_hover' || key === 'landing_hover' || key === 'hover') return 'hover';
  if (key === 'transition' || key === 'back_transition' || key === 'vtol_forward') return 'transition';
  if (key === 'cruise' || key === 'vtol_assist' || key === 'fw_climb' || key === 'fw_descent') return 'cruise';
  return 'other';
}

export const PHASE_FAMILY_LABEL: Record<PhaseFamily, string> = {
  hover: 'Hover (lift motors)',
  transition: 'Transition',
  cruise: 'Wing flight',
  other: 'Other',
};

/** Short label for a timeline segment (the full label goes in the tooltip). */
export function shortPhaseLabel(key: string, label: string): string {
  const short: Record<string, string> = {
    takeoff_hover: 'Take-off',
    transition: 'Transition',
    cruise: 'Cruise',
    back_transition: 'Back-transition',
    landing_hover: 'Landing',
    hover: 'Hover',
  };
  return short[key] ?? label;
}

export interface TimelineSegment {
  phase: PhaseSummary;
  family: PhaseFamily;
  leftPct: number;
  widthPct: number;
}

/** Segments positioned as percentages of the log duration (clamped, never negative). */
export function timelineSegments(phases: PhaseSummary[], durationS: number): TimelineSegment[] {
  const total = durationS > 0 ? durationS : Math.max(1, ...phases.map((p) => p.end_s));
  return phases.map((phase) => {
    const start = Math.max(0, Math.min(total, phase.start_s));
    const end = Math.max(start, Math.min(total, phase.end_s));
    return {
      phase,
      family: phaseFamily(phase.key),
      leftPct: (start / total) * 100,
      widthPct: ((end - start) / total) * 100,
    };
  });
}

/** m:ss for durations and times into the log. */
export function formatClock(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—';
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** Digits for a value in a unit: W and Wh to whole numbers above 100, else a little more. */
export function formatMeasure(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  const abs = Math.abs(value);
  const digits = unit === 'min' ? 1 : abs >= 100 ? 0 : abs >= 10 ? 1 : 2;
  const text = value.toLocaleString('en-IE', { maximumFractionDigits: digits, minimumFractionDigits: 0 });
  return unit ? `${text} ${unit}` : text;
}

export function formatSignedPct(pct: number | null | undefined): string {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return '—';
  const r = Math.round(pct * 10) / 10;
  return `${r > 0 ? '+' : r < 0 ? '−' : '±'}${Math.abs(r).toLocaleString('en-IE', { maximumFractionDigits: 1 })} %`;
}

/** What each comparison measures, in plain words (keyed by the row key or its family). */
export function comparisonMeaning(row: Pick<ComparisonRow, 'key' | 'label'>): string {
  const k = row.key;
  if (k === 'hover_power')
    return 'Battery power needed to hold the aircraft still in the air on its lift motors. It sets how much of the battery the take-off and landing use.';
  if (k === 'hover_current')
    return 'Battery current while hovering. It is what the battery, wiring and ESCs must carry continuously during take-off and landing.';
  if (k === 'transition_peak_power')
    return 'The highest power during the transition from hover to wing flight, when the lift motors still carry weight while the aircraft speeds up. It sizes the battery’s burst current.';
  if (k === 'cruise_power')
    return 'Battery power in steady level wing flight at the airspeed actually flown. It decides endurance and range more than anything else.';
  if (k.startsWith('energy_'))
    return 'Energy taken from the battery during this phase (power × time), predicted from the model’s power over the same measured duration.';
  if (k === 'endurance_cruise')
    return 'How long the aircraft could fly on the wing with the design’s battery, worked out from the measured powers instead of the predicted ones (same hover time and transitions as the mission).';
  if (k === 'transition_speed')
    return 'Airspeed at which the autopilot finished the transition. The wing must carry the weight by then, so it should be at or above the predicted speed where the wing lifts the aircraft.';
  if (k === 'stall_speed')
    return 'The lowest airspeed the aircraft flew on the wing alone. The real stall speed is at or below it, so this only checks the prediction is not contradicted.';
  return row.label;
}

export type RowTone = 'ok' | 'warn' | 'neutral';

export function rowTone(row: Pick<ComparisonRow, 'status'>): RowTone {
  return row.status === 'inside' ? 'ok' : row.status === 'outside' ? 'warn' : 'neutral';
}

export function rowStatusLabel(row: Pick<ComparisonRow, 'status' | 'kind'>): string {
  if (row.status === 'unavailable') return 'No data';
  if (row.kind === 'lower_bound') return row.status === 'inside' ? 'Consistent' : 'Contradicts';
  return row.status === 'inside' ? 'Inside range' : 'Outside range';
}

/** The comparison rows grouped for display: headline rows first, per-phase energies after. */
export function splitRows(rows: ComparisonRow[]): { main: ComparisonRow[]; energy: ComparisonRow[] } {
  return {
    main: rows.filter((r) => !r.key.startsWith('energy_')),
    energy: rows.filter((r) => r.key.startsWith('energy_')),
  };
}

/** Parse a gram entry from a text field; null for blank, NaN for invalid. */
export function parseGrams(text: string): number | null {
  const t = text.trim().replace(',', '.');
  if (t === '') return null;
  const n = Number(t);
  return Number.isFinite(n) && n > 0 && n <= 30000 ? n : Number.NaN;
}

/** Evenly spaced points with gaps (null) kept as breaks in the line. */
export function linePath(
  xs: number[],
  ys: Array<number | null>,
  X: (x: number) => number,
  Y: (y: number) => number,
): string {
  let d = '';
  let pen = false;
  for (let i = 0; i < xs.length; i++) {
    const y = ys[i];
    if (y === null || y === undefined || !Number.isFinite(y)) {
      pen = false;
      continue;
    }
    d += `${pen ? 'L' : 'M'}${X(xs[i]).toFixed(1)},${Y(y).toFixed(1)}`;
    pen = true;
  }
  return d;
}
