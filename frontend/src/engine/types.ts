/**
 * Public types of the Tier 1 engine (docs/phases/PHASE2.md section 5, docs/ENGINE.md).
 *
 * Every number shown to the owner is a Quantity: a nominal value, a low-high range, a unit,
 * a label, a plain-language explanation and the method/reference it came from.
 */

import type {
  AirfoilSummary,
  DesignParameters,
  Layout,
  Mission,
  Settings,
} from '../api/types';

/** One engine output number with its uncertainty range and explanation. */
export interface Quantity {
  /** Nominal (best-estimate) value; NaN when it cannot be computed (see statuses). */
  value: number;
  /** Lower end of the plausible range (same unit). */
  low: number;
  /** Upper end of the plausible range (same unit). */
  high: number;
  /** Unit symbol, for example "g", "mm", "m/s", "W", "min"; "" when dimensionless. */
  unit: string;
  /** Short display name. */
  label: string;
  /** One or two plain sentences: what the number means and why it matters. */
  explain: string;
  /** The method and reference it was computed with. */
  source: string;
}

export type StatusLevel = 'ok' | 'warn' | 'fail' | 'info';

/** A check or note with a plain message telling the owner what it means or what to change. */
export interface Status {
  key: string;
  label: string;
  level: StatusLevel;
  message: string;
}

/** Airfoil summaries keyed by airfoil id (GET /api/airfoils). */
export type AirfoilSummaryMap = Record<string, AirfoilSummary>;

export interface EngineInput {
  parameters: DesignParameters;
  mission: Mission;
  /** The Phase 1 settings document (limits and check thresholds). */
  settings: Settings;
  /** May be empty while the airfoil list is loading; the engine then uses stated fallbacks. */
  airfoils: AirfoilSummaryMap;
}

// ---------- Geometry ----------

export type Vec3 = [number, number, number];

export interface WingGeometry {
  span_mm: number;
  /** Reference (trapezoidal, extended through the fuselage) planform area. */
  area_m2: number;
  /** Planform area outside the fuselage sides. */
  exposed_area_m2: number;
  aspect_ratio: number;
  taper_ratio: number;
  root_chord_mm: number;
  tip_chord_mm: number;
  /** Mean aerodynamic chord length. */
  mac_mm: number;
  /** Spanwise station of the MAC from the centreline. */
  mac_y_mm: number;
  /** Leading-edge x of the MAC. */
  mac_x_le_mm: number;
  /** Aerodynamic centre: quarter chord of the MAC (x). */
  ac_x_mm: number;
  sweep_le_deg: number;
  sweep_quarter_chord_deg: number;
  sweep_max_thickness_deg: number;
  /** Airfoil thickness-to-chord ratio used. */
  thickness_ratio: number;
  /** Chordwise position of maximum thickness (fraction of chord). */
  x_max_thickness: number;
  wetted_area_m2: number;
  /** Wing root leading edge. */
  root_le: Vec3;
  /** Right tip leading edge (after sweep and dihedral). */
  tip_le: Vec3;
}

export interface TailGeometry {
  type: DesignParameters['tail']['type'];
  /** Total planform area of all tail panels (true panel area). */
  planform_area_m2: number;
  /** Horizontal-tail equivalent area (projected for V and inverted V: S cos(angle)). */
  horizontal_area_m2: number;
  /** Vertical-tail equivalent area (projected for V and inverted V: S sin(angle)). */
  vertical_area_m2: number;
  /** Area that is effective in pitch for the neutral point (S cos^2(angle) for V tails). */
  pitch_effective_area_m2: number;
  /** Aspect ratio of the horizontal (or V) surface, panel based. */
  aspect_ratio: number;
  chord_mm: number;
  /** Panel dihedral angle (0 conventional, +v_angle for V, -v_angle for inverted V). */
  panel_angle_deg: number;
  /** Wing aerodynamic centre to tail quarter chord. */
  arm_mm: number;
  /** Tail quarter-chord x position. */
  quarter_chord_x_mm: number;
  le_x_mm: number;
  te_x_mm: number;
  /** Tail root height above the centreline. */
  z_mm: number;
  thickness_ratio: number;
  x_max_thickness: number;
  wetted_area_m2: number;
  horizontal_volume_coefficient: number;
  vertical_volume_coefficient: number;
  /** Extra tube needed to carry the tail behind the fuselage or booms (0 when not needed). */
  support_length_mm: number;
  /** 'centre' tail boom behind the fuselage or 'booms' extensions (twin-boom H tail). */
  support_kind: 'centre' | 'booms';
}

export interface FuselageStation {
  x_mm: number;
  width_mm: number;
  height_mm: number;
  /** Perimeter of this cross-section. */
  perimeter_mm: number;
}

export interface FuselageGeometry {
  length_mm: number;
  width_mm: number;
  height_mm: number;
  cross_section: 'ellipse' | 'rounded_rect';
  /** Loft stations from nose to tail (shared by the 3D view and the wetted-area integral). */
  stations: FuselageStation[];
  nose_length_mm: number;
  tail_length_mm: number;
  max_cross_section_area_m2: number;
  /** Equivalent diameter sqrt(4 A / pi). */
  equivalent_diameter_mm: number;
  fineness_ratio: number;
  wetted_area_m2: number;
  /** Nose payload bay: x from 0 to its length. */
  nose_bay: { x0_mm: number; x1_mm: number; width_mm: number; height_mm: number };
}

export interface BoomGeometry {
  side: 'left' | 'right';
  start: Vec3;
  end: Vec3;
  diameter_mm: number;
  length_mm: number;
}

export interface RotorGeometry {
  id: 'front_left' | 'front_right' | 'rear_left' | 'rear_right' | 'pusher';
  /** Hub centre (top of the motor). */
  position: Vec3;
  diameter_mm: number;
  /** Unit vector of the thrust force: lift rotors (0, 0, 1) in hover; the pusher (-1, 0, 0) because x points aft. */
  axis: Vec3;
  /** True when this rotor tilts forward for cruise (front pair for front_tilt, rear for rear_tilt). */
  tilts: boolean;
  /** True when this rotor is stopped in cruise. */
  stopped_in_cruise: boolean;
}

/** Simple render primitives for the 3D view and drawings (no three.js types here). */
export interface RenderPrimitives {
  /** Each surface is a list of sections; each section is a closed airfoil polyline in aircraft coordinates. */
  surfaces: { name: string; sections: Vec3[][] }[];
  fuselage: { stations: FuselageStation[]; cross_section: 'ellipse' | 'rounded_rect'; nose_bay_x1_mm: number };
  booms: BoomGeometry[];
  tail_supports: { start: Vec3; end: Vec3; diameter_mm: number }[];
  motors: { id: RotorGeometry['id']; position: Vec3; diameter_mm: number; height_mm: number }[];
  props: { id: RotorGeometry['id']; centre: Vec3; diameter_mm: number; axis: Vec3 }[];
  tilt_hinges: { position: Vec3; axis: Vec3 }[];
  landing_gear: { type: 'skids' | 'legs' | 'none'; segments: { start: Vec3; end: Vec3 }[] };
}

export interface Geometry {
  layout: Layout;
  wing: WingGeometry;
  tail: TailGeometry;
  fuselage: FuselageGeometry;
  booms: BoomGeometry[];
  rotors: RotorGeometry[];
  /** Mean x of the four lift rotors weighted equally. */
  lift_rotor_centre_x_mm: number;
  front_rotor_x_mm: number;
  rear_rotor_x_mm: number;
  tilt_hinge_x_mm: number | null;
  landing_gear_bottom_z_mm: number;
  render: RenderPrimitives;
  /** Geometry problems found while building (overlaps, impossible values). */
  statuses: Status[];
}

// ---------- Mass and balance ----------

export interface MassComponent {
  key: string;
  label: string;
  /** Group used for the structure fraction and correlated uncertainty. */
  group: 'structure' | 'propulsion' | 'energy' | 'systems' | 'payload';
  mass_g: number;
  /** Relative 1-sigma-like uncertainty used for the range (for example 0.3 = +/-30 %). */
  uncertainty: number;
  x_mm: number;
  source: string;
  explain: string;
}

export interface MassResult {
  /** Components at the maximum payload (the payload row carries the maximum). */
  components: MassComponent[];
  empty: Quantity;
  battery: Quantity;
  structure: Quantity;
  takeoff_min_payload: Quantity;
  takeoff_max_payload: Quantity;
  structure_fraction: Quantity;
  converged: boolean;
  iterations: number;
  /** Sized motor maximum electrical power per lift motor (W). */
  lift_motor_max_power_w: number;
  lift_motor_mass_g: number;
  lift_motor_max_thrust_n: number;
  pusher_motor_max_power_w: number;
}

export interface BalanceResult {
  cg_min_payload_x: Quantity;
  cg_max_payload_x: Quantity;
  cg_min_payload_mac: Quantity;
  cg_max_payload_mac: Quantity;
  neutral_point_x: Quantity;
  static_margin_min_payload: Quantity;
  static_margin_max_payload: Quantity;
  /** Fraction of hover thrust carried by the front pair at maximum payload. */
  hover_front_share: Quantity;
}

// ---------- Aerodynamics and performance ----------

export interface DragItem {
  key: string;
  label: string;
  /** Drag area D/q in m^2. */
  drag_area_m2: number;
  /** Contribution to CD0 (based on wing reference area). */
  cd0: number;
  /** Skin-friction items only. */
  reynolds?: number;
  cf?: number;
  form_factor?: number;
  interference?: number;
  wetted_area_m2?: number;
  source: string;
}

export interface AeroResult {
  wing_area: Quantity;
  aspect_ratio: Quantity;
  mac: Quantity;
  wing_loading: Quantity;
  reynolds_cruise: Quantity;
  reynolds_stall: Quantity;
  lift_curve_slope: Quantity;
  cl_max: Quantity;
  stall_speed: Quantity;
  cl_cruise: Quantity;
  cruise_to_stall: Quantity;
  cd0: Quantity;
  drag_items: DragItem[];
  oswald: Quantity;
  cd_induced: Quantity;
  lift_to_drag: Quantity;
  drag_cruise: Quantity;
  tail_volume_h: Quantity;
  tail_volume_v: Quantity;
  downwash_gradient: Quantity;
}

export interface MissionSegment {
  key: 'takeoff_hover' | 'transition_out' | 'cruise' | 'transition_in' | 'landing_hover';
  label: string;
  duration_s: number;
  power_w: number;
  energy_wh: number;
}

export interface PerformanceResult {
  cruise_power: Quantity;
  hover_power: Quantity;
  transition_power: Quantity;
  hover_disc_loading: Quantity;
  peak_current: Quantity;
  battery_c_rate: Quantity;
  battery_energy: Quantity;
  usable_energy: Quantity;
  vtol_energy: Quantity;
  endurance_cruise: Quantity;
  endurance_total: Quantity;
  range: Quantity;
  endurance_cruise_min_payload: Quantity;
  hover_thrust_to_weight: Quantity;
  mission: MissionSegment[];
}

export interface Estimates {
  /** False when the input is degenerate; numbers are then NaN and statuses explain why. */
  valid: boolean;
  layout: Layout;
  geometry: Geometry | null;
  mass: MassResult | null;
  balance: BalanceResult | null;
  aero: AeroResult | null;
  performance: PerformanceResult | null;
  /** Checks first (ok/warn/fail), then notes (info). */
  statuses: Status[];
  /** Plain-language list of the main assumptions behind these numbers. */
  assumptions: string[];
  /** Milliseconds the estimate took (for the performance budget). */
  elapsed_ms: number;
}

export interface LayoutComparison {
  layout: Layout;
  label: string;
  takeoff_mass: Quantity;
  endurance: Quantity;
  cruise_power: Quantity;
  hover_power: Quantity;
  complexity: {
    rating: 'low' | 'medium' | 'high';
    /** Higher = more complex (count of complexity items). */
    score: number;
    reasons: string[];
  };
  has_tilt_mechanism: boolean;
  ardupilot_note: string;
  /** What was changed to compare fairly (battery moved to keep the balance point, tilt axis moved). */
  adjustments: string[];
  /**
   * The parameter changes behind this card's numbers, as [dotted path without "parameters.",
   * value]: the layout first, then any tilt axis and battery position. "Use this layout"
   * applies all of them, so the design matches what the card shows.
   */
  changes: [string, unknown][];
  /** Battery centre used for this layout, mm from the nose. */
  battery_x_mm: number;
  /** Checks that are warn or fail for this layout. */
  problems: Status[];
}
