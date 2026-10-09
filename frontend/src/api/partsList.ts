/**
 * Phase 4 parts list and supplier lookup (docs/phases/PHASE4.md section 6, "Endpoints" and
 * "Parts-list response"). Every parts-list route takes `?source=draft` or `?source=<version id>`.
 */
import { api } from './client';
import type { Part } from './types';

export type PartsSystem = 'propulsion' | 'energy' | 'flight_control' | 'structure';

/** A supplier listing as the parts list carries it. */
export interface PartsListListing {
  id: number;
  supplier_name: string;
  country: 'IE' | 'UK';
  url: string;
  price_eur: number | null;
  in_stock: boolean | null;
  last_checked_at: string | null;
  /** Link answered 200-399 when checked; null = never checked. */
  url_ok: boolean | null;
  url_status: number | null;
  /** Not checked in 30 days. */
  stale: boolean;
}

export interface PartsListPart {
  id: number;
  category: string;
  manufacturer: string;
  model: string;
  name: string;
  mass_g: number;
  verified: boolean;
  /** Manufacturer page the specification came from. */
  source: string;
  price_eur: number | null;
  price_source: 'listing' | 'estimate' | null;
  spec: Record<string, unknown>;
}

export interface PartsListFlag {
  code: string;
  message: string;
}

/** Alternative values: mass_g, price_eur and endurance_min are changes against the chosen part; the rest are the alternative's own values. */
export interface AlternativeDeltas {
  mass_g?: number;
  price_eur?: number;
  hover_g_per_w?: number;
  max_thrust_g?: number;
  endurance_min?: number;
  margin?: number;
  current_margin?: number;
  torque_margin?: number;
}

export interface PartsListAlternative {
  part: PartsListPart;
  feasible: boolean;
  reason_lost: string;
  deltas: AlternativeDeltas;
  tier?: 'cheaper' | 'premium' | string;
  /** Number of cells for a custom battery pack. */
  quantity?: number;
  /** For example "custom 6S3P pack of Molicel INR-21700-P45B". */
  label?: string;
}

export interface PartsListRole {
  role: string;
  label: string;
  system: PartsSystem;
  filled: boolean;
  part: PartsListPart | null;
  /** Units to buy (pairs, cells, 1 m tubes). */
  quantity: number;
  unit_mass_g: number | null;
  /** Installed mass (tubes: the cut length). */
  line_mass_g: number | null;
  unit_price_eur: number | null;
  price_source: 'listing' | 'estimate' | null;
  line_price_eur: number | null;
  best_listing: PartsListListing | null;
  listings: PartsListListing[];
  /** Plain sentences quoting the numbers; the unfilled reason when filled is false. */
  reasoning: string[];
  alternatives: PartsListAlternative[];
  flags: PartsListFlag[];
  locked: boolean;
  unfilled_reason: string | null;
  metrics: Record<string, unknown>;
  custom_pack: { cells_series: number; cells_parallel: number; cell: string; overhead_fraction: number } | null;
  tier1_mass_g: number | null;
}

export interface PartsListConsumables {
  label: string;
  system: 'consumables';
  mass_g: number;
  mass_source: string;
  cost_eur: number;
  cost_source: string;
  items: { label: string; cost_eur: number }[];
}

export type BudgetStatus = 'under' | 'near' | 'over';

export interface PartsListTotals {
  mass_g: number;
  tier1_mass_g: number;
  mass_vs_tier1_g: number;
  avionics_parts_g: number;
  avionics_allowance_g: number;
  takeoff_mass_kg: number;
  takeoff_mass_generic_kg: number;
  estimated_endurance_min: number | null;
  generic_endurance_min: number | null;
  endurance_note: string;
  cost_eur: number;
  budget_eur: number;
  budget_status: BudgetStatus;
  budget_message: string;
  budget_fraction: number;
  unpriced_roles: string[];
}

export interface PartsListUpgrade {
  role: string;
  role_label: string;
  part: PartsListPart;
  label: string;
  extra_cost_eur: number;
  mass_change_g: number;
  endurance_gain_min: number;
  eur_per_min: number | null;
  trade_off: string;
}

export interface PartsList {
  selection_version: string;
  layout: string;
  generated_at: string;
  source: { kind: 'draft' | 'version'; version_id: number | null; version_number: number | null };
  stored: { in_sync: boolean; stored_at: string | null };
  roles: PartsListRole[];
  consumables: PartsListConsumables;
  totals: PartsListTotals;
  upgrades: PartsListUpgrade[];
  unfilled: { role: string; label: string; reason: string }[];
  uk_import_note: string;
  system_labels: Record<string, string>;
  catalogue_size: number;
}

/** Body of 202 POST /api/projects/{id}/parts-list/refresh-all. */
export interface RefreshAllResult {
  queued: { part_id: number; name: string }[];
  skipped: { part_id: number; name: string; reason: string }[];
  message: string;
}

/** 'draft' or a version id. */
export type PartsSource = 'draft' | number;

const base = (projectId: number) => `/api/projects/${projectId}/parts-list`;
const q = (source: PartsSource) => `?source=${encodeURIComponent(String(source))}`;

export function getPartsList(projectId: number, source: PartsSource, signal?: AbortSignal): Promise<PartsList> {
  return api<PartsList>(`${base(projectId)}${q(source)}`, { signal });
}

export function recomputePartsList(projectId: number, source: PartsSource, signal?: AbortSignal): Promise<PartsList> {
  return api<PartsList>(`${base(projectId)}/recompute${q(source)}`, { method: 'POST', signal });
}

export function setRolePart(
  projectId: number,
  source: PartsSource,
  role: string,
  body: { part_id: number; quantity?: number; locked?: boolean },
): Promise<PartsList> {
  return api<PartsList>(`${base(projectId)}/roles/${encodeURIComponent(role)}${q(source)}`, { method: 'PUT', body });
}

export function unlockRole(projectId: number, source: PartsSource, role: string): Promise<PartsList> {
  return api<PartsList>(`${base(projectId)}/roles/${encodeURIComponent(role)}/lock${q(source)}`, { method: 'DELETE' });
}

export function refreshAllListings(projectId: number, source: PartsSource): Promise<RefreshAllResult> {
  return api<RefreshAllResult>(`${base(projectId)}/refresh-all${q(source)}`, { method: 'POST' });
}

/** Queue one part's supplier lookup on the worker (202); poll getPart until it is done. */
export function refreshPartListings(partId: number): Promise<{ part: Part }> {
  return api<{ part: Part }>(`/api/parts/${partId}/refresh-listings?wait=false`, { method: 'POST' });
}

export function getPart(partId: number, signal?: AbortSignal): Promise<Part> {
  return api<Part>(`/api/parts/${partId}`, { signal });
}

export function listParts(signal?: AbortSignal): Promise<Part[]> {
  return api<Part[]>('/api/parts', { signal });
}
