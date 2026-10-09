/**
 * Phase 5 file exports (docs/phases/PHASE5.md section 3, backend/app/routers/exports.py):
 * generate the print, CAD, drawing, BOM and notes files of the draft or a version on the
 * worker, poll the job, list past exports, download files one by one or as a ZIP, fetch a
 * piece's preview mesh and delete an export. The manifest ("vtol-files/1") is large; only the
 * parts the Files tab reads are typed.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, api, errorMessage, isAbortError, isAuthError } from './client';

export type ExportStatus = 'queued' | 'running' | 'done' | 'error';

/** One file of the export, as the manifest lists it. */
export interface ExportFile {
  path: string;
  /** "print" | "cad" | "drawings" | "bom" | "notes" (others are shown under "Other files"). */
  group: string;
  /** File extension without the dot: stl, 3mf, step, pdf, dxf, csv, md, json. */
  kind: string;
  size_bytes: number;
  sha256?: string;
  /** Plain explanation of the file type and which free program opens it (may be empty). */
  opens_with?: string;
}

export interface PieceOrientation {
  policy?: string | null;
  description?: string | null;
  reason?: string | null;
  tilt_deg?: number | null;
}

/** One printed piece of a part (a part larger than the printer is split into pieces). */
export interface ExportPiece {
  number: number;
  count: number;
  label: string;
  /** STL path inside the export; its file stem is the piece id of the mesh endpoint. */
  stl: string;
  /** Size in print orientation, mm [x, y, z]. */
  size_mm: number[];
  envelope_mm?: number[];
  fits: boolean;
  orientation?: PieceOrientation;
  filament?: string;
  mass_g?: number;
  print_time_h?: number;
  print_time_band?: string;
  watertight?: boolean;
  triangles?: number;
}

export interface ExportPart {
  key: string;
  label: string;
  description?: string;
  filament?: string;
  quantity: number;
  pieces: ExportPiece[];
  mass_g_each?: number;
  files?: { '3mf'?: string; step?: string; notes?: string };
}

export interface BomTotals {
  rows?: number;
  line_mass_g?: number;
  line_price_eur?: number;
  selection_price_eur?: number;
  printed_mass_g?: number;
  unpriced_rows?: number;
}

export interface ExportManifest {
  schema: string;
  printer?: { name?: string; usable_envelope_mm?: number[]; bed_mm?: number[] };
  parts?: ExportPart[];
  plates?: { count?: number; spacing_mm?: number };
  tubes?: Array<{ key: string; label: string; od_mm: number; wall_mm: number; cut_length_mm: number }>;
  bom?: { path?: string; totals?: BomTotals };
  files?: ExportFile[];
  checks?: { all_pieces_fit?: boolean; pieces?: number; watertight_all?: boolean };
  warnings?: unknown[];
}

export interface ExportSummary {
  parts: number;
  pieces: number | null;
  all_pieces_fit: boolean | null;
  watertight_all: boolean | null;
  plates: number | null;
  files: number;
  groups: Record<string, number>;
  envelope_mm: number[] | null;
  bed_mm: number[] | null;
  bom_totals: BomTotals | null;
  warnings: unknown[];
}

export interface ExportItem {
  id: number;
  project_id: number;
  version_id: number | null;
  version_number: number | null;
  source: 'draft' | 'version';
  status: ExportStatus;
  /** 0 to 1. */
  progress: number;
  stage: string;
  error: string | null;
  inputs_hash: string;
  reused_from_id: number | null;
  queue_position: number | null;
  /** Whether the Phase 4 parts list ('selected') or generic sizes were used. */
  parts: 'selected' | 'generic';
  analysis_id: number | null;
  duration_s: number | null;
  peak_rss_mb: number | null;
  total_size_bytes: number | null;
  file_count: number | null;
  summary: ExportSummary | null;
  zip_url: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface ExportDetail extends ExportItem {
  manifest: ExportManifest | null;
}

/** The preview mesh of one piece in its print orientation (mm). */
export interface PieceMesh {
  piece_id: string;
  part_key: string | null;
  part_label: string | null;
  label: string | null;
  number: number | null;
  count: number | null;
  fits: boolean | null;
  size_mm: number[] | null;
  filament: string | null;
  orientation: PieceOrientation;
  printer: string | null;
  /** Printer bed, mm [x, y]. */
  bed_mm: number[] | null;
  /** Usable print envelope, mm [x, y, z]. */
  envelope_mm: number[] | null;
  stl_path: string | null;
  triangles_original: number;
  triangles: number;
  vertices: number;
  decimated: boolean;
  bounds_mm: { min: number[]; max: number[] };
  /** base64 little-endian float32 [x, y, z, ...]. */
  positions: string;
  /** base64 little-endian uint32 triangle indices. */
  indices: string;
}

export type ExportSource = 'draft' | { version_id: number };

export function startExport(projectId: number, source: ExportSource): Promise<ExportItem> {
  return api<ExportItem>(`/api/projects/${projectId}/exports`, { method: 'POST', body: { source } });
}

export function listExports(projectId: number, signal?: AbortSignal): Promise<ExportItem[]> {
  return api<ExportItem[]>(`/api/projects/${projectId}/exports?limit=50`, { signal });
}

export function getExport(id: number, signal?: AbortSignal): Promise<ExportDetail> {
  return api<ExportDetail>(`/api/exports/${id}`, { signal });
}

export function deleteExport(id: number): Promise<void> {
  return api<void>(`/api/exports/${id}`, { method: 'DELETE' });
}

export function getPieceMesh(exportId: number, pieceId: string, signal?: AbortSignal): Promise<PieceMesh> {
  return api<PieceMesh>(`/api/exports/${exportId}/pieces/${encodeURIComponent(pieceId)}/mesh`, { signal });
}

/** Download link of one file of an export (each path segment encoded). */
export function fileUrl(exportId: number, path: string): string {
  return `/api/exports/${exportId}/files/${path.split('/').map(encodeURIComponent).join('/')}`;
}

/** Download link of the whole export as one ZIP. */
export function zipUrl(exportId: number): string {
  return `/api/exports/${exportId}/zip`;
}

export const EXPORT_POLL_MS = 1500;

export function isExportActive(status: ExportStatus | undefined): boolean {
  return status === 'queued' || status === 'running';
}

/**
 * Polls one export (`GET /api/exports/{id}`) every 1.5 s while it is queued or running and
 * stops once it is done or failed. `onSettled` runs once when a polled job leaves the active
 * states (so the caller can refresh the list of past exports).
 */
export function useExportJob(id: number | null, onSettled?: (job: ExportDetail) => void) {
  const [job, setJob] = useState<ExportDetail | null>(null);
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
        const next = await getExport(id, controller.signal);
        if (cancelled) return;
        setJob(next);
        setError(null);
        if (isExportActive(next.status)) {
          wasActive = true;
          timer = window.setTimeout(() => void tick(), EXPORT_POLL_MS);
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
        // A network blip should not end the polling; try again a little later.
        timer = window.setTimeout(() => void tick(), EXPORT_POLL_MS * 2);
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

  // A job from a previous id is never shown for a new one.
  const current = job && job.id === id ? job : null;
  return { job: current, error, missing: missing !== null && missing === id, reset };
}
