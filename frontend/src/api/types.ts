/**
 * TypeScript mirrors of the Phase 1 API shapes in docs/ARCHITECTURE.md.
 *
 * Units are carried as field-name suffixes (mm, g, kg, mps, w, eur ...), exactly as the
 * backend serialises them. Labels, units and plain-language explanations for mission and
 * design fields are NOT declared here: they are served by /api/schema/* (see FieldMeta).
 */

// ---------- Auth ----------

/** The signed-in owner as returned by /api/auth/me and /api/auth/login. */
export interface User {
  /** Integer primary key of the user row. */
  id: number;
  /** Display identity (APP_OWNER_EMAIL on the server). */
  email: string;
  /** Short name shown in the top bar. */
  display_name: string;
}

/** Response body of POST /api/auth/login. */
export interface LoginResponse {
  user: User;
}

// ---------- Design parameters and mission ----------

/** Which motors tilt (or none) when the aircraft transitions to wing flight. */
export type Layout = 'front_tilt' | 'rear_tilt' | 'quad_pusher';

/** Which build the mission describes: the 3D-printed prototype or the carbon final aircraft. */
export type Scale = 'prototype' | 'final';

/** Mission document (schema_version 1): what the aircraft must do. */
export interface Mission {
  /** Version of this document's shape; the server upgrades older documents on read. */
  schema_version: number;
  /** Prototype (2-3 kg, printed) or final (up to 24 kg, carbon). */
  scale: Scale;
  /** Target take-off mass including batteries and the heaviest payload, in kilograms. */
  target_takeoff_mass_kg: number;
  /** Target endurance in wing flight, in minutes. */
  target_endurance_min: number;
  /** Cruise speed in metres per second. */
  cruise_speed_mps: number;
  /** Lightest nose-bay payload, in grams. */
  payload_min_g: number;
  /** Heaviest nose-bay payload, in grams. */
  payload_max_g: number;
}

/** Wing planform and placement; lengths in mm, angles in degrees. */
export interface WingParameters {
  span_mm: number;
  root_chord_mm: number;
  tip_chord_mm: number;
  sweep_deg: number;
  dihedral_deg: number;
  incidence_deg: number;
  /** Airfoil identifier, for example "sd7037". */
  airfoil: string;
  /** Wing root leading edge measured from the fuselage nose, in mm. */
  x_le_mm: number;
  /** Vertical offset from the fuselage centreline, in mm (0 = mid-wing, positive = up). */
  z_mm: number;
  /**
   * Schema v2: tip incidence relative to the root, in degrees (negative = washout).
   * Optional in this type so schema v1 documents still type-check; default 0.
   */
  twist_deg?: number;
}

/** Fuselage outer dimensions, in mm. */
export interface FuselageParameters {
  length_mm: number;
  width_mm: number;
  height_mm: number;
  cross_section: 'ellipse' | 'rounded_rect';
}

/** Motor booms, in mm. */
export interface BoomParameters {
  count: number;
  /** Distance of each boom from the centreline, in mm. */
  lateral_offset_mm: number;
  length_mm: number;
  /** Boom front relative to the wing leading edge, in mm (negative = ahead). */
  x_offset_mm: number;
  /** Schema v2: outer diameter of the carbon boom tube, in mm (default 20). */
  diameter_mm?: number;
}

/** Motor positions along each boom, in mm. */
export interface MotorParameters {
  front_x_mm: number;
  rear_x_mm: number;
  /** Height above the boom centreline, in mm. */
  height_mm: number;
}

/** Tilt mechanism; ignored for the quad_pusher layout. */
export interface TiltParameters {
  /** Position of the tilt axis along the boom, in mm. */
  axis_x_mm: number;
  max_angle_deg: number;
}

/** Separate pusher motor; only used for the quad_pusher layout. */
export interface PusherParameters {
  prop_diameter_mm: number;
  x_mm: number;
}

/** Tail geometry, in mm. */
export interface TailParameters {
  type: 'conventional' | 'v_tail' | 'inverted_v' | 'twin_boom_h';
  span_mm: number;
  chord_mm: number;
  /** Wing quarter chord to tail quarter chord, in mm. */
  arm_mm: number;
  height_mm: number;
  /** Schema v2: for v_tail / inverted_v, angle of each panel above (or below) horizontal, degrees (default 40). */
  v_angle_deg?: number;
  /** Schema v2: tail airfoil identifier (default "naca0009"). */
  airfoil?: string;
}

/** Swappable nose bay envelope, in mm (geometry only; payload mass lives in Mission). */
export interface NoseBayParameters {
  length_mm: number;
  width_mm: number;
  height_mm: number;
}

/** Landing gear. */
export interface LandingGearParameters {
  type: 'skids' | 'legs' | 'none';
  height_mm: number;
}

/** Schema v2: the four lift (and, for tilt layouts, cruise) propellers. */
export interface PropulsionParameters {
  prop_diameter_mm: number;
  prop_pitch_mm: number;
  prop_blades: number;
}

/** Schema v2: battery chemistry. */
export type BatteryChemistry = 'lipo' | 'li-ion';

/** Schema v2: flight battery pack. */
export interface BatteryParameters {
  chemistry: BatteryChemistry;
  /** Cells in series (1-14). */
  cells_series: number;
  /** Cells in parallel (1-10). */
  cells_parallel: number;
  /** Capacity per parallel group member (one cell or string), in mAh; pack capacity = this x cells_parallel. */
  capacity_mah: number;
  /** Pack centre measured from the nose, in mm. */
  x_mm: number;
}

/** Schema v2: mass allowances for items not modelled individually until Phase 4. */
export interface AllowanceParameters {
  /** Autopilot, GPS, receiver, telemetry radio, power module, in grams. */
  avionics_g: number;
  /** Wiring, connectors and fasteners as a fraction of the empty mass. */
  wiring_fraction: number;
}

/**
 * Design parameters document (schema_version 1 or 2): the editable geometry of the aircraft.
 * The schema v2 blocks and fields (docs/phases/PHASE2.md section 2) are optional in this type
 * so schema v1 documents still type-check; the server always sends them for v2 and the
 * engine fills the contract defaults when they are missing.
 */
export interface DesignParameters {
  /** Version of this document's shape; the server upgrades older documents on read. */
  schema_version: number;
  layout: Layout;
  wing: WingParameters;
  fuselage: FuselageParameters;
  booms: BoomParameters;
  motors: MotorParameters;
  tilt: TiltParameters;
  pusher: PusherParameters;
  tail: TailParameters;
  nose_bay: NoseBayParameters;
  landing_gear: LandingGearParameters;
  /** Schema v2. */
  propulsion?: PropulsionParameters;
  /** Schema v2. */
  battery?: BatteryParameters;
  /** Schema v2. */
  allowances?: AllowanceParameters;
}

/** The editable working copy of a project: parameters plus mission. */
export interface DraftDocument {
  parameters: DesignParameters;
  mission: Mission;
}

/** GET/PUT /api/projects/{id}/draft. */
export interface Draft extends DraftDocument {
  /** Version the draft was last saved from or restored from; null when there is none. */
  based_on_version_id: number | null;
  /** ISO 8601 UTC timestamp of the last draft save. */
  updated_at: string;
}

// ---------- Airfoils (GET /api/airfoils, schema in docs/phases/PHASE2.md section 3) ----------

/** XFOIL polar summary of one airfoil at one Reynolds number. */
export interface AirfoilPolarSummary {
  re: number;
  cl_max: number;
  alpha_cl_max_deg: number;
  alpha_zero_lift_deg: number;
  /** Section lift-curve slope fitted between -2 and 6 degrees, per radian. */
  cl_alpha_per_rad: number;
  cd_min: number;
  cl_at_cd_min: number;
  cm0: number;
  /**
   * True when the XFOIL alpha sweep ended before the section stalled, so cl_max is a lower
   * bound (the real maximum may be higher).
   */
  cl_max_at_sweep_end?: boolean;
}

/** Row of GET /api/airfoils (what the Tier 1 engine needs). */
export interface AirfoilSummary {
  id: string;
  name: string;
  description: string;
  use: 'wing' | 'tail';
  /** Maximum thickness, % of chord. */
  thickness_pct: number;
  /** Chordwise position of maximum thickness, % of chord. */
  x_thickness_pct: number;
  /** Maximum camber, % of chord. */
  camber_pct: number;
  /** Chordwise position of maximum camber, % of chord. */
  x_camber_pct: number;
  source: string;
  /** One entry per Reynolds number, ascending. */
  polar_summary: AirfoilPolarSummary[];
}

/** GET /api/airfoils/{id}: the summary plus unit-chord coordinates in Selig order. */
export interface AirfoilDetail extends AirfoilSummary {
  coordinates: [number, number][];
}

// ---------- Projects ----------

/** Row of GET /api/projects. */
export interface ProjectSummary {
  id: number;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  /** Number of saved versions in the project. */
  version_count: number;
  /** Most recently created version, or null when none has been saved. */
  latest_version: { id: number; number: number; name: string } | null;
  /** Number the next saved version will get (monotonic, never reused). Absent from older servers. */
  next_version_number?: number;
}

/** GET /api/projects/{id}. */
export interface Project {
  id: number;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  draft: Draft;
  version_count: number;
  /** Number the next saved version will get (monotonic, never reused). Absent from older servers. */
  next_version_number?: number;
}

/** Body of POST /api/projects. */
export interface ProjectCreate {
  name: string;
  description?: string;
}

/** Body of PATCH /api/projects/{id}. */
export interface ProjectPatch {
  name?: string;
  description?: string;
}

// ---------- Versions ----------

/** Row of GET /api/projects/{id}/versions. */
export interface VersionSummary {
  id: number;
  /** Permanent per-project label (v1, v2 ...); never reused after a delete. */
  number: number;
  name: string;
  notes: string;
  /** Version this one was saved or duplicated from, or null. */
  parent_version_id: number | null;
  created_at: string;
}

/** GET /api/versions/{vid} and the 201 bodies of save/duplicate. */
export interface DesignVersion extends VersionSummary {
  project_id: number;
  parameters: DesignParameters;
  mission: Mission;
}

/** Body of POST /api/projects/{id}/versions. Omit parameters/mission to snapshot the draft. */
export interface VersionCreate {
  name: string;
  notes?: string;
  parameters?: DesignParameters;
  mission?: Mission;
}

/** Body of PATCH /api/versions/{vid}. */
export interface VersionPatch {
  name?: string;
  notes?: string;
}

// ---------- Parts ----------

/** One spec field of a parts category, generated from the backend's Pydantic models. */
export interface PartCategoryField {
  name: string;
  label: string;
  /** Unit symbol such as "mm" or "g"; null or empty when dimensionless. */
  unit: string | null;
  /** Value type: number, integer, string, boolean, list ... */
  type: string;
  required: boolean;
  description: string;
}

/** Row of GET /api/parts/categories. */
export interface PartCategory {
  key: string;
  label: string;
  description: string;
  fields: PartCategoryField[];
}

/** A supplier listing attached to a part. */
export interface PartListing {
  id: number;
  part_id: number;
  supplier_name: string;
  /** Supplier country: Ireland or United Kingdom. */
  country: 'IE' | 'UK';
  url: string;
  /** Listed price in euro, or null when unknown. */
  price_eur: number | null;
  /** Stock state when last checked, or null when unknown. */
  in_stock: boolean | null;
  last_checked_at: string | null;
}

/** Row of GET /api/parts. */
export interface Part {
  id: number;
  category: string;
  manufacturer: string;
  model: string;
  /** Mass of the part in grams. */
  mass_g: number;
  /** Rough price estimate in euro, or null when unknown. */
  price_eur_estimate: number | null;
  /** Category-specific specification, validated by the backend. */
  spec: Record<string, unknown>;
  /** Where the specification came from. */
  source: string;
  /** False for placeholders and unchecked entries. */
  verified: boolean;
  notes: string;
  listings: PartListing[];
  created_at?: string;
  updated_at?: string;
}

// ---------- Settings ----------

/** Printer build volume and the usable envelope, per axis in mm. */
export interface Envelope {
  x: number;
  y: number;
  z: number;
}

/** Full settings document (schema_version 1). */
export interface Settings {
  schema_version: number;
  printer: {
    name: string;
    build_volume_mm: Envelope;
    usable_envelope_mm: Envelope;
  };
  limits: {
    /** Design limit for maximum take-off mass, in kg. */
    design_mtow_kg: number;
    /** Legal limit (EU Open A3), in kg. */
    legal_mtow_kg: number;
    /** Mass at which the UI starts warning, in kg. */
    warn_mtow_kg: number;
  };
  checks: {
    hover_thrust_to_weight_min: number;
    static_margin_min: number;
    static_margin_max: number;
    cruise_to_stall_speed_ratio_min: number;
    battery_reserve_fraction: number;
    battery_current_max_fraction_of_rating: number;
  };
  units: {
    system: 'metric';
  };
}

/** Per-dotted-path metadata returned with the settings document. */
export interface SettingsMetaEntry {
  /** Display name for the row title and the input's accessible label. */
  label: string;
  description: string;
  source: string;
  /** True when the stored value equals the shipped default. */
  is_default: boolean;
}

/** GET/PUT /api/settings. */
export interface SettingsResponse {
  settings: Settings;
  meta: Record<string, SettingsMetaEntry>;
  /** Plain-language notes, e.g. stored values that no longer fit this version and were reset. */
  warnings: string[];
}

// ---------- Schema (labels and explanations) ----------

/** One option of an enum field, with an optional note shown through Explain. */
export interface EnumOption {
  value: string;
  label: string;
  note?: string;
}

/** Metadata for one mission or design field, keyed by dotted path. */
export interface FieldMeta {
  label: string;
  /** Unit symbol, or null/empty for dimensionless and text fields. */
  unit: string | null;
  /** Value type: number, integer, string, enum, boolean ... */
  type: string;
  /** Plain-language explanation shown by Explain. */
  description: string;
  min?: number;
  max?: number;
  enum?: EnumOption[];
}

/** Response of GET /api/schema/design and GET /api/schema/mission. */
export type SchemaMap = Record<string, FieldMeta>;

/** Response of GET /api/schema/notes. */
export interface SchemaNotes {
  /** That a new project's numbers are starting values, not an analysed design. */
  defaults: string;
}

// ---------- System ----------

/** GET /api/system/info. */
export interface SystemInfo {
  version: string;
  phase: number;
  environment: string;
  data_dir: string;
  backup: {
    last_run_at: string | null;
    next_run_at: string | null;
    count: number;
  };
}

/** Row of GET /api/system/backups. */
export interface BackupEntry {
  name: string;
  size_bytes: number;
  created_at: string;
}

/** GET /api/health (no auth). */
export interface HealthResponse {
  status: string;
  version: string;
  db: string;
}

// ---------- Reference images and Claude image readings (docs/phases/PHASE2.md section 4) ----------

export type ImageView = 'front' | 'side' | 'top' | 'three_quarter' | 'other';

/** Row of GET /api/projects/{id}/images and the 201 body of the upload. */
export interface ReferenceImage {
  id: number;
  filename: string;
  view: ImageView;
  width_px: number;
  height_px: number;
  size_bytes: number;
  /** Where the browser loads the file: /api/images/{id}/file. */
  url: string;
  created_at: string;
}

/** The real dimension a reading scales the image proportions by. */
export interface ReadingReference {
  parameter: 'wing.span_mm' | 'fuselage.length_mm';
  value_mm: number;
}

/** One proposed parameter value. */
export interface ProposedValue {
  value: number | string;
  unit: string | null;
  /** 0-1. */
  confidence: number;
  note: string;
}

/** What Claude proposes after reading the images (values already converted to mm and clamped). */
export interface ReadingProposal {
  layout: Layout;
  layout_confidence: number;
  layout_reason: string;
  /** Keyed by design dotted path ("wing.span_mm"). Parameters not visible are omitted. */
  parameters: Record<string, ProposedValue>;
  unmapped_notes: string[];
  warnings: string[];
}

export type ReadingStatus = 'running' | 'ok' | 'refused' | 'error';

/** GET /api/image-readings/{id}; POST answers 202 with status "running". */
export interface ImageReading {
  id: number;
  project_id: number;
  model: string;
  reference: ReadingReference;
  image_ids: number[];
  status: ReadingStatus;
  proposal: ReadingProposal | null;
  error: string | null;
  usage: Record<string, unknown> | null;
  created_at: string;
}

/** GET /api/image-readings/status. */
export interface ReadingAvailability {
  available: boolean;
  model: string;
  /** Plain message when not available (missing API key). */
  message: string | null;
}
