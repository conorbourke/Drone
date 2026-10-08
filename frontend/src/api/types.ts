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

/** Design parameters document (schema_version 1): the editable geometry of the aircraft. */
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
  description: string;
  source: string;
  /** True when the stored value equals the shipped default. */
  is_default: boolean;
}

/** GET/PUT /api/settings. */
export interface SettingsResponse {
  settings: Settings;
  meta: Record<string, SettingsMetaEntry>;
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
