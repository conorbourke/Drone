/**
 * Phase 7 full-scale checks (docs/phases/PHASE7.md section 2, backend/app/routers/fullscale.py):
 * the Phase 3 checks plus the 24 kg checks of the draft or a version, answered at once (about a
 * second). Only the parts the tab reads are typed.
 */
import { api } from './client';
import type { AnalysisCheck, ServerQuantity } from './analysis';
import type { ExportSource } from './exports';

export interface MtowThreshold {
  key: string;
  label: string;
  kg: number;
  exceeded: boolean;
  margin_kg: number;
  setting: string;
}

export interface MtowResult {
  mass_kg: number;
  label: string;
  thresholds: MtowThreshold[];
  level: AnalysisCheck['level'];
  banner: string;
}

export interface MotorOutCase {
  failed_motor: string;
  payload_case: string;
  cg_x_mm: number;
  mass_kg: number;
  holds_attitude_and_heading: boolean;
  holds_attitude_spinning: boolean;
  max_utilisation: number | null;
  thrusts_n: Record<string, number> | null;
  roll_authority_fraction: number | null;
  yaw_authority_fraction: number | null;
  residual_yaw_nm: number | null;
  battery_current_a: number | null;
  level: AnalysisCheck['level'];
  message: string;
}

export interface MotorOutResult {
  layout: string;
  model: string[];
  max_static_thrust_per_motor_n: number;
  tilt_yaw_angle_deg?: number;
  cases: MotorOutCase[];
  worst_case: MotorOutCase;
  level: AnalysisCheck['level'];
  message: string;
  recommendation: {
    needed: boolean;
    layout?: string;
    message?: string;
    x8?: {
      required_static_thrust_per_motor_n: number;
      current_motor_static_thrust_n: number;
      same_motors_enough: boolean;
      installed_thrust_to_weight?: number;
      coax_lower_rotor_efficiency?: number;
    };
  };
  source: string;
}

export interface TubeRow {
  part: string;
  outer_mm: number;
  inner_mm: number;
  mass_per_m_g: number | null;
  stress_mpa: number | null;
  allowable_mpa: number | null;
  margin: number | null;
  deflection_mm: number | null;
  ok: boolean;
  problems: string[];
}

export interface StructureResult {
  spar: {
    root_moment_ultimate_nm: number;
    catalogue: TubeRow[];
    needed_standard_tube?: { outer_mm: number; wall_mm: number; margin: number; mass_g_per_m?: number } | null;
    composite_caps?: { spar: string; margin: number; level: string } | null;
    level: AnalysisCheck['level'];
    message: string;
  };
  booms: {
    moment_ultimate_nm: number;
    critical_case?: string;
    arm_mm?: number;
    catalogue: TubeRow[];
    needed_outer_mm?: { outer_mm: number; wall_mm: number; margin: number; deflection_mm?: number } | null;
    level: AnalysisCheck['level'];
    message: string;
  };
  source?: string;
}

export interface LandingGearResult {
  sink_rate_mps: number;
  stroke_mm: number;
  efficiency: number;
  load_factor: number;
  total_load_n: number;
  attachments: number;
  load_per_attachment_n: number;
  boom_margin?: number | null;
  level: AnalysisCheck['level'];
  message: string;
  source: string;
}

export interface LiIonAlternative {
  cell: string;
  config: string;
  energy_wh: number;
  mass_kg: number;
  continuous_rating_a: number;
  peak_fraction_of_rating: number;
  ok: boolean;
}

export interface BatteryResult {
  hover_current_a: number;
  peak_current_a: number;
  motor_out_current_a: number | null;
  pack: string;
  continuous_rating_a: number;
  limit_a: number;
  max_fraction_of_rating: number;
  level: AnalysisCheck['level'];
  message: string;
  li_ion_alternatives: LiIonAlternative[];
  li_ion_note?: string;
}

export interface LayupPly {
  fabric: string;
  fabric_g_m2: number;
  orientation: string;
  count: number;
  position: string;
  cured_g_m2: number;
  thickness_mm: number;
}

export interface LayupPart {
  key: string;
  label: string;
  plies: LayupPly[];
  core?: { material: string; thickness_mm: number; coverage_fraction?: number } | null;
  areal_density_g_m2?: number;
  area_m2?: number;
  local_reinforcement?: string;
  mass_g: number;
}

export interface LayupResult {
  parts: LayupPart[];
  structural_mass: {
    layup_total_g: number;
    mass_model_g: number;
    difference_g: number;
    takeoff_mass_with_layup_kg: number;
    note?: string;
  };
  sources: string[];
}

export interface FullscaleResult {
  schema: string;
  valid: boolean;
  message?: string;
  source: 'draft' | 'version';
  version_id: number | null;
  version_number: number | null;
  analysis_id: number | null;
  analysis_source: 'reused' | 'quick';
  parts: 'selected' | 'generic';
  catalogue_size: number;
  computed_at: string;
  checks: AnalysisCheck[];
  counts?: { fail: number; warn: number };
  mtow?: MtowResult;
  mtow_layup?: MtowResult;
  motor_out?: MotorOutResult;
  structure?: StructureResult;
  landing_gear?: LandingGearResult;
  battery?: BatteryResult;
  layup?: LayupResult;
  range_endurance?: {
    endurance_cruise: ServerQuantity;
    endurance_total: ServerQuantity;
    range: ServerQuantity;
    a3_note: string;
  };
  a3_note?: string;
  notes?: string[];
  summary?: Record<string, ServerQuantity>;
  duration_s?: number;
}

export function runFullscale(projectId: number, source: ExportSource): Promise<FullscaleResult> {
  return api<FullscaleResult>(`/api/projects/${projectId}/fullscale`, { method: 'POST', body: { source } });
}
