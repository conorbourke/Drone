/**
 * Phase 7 mould sets (docs/phases/PHASE7.md section 1, backend/app/routers/moulds.py): generate
 * the two-part, tiled female moulds of the nose bay shell, the fuselage shell and the wing-root
 * fairing on the worker, poll the job, list earlier sets, download files one by one or as a ZIP,
 * fetch a tile's preview mesh and delete a set. The manifest ("vtol-moulds/1") is large; only
 * the parts the tab reads are typed.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, api, errorMessage, isAbortError, isAuthError } from './client';
import type { ExportItem, ExportSource, PieceMesh } from './exports';

export type MouldPartKey = 'nose' | 'fuselage' | 'wing_root_fairing';

export const MOULD_PARTS: ReadonlyArray<{ key: MouldPartKey; label: string; description: string }> = [
  {
    key: 'nose',
    label: 'Nose bay shell',
    description: 'The swappable nose with the camera bay: a closed rounded nose and the constant section behind it.',
  },
  {
    key: 'fuselage',
    label: 'Fuselage shell',
    description: 'The main fuselage skin from the nose bay to the tail, made as two halves.',
  },
  {
    key: 'wing_root_fairing',
    label: 'Wing-root fairings',
    description: 'The rounded fillet between the wing root and the fuselage side. One mould makes the right fairing; the left one is its mirror image.',
  },
];

export interface MouldFile {
  path: string;
  /** stl | 3mf | step | pdf */
  kind: string;
  size_bytes: number;
  part: string;
  tile?: string;
  half?: string;
}

export interface MouldTileOrientation {
  name?: string;
  reason?: string;
  supports?: string;
}

export interface MouldTile {
  label: string;
  index: number;
  count: number;
  size_mm: number[];
  fits: boolean;
  watertight?: boolean;
  envelope_mm?: number[];
  estimated_mass_g?: number;
  estimated_print_time?: string;
  filament?: string;
  print_orientation?: MouldTileOrientation;
  notes?: string[];
  files: { stl?: string; '3mf'?: string };
  half?: string;
}

export interface MouldHalf {
  key: string;
  label: string;
  letter?: string;
  pull_note?: string;
  tiles: MouldTile[];
  step_file?: string;
  demould?: { undercut_fraction?: number; demouldable?: boolean };
  draft_min_outside_band_deg?: number;
}

export interface DraftFlagged {
  face: number;
  where: string;
  min_draft_deg: number;
  area_below_min_mm2: number;
}

export interface DraftMap {
  points_uv_mm: number[][];
  h_mm?: number[];
  draft_deg: number[];
  half: string[];
  in_band: boolean[];
}

export interface MouldDraft {
  min_draft_deg: number;
  parting_band_mm?: number;
  summary: {
    faces: number;
    flagged: number;
    area_mm2?: number;
    fraction_below_min?: number;
    fraction_below_min_outside_band?: number;
    undercut_fraction?: number;
    min_draft_outside_band_deg?: Record<string, number>;
  };
  flagged_faces: number[];
  flagged_detail?: DraftFlagged[];
  note?: string;
  map?: DraftMap;
}

export interface MouldPart {
  key: string;
  label: string;
  description?: string;
  net_part?: string;
  open_ends?: string[];
  notes?: string[];
  halves: MouldHalf[];
  parting_line?: { description?: string; silhouette_deviation_mm?: number; length_mm?: number };
  draft: MouldDraft;
  demould?: { method?: string };
  mould?: {
    wall_mm?: number;
    flange_width_mm?: number;
    bolt?: string;
    bolt_pitch_mm?: number;
    registration_keys?: { type?: string };
    trim_line?: { offset_mm?: number };
    vent_channels?: boolean;
    laminate_allowance?: { direction?: string; laminate_mm?: number; layup?: string };
  };
  tiling?: { tiles_per_half?: number; rule?: string };
  print_notes?: { filament?: string; settings?: Record<string, unknown>; finishing?: string[] };
  assembly_order?: string[];
  files?: { pdf?: string };
}

export interface MouldManifest {
  schema: string;
  parts: MouldPart[];
  envelope_mm?: number[];
  printer?: string;
  files: MouldFile[];
  notes?: string[];
  options?: Record<string, unknown>;
}

export interface MouldPartSummary {
  part: string;
  label: string;
  halves: string[];
  tiles_per_half: number;
  tiles: number;
  all_tiles_fit: boolean;
  demouldable: boolean;
  flagged_faces: number;
}

export interface MouldSummary {
  parts: MouldPartSummary[];
  tiles: number;
  all_tiles_fit: boolean | null;
  demouldable: boolean | null;
  flagged_faces: number;
  estimated_mass_g: number;
  files: number;
  kinds: Record<string, number>;
  envelope_mm: number[] | null;
  printer: string | null;
}

export interface MouldItem extends Omit<ExportItem, 'summary'> {
  summary: MouldSummary | null;
  mould_parts: MouldPartKey[];
  options: { min_draft_deg?: number; vent_channels?: boolean };
}

export interface MouldDetail extends MouldItem {
  manifest: MouldManifest | null;
}

/** Tile preview mesh: the piece mesh shape plus the half and the estimates. */
export interface TileMesh extends PieceMesh {
  half: string | null;
  half_label: string | null;
  estimated_mass_g: number | null;
  estimated_print_time: string | null;
}

export interface MouldRequest {
  source: ExportSource;
  parts: MouldPartKey[];
  min_draft_deg?: number;
  vent_channels?: boolean;
}

export function startMoulds(projectId: number, body: MouldRequest): Promise<MouldItem> {
  return api<MouldItem>(`/api/projects/${projectId}/moulds`, { method: 'POST', body });
}

export function listMoulds(projectId: number, signal?: AbortSignal): Promise<MouldItem[]> {
  return api<MouldItem[]>(`/api/projects/${projectId}/moulds?limit=50`, { signal });
}

export function getMoulds(id: number, signal?: AbortSignal): Promise<MouldDetail> {
  return api<MouldDetail>(`/api/moulds/${id}`, { signal });
}

export function deleteMoulds(id: number): Promise<void> {
  return api<void>(`/api/moulds/${id}`, { method: 'DELETE' });
}

export function getTileMesh(mouldId: number, tileId: string, signal?: AbortSignal): Promise<TileMesh> {
  return api<TileMesh>(`/api/moulds/${mouldId}/tiles/${encodeURIComponent(tileId)}/mesh`, { signal });
}

/** Download link of one file of a mould set (each path segment encoded). */
export function mouldFileUrl(mouldId: number, path: string): string {
  return `/api/moulds/${mouldId}/files/${path.split('/').map(encodeURIComponent).join('/')}`;
}

export function mouldZipUrl(mouldId: number): string {
  return `/api/moulds/${mouldId}/zip`;
}

export const MOULD_POLL_MS = 2000;

export function isMouldActive(status: string | undefined): boolean {
  return status === 'queued' || status === 'running';
}

/**
 * Polls one mould set every 2 s while it is queued or running; `onSettled` runs once when a
 * polled job leaves the active states.
 */
export function useMouldJob(id: number | null, onSettled?: (job: MouldDetail) => void) {
  const [job, setJob] = useState<MouldDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState<number | null>(null);
  const settled = useRef(onSettled);
  useEffect(() => {
    settled.current = onSettled;
  });

  useEffect(() => {
    if (id === null) return;
    let cancelled = false;
    let timer: number | null = null;
    let wasActive = false;
    const controller = new AbortController();
    const tick = async () => {
      try {
        const next = await getMoulds(id, controller.signal);
        if (cancelled) return;
        setJob(next);
        setError(null);
        if (isMouldActive(next.status)) {
          wasActive = true;
          timer = window.setTimeout(() => void tick(), MOULD_POLL_MS);
        } else if (wasActive) {
          settled.current?.(next);
        }
      } catch (caught) {
        if (cancelled || isAbortError(caught) || isAuthError(caught)) return;
        if (caught instanceof ApiError && caught.status === 404) {
          setMissing(id);
          return;
        }
        setError(errorMessage(caught));
        timer = window.setTimeout(() => void tick(), MOULD_POLL_MS * 2);
      }
    };
    void tick();
    return () => {
      cancelled = true;
      controller.abort();
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [id]);

  const reset = useCallback(() => {
    setJob(null);
    setError(null);
  }, []);

  const current = job && job.id === id ? job : null;
  return { job: current, error, missing: missing !== null && missing === id, reset };
}
