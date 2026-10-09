/**
 * Phase 6 flight data (docs/phases/PHASE6.md, backend/app/routers/flight_data.py): the logging
 * guide, flight-log upload (raw file body, streamed by the server, progress through XHR) and
 * the bundled sample, the worker's result (phases, statistics, comparison), chart series,
 * calibration (proposed, preview, apply, undo) and built weights.
 */
import { ApiError, api, notifyUnauthorized } from './client';

export type FlightLogStatus = 'queued' | 'running' | 'done' | 'error';

export interface GuideParameter {
  param: string;
  value: string | number;
  reason: string;
}

export interface LoggingGuide {
  parameters: GuideParameter[];
  log_bitmask: {
    value: number;
    computation: string;
    explain: string;
    bits: Array<{ bit: number; value: number; name: string; logs: string }>;
  };
  download: string[];
  always_logged: string[];
  source: string;
  max_upload_mb: number;
  accepted: string[];
  sample_available: boolean;
}

export interface PhaseSummary {
  key: string;
  label: string;
  flight: number | null;
  start_s: number;
  end_s: number;
  duration_s: number | null;
  energy_wh: number | null;
  power_mean_w: number | null;
  airspeed_median_mps: number | null;
  decided_by: string | null;
}

export interface FlightSummary {
  phases: PhaseSummary[];
  log_duration_s: number | null;
  flight_duration_s: number | null;
  energy_wh: number | null;
  method: string | null;
  vibration: string | null;
  missing: string[];
}

export interface FlightLogItem {
  id: number;
  project_id: number;
  version_id: number | null;
  version_number: number | null;
  source: 'draft' | 'version';
  filename: string;
  size_bytes: number;
  sample: boolean;
  takeoff_mass_kg: number | null;
  status: FlightLogStatus;
  progress: number;
  stage: string;
  error: string | null;
  queue_position: number | null;
  firmware: string | null;
  vehicle_type: string | null;
  log_start_at: string | null;
  flight_duration_s: number | null;
  summary: FlightSummary | null;
  counts: { inside: number; outside: number; unavailable: number } | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Stat {
  mean?: number | null;
  median?: number | null;
  min?: number | null;
  max?: number | null;
  p95?: number | null;
  steady_mean?: number | null;
  steady_median?: number | null;
  [key: string]: unknown;
}

export interface PhaseStats {
  key: string;
  label: string;
  start_s: number;
  end_s: number;
  duration_s: number;
  energy_wh?: number | null;
  power_w?: Stat;
  current_a?: Stat;
  voltage_v?: Stat & { sag_mean?: number | null };
  airspeed_mps?: Stat & { source?: string };
  groundspeed_mps?: Stat;
  altitude_m?: Stat;
  vibration?: { level?: string; [key: string]: unknown };
  decided_by?: string;
  [key: string]: unknown;
}

export interface ProcessedLog {
  firmware: string | null;
  vehicle: Record<string, unknown>;
  duration_s: number;
  missing: Array<{ type: string; effect: string }>;
  phases: PhaseStats[];
  flight: PhaseStats | null;
  phase_detection: { method?: string; [key: string]: unknown };
  energy_check?: Record<string, unknown> | null;
  vibration: { worst_level: string; thresholds_mps2: { warn: number; fail: number }; source: string };
  notes: string[];
  events: { modes: Array<{ t_s: number; mode: string }>; messages: Array<{ t_s: number; text: string }> };
  params: Record<string, number>;
}

export interface ComparisonRow {
  key: string;
  label: string;
  phase: string;
  unit: string;
  kind: 'value' | 'lower_bound';
  basis: string;
  predicted: { value: number; low: number; high: number } | null;
  measured: number | null;
  error_pct: number | null;
  inside: boolean | null;
  status: 'inside' | 'outside' | 'unavailable';
  explanation: string;
}

export interface Comparison {
  available: boolean;
  reason?: string;
  reference: { analysis_id: number | null; kind: 'stored' | 'quick'; label: string; source: string } | null;
  mass_kg?: number;
  mass_source?: string;
  comparisons?: ComparisonRow[];
  counts?: { inside: number; outside: number; unavailable: number };
  notes?: string[];
  conditions?: { cruise_airspeed_mps: number | null; airspeed_source: string | null };
}

export interface FlightLogDetail extends FlightLogItem {
  result: ProcessedLog | null;
  comparison: Comparison | null;
  sample_note: string | null;
}

export interface SeriesChannel {
  unit: string;
  label: string;
  values: Array<number | null>;
}

export interface FlightSeries {
  t_s: number[];
  interval_s: number;
  source_rate_hz: number;
  channels: Record<string, SeriesChannel>;
  phases: PhaseSummary[];
}

export interface CalibrationFactor {
  name: string;
  label: string;
  explain: string;
  valid: boolean;
  value?: number;
  uncertainty?: number;
  n_logs: number;
  source_logs: number[];
  reason?: string;
  effect?: string;
  plausible?: boolean | null;
  applicable?: boolean;
  why_not?: string;
}

export interface AppliedFactor {
  name: string;
  label: string;
  value: number;
  uncertainty: number;
  n_logs: number;
  source_logs: number[];
  effect: string;
}

export interface CalibrationState {
  proposed: {
    n_logs: number;
    factors: Record<string, CalibrationFactor>;
    notes: string[];
    log_ids: number[];
  };
  applied: {
    factors: Record<string, AppliedFactor>;
    n_logs: number;
    source_log_ids: number[];
    applied_at: string;
  } | null;
}

export interface PreviewRow {
  key: string;
  label: string;
  unit: string;
  explain: string;
  before: number;
  after: number;
  change_pct: number | null;
}

export interface CalibrationPreview {
  rows: PreviewRow[];
  reason: string | null;
  factors: string[];
}

export interface BuiltWeightItem {
  key: string;
  label: string;
  group: string;
  subgroup: string;
  predicted_g: number;
  explain: string;
  measured_g: number | null;
  note: string;
  predicted_at_entry_g: number | null;
}

export interface BuiltWeights {
  items: BuiltWeightItem[];
  totals: {
    predicted_g: number;
    weighed_items: number;
    items: number;
    measured_g: number;
    predicted_weighed_g: number;
    difference_pct: number | null;
  };
  structural: {
    valid: boolean;
    value?: number;
    uncertainty?: number;
    reason?: string;
    per_group?: Record<string, { predicted_g: number; measured_g: number; factor: number; items: number }>;
  };
  reason: string | null;
  source: string;
}

export const getGuide = () => api<LoggingGuide>('/api/flight-data/guide');
export const listFlightLogs = (projectId: number, signal?: AbortSignal) =>
  api<FlightLogItem[]>(`/api/projects/${projectId}/flight-logs`, { signal });
export const getFlightLog = (id: number, signal?: AbortSignal) => api<FlightLogDetail>(`/api/flight-logs/${id}`, { signal });
export const getFlightSeries = (id: number, points = 600, signal?: AbortSignal) =>
  api<FlightSeries>(`/api/flight-logs/${id}/series?points=${points}`, { signal });
export const loadSampleFlight = (projectId: number) =>
  api<FlightLogItem>(`/api/projects/${projectId}/flight-logs/sample`, { method: 'POST' });
export const updateFlightLog = (id: number, body: { takeoff_mass_kg?: number | null; version_id?: number | null }) =>
  api<FlightLogItem>(`/api/flight-logs/${id}`, { method: 'PATCH', body });
export const reprocessFlightLog = (id: number) => api<FlightLogItem>(`/api/flight-logs/${id}/reprocess`, { method: 'POST' });
export const deleteFlightLog = (id: number) => api<void>(`/api/flight-logs/${id}`, { method: 'DELETE' });
export const getCalibration = (projectId: number) => api<CalibrationState>(`/api/projects/${projectId}/calibration`);
export const previewCalibration = (projectId: number) =>
  api<CalibrationPreview>(`/api/projects/${projectId}/calibration/preview`);
export const applyCalibration = (projectId: number, factors?: string[]) =>
  api<CalibrationState>(`/api/projects/${projectId}/calibration`, { method: 'POST', body: factors ? { factors } : {} });
export const undoCalibration = (projectId: number) =>
  api<CalibrationState>(`/api/projects/${projectId}/calibration`, { method: 'DELETE' });
export const getBuiltWeights = (projectId: number) => api<BuiltWeights>(`/api/projects/${projectId}/built-weights`);
export const saveBuiltWeights = (
  projectId: number,
  items: Array<{ key: string; measured_g: number | null; note: string }>,
) => api<BuiltWeights>(`/api/projects/${projectId}/built-weights`, { method: 'PUT', body: { items } });

export interface UploadOptions {
  versionId: number | null;
  takeoffMassKg: number | null;
  onProgress: (fraction: number) => void;
  signal?: AbortSignal;
}

/**
 * Upload one log as the raw request body (the server streams it to disk). XMLHttpRequest
 * rather than fetch because only XHR reports upload progress.
 */
export function uploadFlightLog(projectId: number, file: File, options: UploadOptions): Promise<FlightLogItem> {
  const params = new URLSearchParams({ filename: file.name });
  if (options.versionId !== null) params.set('version_id', String(options.versionId));
  if (options.takeoffMassKg !== null) params.set('takeoff_mass_kg', String(options.takeoffMassKg));
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/projects/${projectId}/flight-logs?${params.toString()}`);
    xhr.withCredentials = true;
    xhr.setRequestHeader('X-Requested-With', 'fetch');
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) options.onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      let body: unknown;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        body = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as FlightLogItem);
        return;
      }
      const detail =
        body && typeof body === 'object' && 'detail' in body && typeof (body as { detail: unknown }).detail === 'string'
          ? (body as { detail: string }).detail
          : xhr.status === 413
            ? `The log is too large (at most 200 MB).`
            : `Upload failed (${xhr.status}).`;
      if (xhr.status === 401) notifyUnauthorized();
      reject(new ApiError(xhr.status, detail, [], true, body));
    };
    xhr.onerror = () => reject(new ApiError(0, 'Could not reach the server. Check your connection and try again.', [], false));
    xhr.onabort = () => reject(new DOMException('Aborted', 'AbortError'));
    options.signal?.addEventListener('abort', () => xhr.abort());
    xhr.send(file);
  });
}
