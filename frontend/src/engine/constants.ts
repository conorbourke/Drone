/**
 * Every constant the Tier 1 engine uses, each with a comment naming its source.
 *
 * Reference list (full titles in docs/ENGINE.md):
 *   [Raymer]    D. P. Raymer, Aircraft Design: A Conceptual Approach, 6th ed., AIAA, 2018 (ch. 12 "Aerodynamics", ch. 15 "Weights").
 *   [Anderson]  J. D. Anderson, Aircraft Performance and Design, McGraw-Hill, 1999; Fundamentals of Aerodynamics, 6th ed., 2017.
 *   [Leishman]  J. G. Leishman, Principles of Helicopter Aerodynamics, 2nd ed., Cambridge, 2006 (ch. 2 momentum theory, figure of merit).
 *   [Nelson]    R. C. Nelson, Flight Stability and Automatic Control, 2nd ed., McGraw-Hill, 1998 (ch. 2 static stability).
 *   [Etkin]     B. Etkin and L. D. Reid, Dynamics of Flight: Stability and Control, 3rd ed., Wiley, 1996.
 *   [Gundlach]  J. Gundlach, Designing Unmanned Aircraft Systems: A Comprehensive Approach, 2nd ed., AIAA, 2014.
 *   [Hoerner]   S. F. Hoerner, Fluid-Dynamic Drag, 1965.
 *   [Brandt]    J. B. Brandt and M. S. Selig, "Propeller Performance Data at Low Reynolds Numbers", AIAA 2011-1255 (UIUC propeller database).
 *   [ISA]       ICAO Doc 7488/3 Manual of the ICAO Standard Atmosphere / ISO 2533:1975.
 *
 * Values marked ESTIMATE are engineering estimates by the engine author with a stated
 * uncertainty; values marked PLACEHOLDER must be calibrated with built weights (Phase 6) or
 * real parts (Phase 4).
 */

import type { BatteryChemistry, Scale } from '../api/types';

// ---------- Physics and atmosphere ----------

/** Standard gravity, m/s^2. Source: CGPM 1901 / ISO 80000-3. */
export const G0 = 9.80665;
/** Sea-level ISA air density, kg/m^3. Source: [ISA]. */
export const RHO_SL = 1.225;
/** Sea-level ISA dynamic viscosity, kg/(m s). Source: [ISA] (Sutherland's law at 288.15 K). */
export const MU_SL = 1.789e-5;
/** Sea-level ISA speed of sound, m/s. Source: [ISA]. */
export const SPEED_OF_SOUND_SL = 340.294;

// ---------- Schema v2 defaults (docs/phases/PHASE2.md section 2) ----------

/** Defaults the engine uses when a schema v1 document lacks the v2 fields. Source: Phase 2 contract section 2. */
export const DESIGN_V2_DEFAULTS = {
  wing_twist_deg: 0,
  boom_diameter_mm: 20,
  tail_v_angle_deg: 40,
  tail_airfoil: 'naca0009',
  propulsion: { prop_diameter_mm: 330, prop_pitch_mm: 140, prop_blades: 2 },
  battery: { chemistry: 'lipo' as BatteryChemistry, cells_series: 6, cells_parallel: 1, capacity_mah: 5000, x_mm: 380 },
  allowances: { avionics_g: 220, wiring_fraction: 0.06 },
} as const;

// ---------- Airfoil fallbacks ----------

/**
 * Nominal thickness, its chordwise position, camber and camber position (all fractions of chord)
 * of the Phase 2 library airfoils, used for geometry before /api/airfoils has loaded (the
 * backend's computed values override them when present).
 * Source: UIUC Airfoil Coordinates Database nominal values; NACA 4-digit values exact from the
 * designation (Abbott & von Doenhoff, Theory of Wing Sections, 1959). ESTIMATE for x positions of
 * the non-NACA sections (+/-5 % chord).
 */
export const AIRFOIL_SHAPE_FALLBACK: Record<string, { t: number; xt: number; m: number; xm: number }> = {
  sd7037: { t: 0.092, xt: 0.28, m: 0.030, xm: 0.40 },
  sd7062: { t: 0.140, xt: 0.26, m: 0.040, xm: 0.40 },
  e387: { t: 0.091, xt: 0.31, m: 0.038, xm: 0.45 },
  mh32: { t: 0.087, xt: 0.29, m: 0.024, xm: 0.40 },
  s3021: { t: 0.095, xt: 0.28, m: 0.030, xm: 0.40 },
  ag35: { t: 0.087, xt: 0.28, m: 0.022, xm: 0.40 },
  clarky: { t: 0.117, xt: 0.28, m: 0.034, xm: 0.42 },
  naca2412: { t: 0.12, xt: 0.30, m: 0.02, xm: 0.40 },
  naca4412: { t: 0.12, xt: 0.30, m: 0.04, xm: 0.40 },
  naca0009: { t: 0.09, xt: 0.30, m: 0, xm: 0 },
  naca0012: { t: 0.12, xt: 0.30, m: 0, xm: 0 },
};

/** Generic airfoil when nothing is known: 10 % thick, 2 % camber. ESTIMATE, flagged by a status. */
export const AIRFOIL_SHAPE_GENERIC = { t: 0.10, xt: 0.30, m: 0.02, xm: 0.40 };

/**
 * Generic low-Reynolds polar used when the airfoil has no polar summary (warned by a status).
 * Values typical of 9-12 % thick cambered sections at Re 100k-300k with XFOIL Ncrit 9
 * (Selig et al., Summary of Low-Speed Airfoil Data vol. 1-3, 1995-97). ESTIMATE.
 */
export const GENERIC_POLAR = {
  cl_max: 1.15,
  alpha_cl_max_deg: 11,
  alpha_zero_lift_deg: -2.5,
  cl_alpha_per_rad: 5.9,
  cd_min: 0.011,
  cl_at_cd_min: 0.4,
  cm0: -0.06,
};
/** Generic symmetric tail-section polar (NACA 0009-like at Re ~100k). ESTIMATE. */
export const GENERIC_TAIL_POLAR = {
  cl_max: 0.85,
  alpha_cl_max_deg: 10,
  alpha_zero_lift_deg: 0,
  cl_alpha_per_rad: 5.7,
  cd_min: 0.012,
  cl_at_cd_min: 0,
  cm0: 0,
};

// ---------- Geometry conventions (also used by the Phase 3 Python port) ----------

/** Rounded-rectangle fuselage corner radius as a fraction of the smaller side. ESTIMATE (drawing convention). */
export const ROUNDED_RECT_CORNER_FRACTION = 0.25;
/** Nose length = min(NOSE_LENGTH_DIAMETERS x max(width, height), NOSE_LENGTH_MAX_FRACTION x length). ESTIMATE (drawing convention). */
export const NOSE_LENGTH_DIAMETERS = 1.2;
export const NOSE_LENGTH_MAX_FRACTION = 0.25;
/** Tail cone length = min(TAIL_LENGTH_DIAMETERS x max(width, height), TAIL_LENGTH_MAX_FRACTION x length). ESTIMATE. */
export const TAIL_LENGTH_DIAMETERS = 2.5;
export const TAIL_LENGTH_MAX_FRACTION = 0.35;
/** The tail cone ends at this fraction of the full cross-section. ESTIMATE. */
export const TAIL_END_SCALE = 0.3;
/** Number of segments in the elliptical nose of the fuselage loft. Convention. */
export const NOSE_SEGMENTS = 12;
/** Motor can diameter for drawings = this x prop diameter (drawing only; mass comes from mass.ts). Convention. */
export const MOTOR_CAN_DRAW_FRACTION = 0.13;

// ---------- Skin friction and form factors [Raymer ch. 12.5] ----------

/** Laminar flat-plate skin friction Cf = 1.328 / sqrt(Re) (Blasius). Source: [Raymer] eq. 12.25. */
export const CF_LAMINAR_COEFF = 1.328;
/** Turbulent Cf = 0.455 / (log10 Re)^2.58 / (1 + 0.144 M^2)^0.65. Source: [Raymer] eq. 12.27. */
export const CF_TURB_COEFF = 0.455;
export const CF_TURB_EXP = 2.58;

/**
 * Laminar-flow fraction of the wetted area. Source: [Raymer] 12.5.3 (smooth composite wings can
 * reach 50 % laminar; production surfaces 10-20 %). ESTIMATE for printed surfaces, which have layer
 * lines and seams: we assume low laminar fractions. Uncertainty covered by the drag factor.
 */
export const LAMINAR_FRACTION: Record<Scale, { wing: number; tail: number; body: number }> = {
  prototype: { wing: 0.15, tail: 0.15, body: 0.05 },
  final: { wing: 0.35, tail: 0.35, body: 0.10 },
};

/**
 * Equivalent sand-grain surface roughness k, metres, for the cutoff Reynolds number
 * R_cutoff = 38.21 (l/k)^1.053 ([Raymer] eq. 12.28). Source: [Raymer] table 12.5:
 * "production sheet metal" 1.33e-4 ft = 4.05e-5 m (used for sanded, painted 3D prints: ESTIMATE),
 * "smooth molded composite" 1.7e-6 ft = 5.2e-7 m (final carbon).
 */
export const SURFACE_ROUGHNESS_M: Record<Scale, number> = {
  prototype: 4.05e-5,
  final: 5.2e-7,
};

/**
 * Raymer's lifting-surface form factor contains a Mach term 1.34 M^0.18 that was fitted to data at
 * M >= ~0.2 and equals ~1.0 there. Below this Mach number we hold the term at its value at this Mach
 * (otherwise it would predict lower drag at lower speed). Source: [Raymer] eq. 12.30; engine decision.
 */
export const FORM_FACTOR_MACH_FLOOR = 0.2;

/**
 * Interference factors Q. Source: [Raymer] 12.5.4: high/mid wing Q = 1.0; fuselage 1.0;
 * conventional tail 1.04-1.05 (1.05 used), H tail 1.08, V tail 1.03; nacelle/pod within one
 * diameter of the wing 1.3. Booms: 1.1 ESTIMATE (most of their length is clear of the wing).
 */
export const INTERFERENCE = {
  wing: 1.0,
  fuselage: 1.0,
  tail_conventional: 1.05,
  tail_v: 1.03,
  tail_h: 1.08,
  boom: 1.1,
};

/** Leakage and protuberance drag as a fraction of the component build-up. Source: [Raymer] 12.5.5 (2-10 % typical); 10 % ESTIMATE for hatches, seams and screws on a small UAV. */
export const LEAKAGE_PROTUBERANCE_FRACTION = 0.10;

// ---------- Stopped propellers, motor pods, landing gear ----------

/** Flat plate normal to the flow, Cd on frontal area. Source: [Hoerner] ch. 3 (Cd ~ 1.17-1.2). */
export const CD_FLAT_PLATE = 1.2;
/**
 * Stopped propeller blade mean chord as a fraction of diameter, and thickness ratio at 0.75 R.
 * ESTIMATE from typical small-UAV propeller planforms (UIUC propeller geometry data, [Brandt]).
 */
export const PROP_MEAN_CHORD_FRACTION = 0.08;
export const PROP_BLADE_THICKNESS_RATIO = 0.12;
/** Fraction of the radius that is blade (outside the hub). ESTIMATE. */
export const PROP_BLADE_RADIUS_FRACTION = 0.85;
/**
 * A stopped lift propeller sits edge-on to the cruise flow at a random azimuth; its frontal area is
 * averaged over azimuth with the factor 2/pi. Engine method (docs/ENGINE.md, "stopped propellers").
 */
export const PROP_AZIMUTH_AVERAGE = 2 / Math.PI;
/** Short cylinder side-on (stopped motor can), Cd on frontal area. Source: [Hoerner] ch. 3 (short cylinders 0.6-0.8); 0.8 used. */
export const CD_MOTOR_CAN_SIDE = 0.8;
/** Running motor behind its spinner facing the flow, Cd on frontal area. ESTIMATE ([Hoerner] blunt bodies 0.2-0.4). */
export const CD_MOTOR_CAN_FRONT = 0.3;
/** Motor can geometry for drag: a cylinder of height = 0.6 x diameter and mean density 3500 kg/m^3. ESTIMATE from outrunner catalogue dimensions. */
export const MOTOR_CAN_HEIGHT_RATIO = 0.6;
export const MOTOR_CAN_DENSITY = 3500;
/** Round strut at small-UAV Reynolds numbers (subcritical), Cd on frontal area. Source: [Hoerner] ch. 3 (circular cylinder 1.0-1.2); 1.0 used. */
export const CD_ROUND_STRUT = 1.0;
/** Landing-gear strut diameter as a fraction of gear height (minimum 4 mm). ESTIMATE. */
export const GEAR_STRUT_DIAMETER_FRACTION = 0.06;

// ---------- Lift ----------

/** 3D maximum lift = 0.9 x section cl_max x cos(sweep c/4). Source: [Raymer] eq. 12.15 (high aspect ratio wings). */
export const CLMAX_3D_FACTOR = 0.9;
/** Fuselage lift factor F = 1.07 (1 + d/b)^2; F x S_exposed / S_ref capped at 0.98. Source: [Raymer] eqs. 12.6-12.9 and text. */
export const FUSELAGE_LIFT_FACTOR_COEFF = 1.07;
export const FUSELAGE_LIFT_CAP = 0.98;
/** Tail dynamic-pressure ratio eta_t. Source: [Nelson] ch. 2 (0.8-1.0 typical); 0.9 used. */
export const TAIL_EFFICIENCY = 0.9;
/** Oswald efficiency clamp, to keep the empirical fits in their valid band. Engine decision. */
export const OSWALD_MIN = 0.5;
export const OSWALD_MAX = 0.95;

// ---------- Propulsion and power ----------

/** Hover figure of merit of the lift propellers. Source: [Leishman] ch. 2 (well-designed rotors 0.7-0.8; small fixed-pitch propellers 0.5-0.7). Contract value. */
export const FIGURE_OF_MERIT_HOVER = 0.65;
/** Figure of merit at full throttle (motor sizing point). ESTIMATE: small props at high load, [Brandt] static data FM 0.5-0.6. */
export const FIGURE_OF_MERIT_MAX = 0.55;
/** Pusher static figure of merit (higher pitch, cruise-optimised prop). ESTIMATE from [Brandt]. */
export const FIGURE_OF_MERIT_PUSHER_STATIC = 0.6;
/** Brushless motor efficiency at hover and cruise. Source: typical outrunner peak 0.80-0.90 ([Gundlach] ch. 7); 0.85 used. */
export const ETA_MOTOR = 0.85;
/** Motor efficiency at full power (sizing). ESTIMATE. */
export const ETA_MOTOR_MAX = 0.8;
/** ESC efficiency. Source: [Gundlach] ch. 7 (0.93-0.97); 0.95 used. */
export const ETA_ESC = 0.95;
/**
 * Cruise propeller efficiency. Tilt layouts cruise on hover-sized propellers running lightly loaded;
 * the pusher has a propeller chosen for cruise. Source: [Brandt] (small propellers peak 0.55-0.80).
 * ESTIMATE until Phase 3 propeller model.
 */
export const ETA_PROP_CRUISE_TILT = 0.65;
export const ETA_PROP_CRUISE_PUSHER = 0.75;
/** Extra thrust to overcome the wing and boom download in the propeller wash. ESTIMATE ([Leishman] ch. 2: tiltrotor download ~10 %; quadplane props are mostly clear of the wing). */
export const HOVER_DOWNLOAD_FRACTION = 0.03;
/** Transition power as a multiple of hover power. Contract value (Phase 2 section 5), to be replaced by the Phase 3 transition model. */
export const TRANSITION_POWER_FACTOR = 1.25;
/** Pusher static thrust as a fraction of weight (to accelerate through transition and climb on the wing). ESTIMATE from common QuadPlane practice (0.3-0.6). */
export const PUSHER_THRUST_TO_WEIGHT = 0.5;
/** Continuous electrical power for avionics and servos, W. ESTIMATE (autopilot ~2.5 W, GPS ~0.5 W, telemetry ~1-2 W, receiver ~0.5 W, servos ~2-3 W). */
export const AVIONICS_POWER_W: Record<Scale, number> = { prototype: 8, final: 25 };
/** ESC current rating margin over the motor's full-power current. Common practice. */
export const ESC_CURRENT_MARGIN = 1.2;

// ---------- Mission profile (contract values, Phase 2 section 5) ----------

export const MISSION_PROFILE = {
  takeoff_hover_s: 45,
  transition_s: 15,
  landing_hover_s: 45,
  /** Usable fraction of the nominal pack energy before the reserve is taken (cell balance, voltage sag, ageing). Contract value. */
  usable_energy_factor: 0.95,
};

// ---------- Batteries ----------

/** Nominal cell voltage, V. Source: LiPo 3.7 V and Li-ion (NMC/NCA 18650/21700) 3.6 V, manufacturer datasheets. */
export const CELL_NOMINAL_V: Record<BatteryChemistry, number> = { lipo: 3.7, 'li-ion': 3.6 };
/**
 * Pack-level specific energy, Wh/kg, including wrap, wiring and connectors.
 * LiPo: hobby packs 130-160 Wh/kg (e.g. a 6S 5000 mAh 111 Wh pack weighs 750-820 g); 145 used.
 * Li-ion: 21700 NMC cells are 230-260 Wh/kg (e.g. Molicel P45B 4.5 Ah, 70 g), packs with nickel
 * strip and BMS-free wrap ~190-210 Wh/kg; 200 used. Source: manufacturer datasheets; [Gundlach]
 * ch. 7 battery tables. Uncertainty +/-10 % (statistical relation).
 */
export const PACK_SPECIFIC_ENERGY_WH_PER_KG: Record<BatteryChemistry, number> = { lipo: 145, 'li-ion': 200 };
export const PACK_SPECIFIC_ENERGY_UNCERTAINTY = 0.1;
/** Typical continuous discharge rating, C. PLACEHOLDER until real packs in Phase 4 (contract values). */
export const TYPICAL_C_RATING: Record<BatteryChemistry, number> = { lipo: 25, 'li-ion': 3 };

// ---------- Mass statistics (all PLACEHOLDER / statistical, see docs/ENGINE.md) ----------

/**
 * Structure areal densities, kg per m^2. Printed values are PLACEHOLDERS to be calibrated with the
 * Phase 6 built weights. Basis:
 *  - printed wing (per m^2 of planform, ribs and skin both sides, without the spar tube): LW-PLA
 *    skin ~0.45 mm at ~0.55 g/cm^3 over ~2.05 m^2 wetted per m^2 planform = ~0.5 kg/m^2, plus ~50 % for
 *    ribs, spar sockets and servo bays: 0.75 kg/m^2, +/-30 %;
 *  - printed fuselage (per m^2 of wetted area, with frames, motor-boom and wing mounts in tougher
 *    filament): 0.8 kg/m^2, +/-35 %;
 *  - printed tail (per m^2 of total panel planform): 0.5 kg/m^2, +/-35 %;
 *  - carbon wing (final, per m^2 planform, two 200 g/m^2 plies per skin with resin over a foam core,
 *    spar caps sized separately): 1.0 kg/m^2, +/-35 %;
 *  - carbon fuselage (per m^2 wetted, 3 plies + frames): 1.0 kg/m^2, +/-35 %;
 *  - carbon tail (per m^2 planform): 0.7 kg/m^2, +/-35 %.
 * Cross-check: structure fraction of small UAVs 25-35 % of take-off mass ([Gundlach] ch. 8).
 */
export const AREAL_DENSITY: Record<Scale, { wing: number; fuselage: number; tail: number }> = {
  prototype: { wing: 0.75, fuselage: 0.8, tail: 0.5 },
  final: { wing: 1.0, fuselage: 1.0, tail: 0.7 },
};
export const AREAL_DENSITY_UNCERTAINTY: Record<Scale, { wing: number; fuselage: number; tail: number }> = {
  prototype: { wing: 0.3, fuselage: 0.35, tail: 0.35 },
  final: { wing: 0.35, fuselage: 0.35, tail: 0.35 },
};

/**
 * Carbon tube mass per metre: m' = rho x pi x (D - t) x t with rho = 1550 kg/m^3 (roll-wrapped or
 * pultruded carbon/epoxy, ~60 % fibre volume) and wall t = max(1 mm, 0.05 D). Basis: tube geometry
 * with typical walls; checks against catalogue tubes (20x18 mm ~95 g/m, 8x6 mm ~33 g/m, 30x27 mm ~210 g/m).
 * Uncertainty +/-15 %.
 */
export const CARBON_TUBE_DENSITY = 1550;
export const CARBON_TUBE_MIN_WALL_M = 0.001;
export const CARBON_TUBE_WALL_FRACTION = 0.05;
export const CARBON_TUBE_UNCERTAINTY = 0.15;
/** Prototype wing spar tube diameter = this x root airfoil thickness, rounded to 1 mm, 8-30 mm. ESTIMATE. */
export const SPAR_TUBE_THICKNESS_FRACTION = 0.7;

/**
 * Final-scale bending-sized spar caps: ultimate load factor, allowable stress and a factor for
 * webs, joints and the root fitting. Load factor 4 limit x 1.5 safety ([Raymer] ch. 14 and
 * CS-23-style practice, ESTIMATE for a UAV); UD carbon allowable 600 MPa (knocked down from
 * ~1500 MPa for compression, defects and fatigue: ESTIMATE); spar depth 0.85 x root thickness.
 */
export const SPAR_ULTIMATE_LOAD_FACTOR = 6;
export const SPAR_ALLOWABLE_STRESS_PA = 600e6;
export const SPAR_DEPTH_FRACTION = 0.85;
export const SPAR_EXTRA_FACTOR = 1.3;
/** Spanwise lift centroid of an elliptic loading, fraction of semi-span: 4/(3 pi). Source: [Anderson] (elliptic lift distribution). */
export const ELLIPTIC_CENTROID = 4 / (3 * Math.PI);

/**
 * Motor mass versus maximum continuous electrical power: m[g] = 1.2 x P[W]^0.73, +/-30 %.
 * STATISTICAL (engine author's power-law fit to catalogue outrunners, for example ~55 g at 200 W,
 * ~100 g at 400 W, ~250 g at 1.6 kW for T-Motor MN/U-class multirotor motors). This is the same
 * form as published regressions (Gur and Rosen, J. Aircraft 46(4), 2009; Lundstrom et al., 2010)
 * but its coefficients are NOT taken from them. Replaced by real motors in Phase 4.
 */
export const MOTOR_MASS_COEFF = 1.2;
export const MOTOR_MASS_EXP = 0.73;
export const MOTOR_MASS_UNCERTAINTY = 0.3;
/** ESC mass m[g] = 1.0 x I_rated[A] + 5, +/-40 %. STATISTICAL fit to catalogue ESCs (20 A ~25 g, 40 A ~45 g, 80 A ~85 g). */
export const ESC_MASS_PER_A = 1.0;
export const ESC_MASS_FIXED_G = 5;
export const ESC_MASS_UNCERTAINTY = 0.4;
/** Propeller mass m[g] = 20 x (D / 0.3048 m)^2.5 x blades/2, +/-40 %. STATISTICAL fit (12" composite props ~20 g, 30" carbon ~190 g incl. adapter). */
export const PROP_MASS_REF_G = 20;
export const PROP_MASS_REF_DIAMETER_M = 0.3048;
export const PROP_MASS_EXP = 2.5;
export const PROP_MASS_UNCERTAINTY = 0.4;
/** Motor mount (printed or carbon plate plus screws) as a fraction of motor mass. ESTIMATE, +/-50 %. */
export const MOTOR_MOUNT_FRACTION = 0.2;
/** Tilt mechanism per tilting side: servo + hinge + bearings = 25 g + 0.25 x (motor + prop). ESTIMATE, +/-50 %. */
export const TILT_MECH_FIXED_G = 25;
export const TILT_MECH_FRACTION = 0.25;
/** Control servos (2 aileron + 2 tail): each 6 g + 0.0025 x take-off mass in g. ESTIMATE (9 g class at 2.5 kg, 60-70 g class at 24 kg), +/-40 %. */
export const CONTROL_SERVO_COUNT = 4;
export const CONTROL_SERVO_FIXED_G = 6;
export const CONTROL_SERVO_PER_TAKEOFF_G = 0.0025;
/** Landing gear as a fraction of take-off mass. ESTIMATE (light UAV skids/legs; [Raymer] ch. 15 gives 3-6 % for light aircraft gear). +/-50 %. */
export const LANDING_GEAR_FRACTION: Record<'skids' | 'legs' | 'none', number> = { skids: 0.03, legs: 0.04, none: 0 };
/** Default uncertainty for allowances entered by the owner (avionics, wiring fraction). ESTIMATE. */
export const ALLOWANCE_UNCERTAINTY = 0.2;

/** Mass fixed-point iteration: at most 20 iterations, converged when the change is below 0.05 g. Contract (max 20). */
export const MASS_MAX_ITERATIONS = 20;
export const MASS_TOLERANCE_G = 0.05;

// ---------- Uncertainty factors (docs/ENGINE.md "Uncertainty model") ----------

export const UNCERTAINTY = {
  /** Parasite + induced drag at Tier 1 fidelity, +/-15 % (contract example; Raymer build-up typically within 10-20 %). */
  drag: 0.15,
  /** Usable battery energy, +/-5 % (contract example; cell capacity spread and temperature). */
  battery_energy: 0.05,
  /** Section maximum lift and the 0.9 factor, +/-10 % ([Raymer] 12.4: the 0.9 rule is approximate). */
  cl_max: 0.1,
  /** Figure of merit +/-0.05 absolute around 0.65 ([Leishman] ch. 2 band for small rotors). */
  figure_of_merit_abs: 0.05,
  /** Combined motor x ESC x propeller efficiency, +/-7 %. ESTIMATE. */
  propulsive_efficiency: 0.07,
  /** Neutral point position, +/-5 % of the MAC (Tier 1 vs vortex-lattice agreement, typical). ESTIMATE. */
  neutral_point_mac: 0.05,
  /** Lift-curve slope, +/-8 % (Helmbold/DATCOM vs wind-tunnel data, [Raymer] 12.4). */
  lift_slope: 0.08,
};

/**
 * Upper limit on the section lift-curve slope fed to Helmbold, per radian: 2 pi, thin-airfoil
 * theory (Anderson, Fundamentals of Aerodynamics ch. 4). Between Re 60k and 200k the XFOIL fit
 * over -2..6 degrees can exceed it (SD7037 gives 8.57/rad at 60k) because a laminar separation
 * bubble shifts the lift curve over part of the fit range. That is a local kink, not a steeper
 * whole-wing slope, and putting it into Helmbold would move the neutral point aft.
 */
export const SECTION_CL_ALPHA_MAX = 2 * Math.PI;

/**
 * When an XFOIL alpha sweep ended before the section stalled, its cl_max is a lower bound. The
 * true value may be this much higher (relative); used only to widen the low side of the
 * stall-speed range (and the high side of CL_max). ESTIMATE: sweeps that stop at the end of the
 * table usually sit within 0.1-0.2 of the real peak.
 */
export const CL_MAX_SWEEP_END_EXTRA = 0.15;

// ---------- Check thresholds not in settings ----------

/** Tail volume coefficient guidance. Source: [Raymer] table 6.4 (homebuilt c_HT 0.50, c_VT 0.04; sailplane 0.50 / 0.02). */
export const TAIL_VOLUME_H_RANGE: [number, number] = [0.3, 0.8];
export const TAIL_VOLUME_V_MIN = 0.02;
/** Cruise lift coefficient above this fraction of CLmax leaves little margin for gusts and turns. ESTIMATE (engine decision). */
export const CL_CRUISE_MAX_FRACTION = 0.7;
