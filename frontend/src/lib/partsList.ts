/**
 * Pure helpers for the Parts tab (unit-tested): grouping by system, link and refresh states,
 * flag chips, relative dates, budget meter geometry, alternative deltas and the messages for
 * the supplier-lookup errors.
 */
import { ApiError, errorMessage } from '../api/client';
import type { AlternativeDeltas, PartsListListing, PartsListRole, PartsListTotals, PartsSystem } from '../api/partsList';
import type { ListingsRefreshStatus, PartsMasses } from '../api/types';
import { PARTS_MASS_LABELS } from '../engine/mass';
import { formatEur, formatNumber, formatWithUnit } from './format';

export const SYSTEM_ORDER: PartsSystem[] = ['propulsion', 'energy', 'flight_control', 'structure'];

const SYSTEM_FALLBACK: Record<string, string> = {
  propulsion: 'Propulsion',
  energy: 'Energy',
  flight_control: 'Flight control',
  structure: 'Structure',
  consumables: 'Consumables',
};

export function systemLabel(system: string, labels?: Record<string, string>): string {
  return labels?.[system] ?? SYSTEM_FALLBACK[system] ?? system;
}

/** Roles grouped by system in the fixed order; empty systems are left out; unknown systems go last. */
export function groupBySystem(roles: PartsListRole[]): { system: string; roles: PartsListRole[] }[] {
  const order: string[] = [...SYSTEM_ORDER];
  for (const r of roles) if (!order.includes(r.system)) order.push(r.system);
  return order
    .map((system) => ({ system, roles: roles.filter((r) => r.system === system) }))
    .filter((g) => g.roles.length > 0);
}

export type Tone = 'neutral' | 'ok' | 'warn' | 'error' | 'info';

export interface LinkState {
  key: 'working' | 'unchecked' | 'broken' | 'stale';
  label: string;
  tone: Tone;
  explain: string;
}

/** Link status of a listing: a failed check wins, then stale, then a working check, else not checked. */
export function linkState(listing: Pick<PartsListListing, 'url_ok' | 'stale' | 'url_status'>): LinkState {
  if (listing.url_ok === false) {
    return {
      key: 'broken',
      label: 'Link not working',
      tone: 'error',
      explain: `The shop page did not answer when the server last checked it${listing.url_status ? ` (HTTP ${listing.url_status})` : ''}. Check prices now to look for a current page.`,
    };
  }
  if (listing.stale) {
    return {
      key: 'stale',
      label: 'Stale',
      tone: 'warn',
      explain: 'Not checked in the last 30 days: the price and stock may have changed.',
    };
  }
  if (listing.url_ok === true) {
    return { key: 'working', label: 'Link working', tone: 'ok', explain: 'The shop page answered when the server last checked it.' };
  }
  return {
    key: 'unchecked',
    label: 'Link not checked',
    tone: 'neutral',
    explain: 'The server has not opened this link yet. "Check prices now" looks the part up and checks every link.',
  };
}

/** "today", "yesterday", "3 days ago", "2 months ago" from an ISO date (relative to `now`). */
export function relativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return 'never';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return 'never';
  const diffMs = now.getTime() - t;
  const minutes = Math.round(diffMs / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.floor(diffMs / 86400000);
  if (days <= 1) return 'yesterday';
  if (days < 31) return `${days} days ago`;
  const months = Math.floor(days / 30.44);
  if (months < 12) return `${months} ${months === 1 ? 'month' : 'months'} ago`;
  const years = Math.floor(days / 365.25);
  return `${years} ${years === 1 ? 'year' : 'years'} ago`;
}

export interface FlagInfo {
  label: string;
  tone: Tone;
}

const FLAG_INFO: Record<string, FlagInfo> = {
  unverified: { label: 'Unverified spec', tone: 'warn' },
  no_thrust_data: { label: 'No thrust data', tone: 'warn' },
  design_mismatch: { label: 'Differs from the design', tone: 'warn' },
  no_listing: { label: 'No Irish or UK listing', tone: 'warn' },
  no_price: { label: 'No price', tone: 'warn' },
  stale: { label: 'Listing stale', tone: 'warn' },
  links_broken: { label: 'Link not working', tone: 'error' },
  build: { label: 'Needs building', tone: 'info' },
  sold_in_pairs: { label: 'Sold in pairs', tone: 'info' },
  rotation: { label: 'CW and CCW', tone: 'info' },
  fit: { label: 'Fit', tone: 'warn' },
  power: { label: 'Power supply', tone: 'info' },
  radio: { label: 'Radio rules', tone: 'info' },
  joint: { label: 'Joint needed', tone: 'info' },
  endurance: { label: 'Endurance', tone: 'warn' },
  constraint: { label: 'Constraint not met', tone: 'error' },
  final_scale: { label: 'Final scale', tone: 'info' },
};

export function flagInfo(code: string): FlagInfo {
  return FLAG_INFO[code] ?? { label: code.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()), tone: 'info' };
}

/** Refresh states that are still in progress (keep polling). */
export function refreshActive(status: ListingsRefreshStatus | null | undefined): boolean {
  return status === 'queued' || status === 'running';
}

export function refreshLabel(status: ListingsRefreshStatus | null | undefined): { label: string; tone: Tone } | null {
  switch (status) {
    case 'queued':
      return { label: 'Price check queued', tone: 'info' };
    case 'running':
      return { label: 'Checking prices…', tone: 'info' };
    case 'done':
      return { label: 'Prices checked', tone: 'ok' };
    case 'refused':
      return { label: 'Check declined', tone: 'warn' };
    case 'error':
      return { label: 'Price check failed', tone: 'error' };
    default:
      return null;
  }
}

/** Plain message for a failed "Check prices now": 429 says when to try again, 503 passes the server's words on. */
export function refreshErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 429) {
    const body = error.body as { retry_after_s?: unknown } | null;
    const seconds = typeof body?.retry_after_s === 'number' ? body.retry_after_s : null;
    if (seconds !== null) {
      const minutes = Math.max(1, Math.ceil(seconds / 60));
      return `Checked within the last hour; try again in ${minutes} ${minutes === 1 ? 'minute' : 'minutes'}.`;
    }
    return 'Checked within the last hour; try again later.';
  }
  return errorMessage(error);
}

export interface BudgetGeometry {
  /** Fill width as % of the track. */
  fillPct: number;
  /** Budget line position as % of the track (100 unless over budget). */
  budgetPct: number;
  /** Track maximum in euro. */
  scaleMax: number;
}

/** Track geometry: the budget is the full track until the cost goes over it; then the cost is. */
export function budgetGeometry(totals: Pick<PartsListTotals, 'cost_eur' | 'budget_eur'>): BudgetGeometry {
  const cost = Math.max(0, totals.cost_eur || 0);
  const budget = Math.max(0, totals.budget_eur || 0);
  const scaleMax = Math.max(cost, budget, 1);
  return {
    fillPct: (cost / scaleMax) * 100,
    budgetPct: (budget / scaleMax) * 100,
    scaleMax,
  };
}

export const BUDGET_STATUS: Record<'under' | 'near' | 'over', { label: string; tone: Tone; icon: string }> = {
  under: { label: 'Under budget', tone: 'ok', icon: '✓' },
  near: { label: 'Near the budget', tone: 'warn', icon: '!' },
  over: { label: 'Over budget', tone: 'error', icon: '✕' },
};

function signed(value: number, text: string): string {
  if (value > 0) return `+${text}`;
  if (value < 0) return `−${text}`;
  return text;
}

/** "+€41.76", "−€241.16", "€0.00". */
export function formatSignedEur(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return signed(value, formatEur(Math.abs(value)));
}

/** "+492 g", "−30 g". */
export function formatSignedG(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return signed(value, formatWithUnit(Math.abs(value), 'g', { maxFractionDigits: 0 }));
}

/** "+30.5 min". */
export function formatSignedMin(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return signed(value, formatWithUnit(Math.abs(value), 'min', { maxFractionDigits: 1 }));
}

/** Short plain texts for an alternative's numbers, in a stable order. */
export function deltaTexts(d: AlternativeDeltas): string[] {
  const out: string[] = [];
  if (d.mass_g !== undefined && d.mass_g !== null) out.push(`mass ${formatSignedG(d.mass_g)}`);
  if (d.price_eur !== undefined && d.price_eur !== null) out.push(`price ${formatSignedEur(d.price_eur)}`);
  if (d.endurance_min !== undefined && d.endurance_min !== null) out.push(`endurance ${formatSignedMin(d.endurance_min)}`);
  if (d.hover_g_per_w !== undefined && d.hover_g_per_w !== null) out.push(`hover ${formatNumber(d.hover_g_per_w, { maxFractionDigits: 1 })} g/W`);
  if (d.max_thrust_g !== undefined && d.max_thrust_g !== null) out.push(`full thrust ${formatWithUnit(d.max_thrust_g, 'g', { maxFractionDigits: 0 })}`);
  if (d.current_margin !== undefined && d.current_margin !== null) out.push(`current ${formatNumber(d.current_margin, { maxFractionDigits: 1 })} x the peak`);
  if (d.torque_margin !== undefined && d.torque_margin !== null) out.push(`torque ${formatNumber(d.torque_margin, { maxFractionDigits: 1 })} x the hinge moment`);
  if (d.margin !== undefined && d.margin !== null) {
    out.push(`strength margin ${d.margin >= 0 ? '+' : '−'}${formatNumber(Math.abs(d.margin), { maxFractionDigits: 2 })}`);
  }
  return out;
}

/** Plain names of the masses that come from selected parts, in a stable order. */
export function partsMassNames(masses: PartsMasses | null | undefined): string[] {
  if (!masses) return [];
  return (Object.keys(PARTS_MASS_LABELS) as (keyof PartsMasses)[])
    .filter((key) => {
      const v = masses[key];
      return typeof v === 'number' && Number.isFinite(v) && v > 0;
    })
    .map((key) => PARTS_MASS_LABELS[key]);
}

/** "Lift motors: 4 x" style quantity text with the unit the role buys in. */
export function quantityText(role: Pick<PartsListRole, 'quantity' | 'role' | 'custom_pack'>): string {
  if (role.role === 'battery' && role.custom_pack) {
    return `${role.quantity} cells (${role.custom_pack.cells_series}S${role.custom_pack.cells_parallel}P)`;
  }
  if (role.role === 'spar_tube' || role.role === 'boom_tube') return `${role.quantity} x 1 m`;
  return `${role.quantity}`;
}
