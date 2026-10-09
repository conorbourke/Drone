/**
 * Types for the Phase 3 server analysis, scale-to-weight, validation and assistant APIs
 * (docs/phases/PHASE3.md section 8). The result documents are large; only the parts the UI
 * reads are typed, everything else stays `unknown`.
 */

/** A number with its plausible range and explanation, as every engine output. */
export interface ServerQuantity {
  value: number | null;
  low: number | null;
  high: number | null;
  unit: string;
  label: string;
  explain: string;
  source: string;
}

export type CheckLevel = 'ok' | 'warn' | 'fail' | 'info';

export interface AnalysisCheck {
  key: string;
  label: string;
  level: CheckLevel;
  message: string;
  value?: unknown;
  threshold?: unknown;
  unit?: string;
  threshold_source?: string;
}

export type AnalysisStatus = 'queued' | 'running' | 'done' | 'error';

/** List item (POST answers, GET list). */
export interface AnalysisListItem {
  id: number;
  project_id: number;
  version_id: number | null;
  version_number: number | null;
  source: 'draft' | 'version';
  kind: 'full' | 'scale';
  status: AnalysisStatus;
  progress: number;
  stage: string;
  error: string | null;
  duration_s: number | null;
  inputs_hash: string;
  reused_from_id: number | null;
  queue_position: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  headline: Record<string, unknown> | null;
}

export interface MassComponentRow {
  key: string;
  label: string;
  group: string;
  mass_g: number;
  x_mm: number;
  uncertainty: number;
  explain: string;
  source: string;
}

export interface DragItem {
  key: string;
  label: string;
  cd: number;
  drag_area_m2: number;
  share: number;
  explain: string;
  source: string;
}

export interface MissionSegment {
  key: string;
  label: string;
  duration_s: number;
  power_w: number;
  energy_wh: number;
}

export interface TransitionPoint {
  speed_mps: number;
  tilt_deg?: number;
  wing_lift_fraction: number;
  thrust_margin: number;
  power_w: number;
  battery_current_a: number;
}

export interface Tier1Row {
  key: string;
  label: string;
  tier1: number | null;
  tier2: number | null;
  unit: string;
  difference_pct: number | null;
  why: string;
}

export interface ResultNote {
  key: string;
  label: string;
  level: CheckLevel;
  message: string;
}

export interface KeyNumbers {
  [key: string]: ServerQuantity;
}

export interface Recommendation {
  key: string;
  label?: string;
  sentence: string;
  rank?: number;
  endurance_gain_min?: number;
  change?: Record<string, { from: unknown; to: unknown }>;
  patch: Record<string, unknown>;
  before: KeyNumbers;
  after: KeyNumbers;
  checks_after?: Record<string, CheckLevel>;
}

export interface RecommendationsBlock {
  valid: boolean;
  message?: string;
  recommendations: Recommendation[];
  fixes: Recommendation[];
  baseline?: KeyNumbers;
  variants_tried?: number;
  all_accepted?: number;
  method?: string;
  duration_s?: number;
}

export interface SpanLoadingPoint {
  y_mm: number;
  lift_n_per_m: number;
}

export interface SparCheck {
  level: CheckLevel;
  margin: number;
  stress_mpa: number;
  allowable_mpa: number;
  spar?: string;
  tube?: string;
  load_factor?: number;
  safety_factor?: number;
  tip_deflection_1g_mm?: number;
  tip_deflection_limit_mm?: number;
  tip_deflection_full_thrust_mm?: number;
  root_moment_ultimate_nm?: number;
  critical_case?: string;
  source?: string;
  span_loading?: SpanLoadingPoint[];
}

/** The full analysis result (AnalysisResult plus `recommendations`). */
export interface AnalysisResult {
  valid: boolean;
  mode?: string;
  layout?: string;
  summary: Record<string, ServerQuantity>;
  mass?: {
    components: MassComponentRow[];
    takeoff_max_payload?: ServerQuantity;
    [key: string]: unknown;
  };
  aero?: {
    stability_derivatives?: Record<string, number | string>;
    [key: string]: unknown;
  };
  drag?: { items: DragItem[]; cd_total_cruise?: number; cd_parasite?: number; cd_profile?: number; cd_induced?: number };
  transition?: {
    points: TransitionPoint[];
    margin_min_setting?: number;
    min_thrust_margin?: number;
    min_margin_speed_mps?: number;
    peak_power_w?: number;
    peak_current_a?: number;
    speed_wing_80pct_mps?: number | null;
    level?: CheckLevel;
  };
  structure?: { wing_spar?: SparCheck; boom?: SparCheck };
  mission?: { payload_max?: { segments: MissionSegment[]; usable_wh: number; vtol_wh: number } };
  checks: AnalysisCheck[];
  notes?: ResultNote[];
  tier1_comparison?: Tier1Row[];
  assumptions?: string[];
  a3_note?: string;
  inputs?: { parameters: unknown; mission: unknown };
  recommendations?: RecommendationsBlock | null;
}

export interface ScaleRow {
  key: string;
  label: string;
  before: number | string | null;
  after: number | string | null;
  unit: string;
  why: string;
}

export interface ScaleResult {
  valid: boolean;
  target_takeoff_mass_kg: number;
  parameters?: Record<string, unknown>;
  mission?: Record<string, unknown>;
  table?: ScaleRow[];
  checks?: AnalysisCheck[];
  notes?: string[];
  message?: string;
  analysis?: AnalysisResult | null;
}

export interface AnalysisDetail<R = AnalysisResult> extends AnalysisListItem {
  target_takeoff_mass_kg: number | null;
  result: R | null;
}

// ---------- Validation ----------

export type ValidationStatus = 'pass' | 'fail' | 'skipped' | 'info';

export interface ValidationCase {
  id: string;
  group: string;
  name: string;
  compared: string;
  reference: { value: number | null; unit: string; source: string };
  engine: { value: number | null; unit: string };
  error_pct: number | null;
  tolerance_pct: number | null;
  status: ValidationStatus;
  note: string;
}

export interface ValidationReport {
  generated_at: string;
  engine_version: string;
  summary: { pass: number; fail: number; skipped: number; info: number; cases: number; all_passed: boolean };
  tolerance_note: string;
  groups: { key: string; label: string }[];
  cases: ValidationCase[];
}

export interface ValidationJob {
  status: 'idle' | 'queued' | 'running' | 'done' | 'error';
  progress: number;
  stage: string;
  queued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  trigger: 'startup' | 'owner' | null;
}

export interface ValidationResponse {
  report: ValidationReport | null;
  source: 'app' | 'snapshot' | null;
  job: ValidationJob;
}

// ---------- Assistant ----------

export interface AssistantStatus {
  available: boolean;
  model: string;
  message: string | null;
  max_tool_calls: number;
}

export interface ToolCallInfo {
  id: string;
  name: string;
  label: string;
  is_error?: boolean;
}

export interface Proposal {
  patch: Record<string, unknown>;
  summary: string;
  base: 'draft';
}

export type ThreadMessage =
  | { id: number; role: 'user'; text: string; created_at: string }
  | {
      id: number;
      role: 'assistant';
      text: string;
      tool_calls: ToolCallInfo[];
      proposals: Proposal[];
      created_at: string;
    }
  | { id: number; role: 'notice'; text: string; code: string; created_at: string };

export interface ThreadResponse {
  thread_id: number | null;
  available: boolean;
  messages: ThreadMessage[];
}
