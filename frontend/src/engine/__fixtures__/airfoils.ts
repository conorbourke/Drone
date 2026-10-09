/**
 * TEST VALUES ONLY. Hand-written airfoil summaries "typical of" SD7037 and NACA 0009 at low
 * Reynolds numbers (XFOIL, Ncrit 9 style), so the engine tests do not depend on the backend's
 * GET /api/airfoils. They are rounded, plausible numbers chosen by the engine author from
 * published low-Reynolds data trends (Selig et al., Summary of Low-Speed Airfoil Data, vol. 1-3),
 * NOT the backend's computed polars. Do not use them for design decisions.
 */

import type { AirfoilSummary } from '../../api/types';
import type { AirfoilSummaryMap } from '../types';

export const SD7037_TEST: AirfoilSummary = {
  id: 'sd7037',
  name: 'SD7037 (test values)',
  description: 'TEST VALUES: thin, gentle stall, good all-rounder for small UAV wings at low speed.',
  use: 'wing',
  thickness_pct: 9.2,
  x_thickness_pct: 28,
  camber_pct: 3.0,
  x_camber_pct: 40,
  source: 'Hand-written test values typical of SD7037 (not XFOIL output).',
  polar_summary: [
    { re: 60000, cl_max: 1.05, alpha_cl_max_deg: 11.0, alpha_zero_lift_deg: -2.8, cl_alpha_per_rad: 5.6, cd_min: 0.0180, cl_at_cd_min: 0.55, cm0: -0.075 },
    { re: 100000, cl_max: 1.14, alpha_cl_max_deg: 11.5, alpha_zero_lift_deg: -2.9, cl_alpha_per_rad: 5.8, cd_min: 0.0125, cl_at_cd_min: 0.50, cm0: -0.078 },
    { re: 200000, cl_max: 1.22, alpha_cl_max_deg: 12.0, alpha_zero_lift_deg: -3.0, cl_alpha_per_rad: 6.0, cd_min: 0.0092, cl_at_cd_min: 0.45, cm0: -0.080 },
    { re: 400000, cl_max: 1.30, alpha_cl_max_deg: 12.5, alpha_zero_lift_deg: -3.0, cl_alpha_per_rad: 6.1, cd_min: 0.0074, cl_at_cd_min: 0.40, cm0: -0.080 },
    { re: 800000, cl_max: 1.38, alpha_cl_max_deg: 13.0, alpha_zero_lift_deg: -3.0, cl_alpha_per_rad: 6.2, cd_min: 0.0062, cl_at_cd_min: 0.40, cm0: -0.080 },
    { re: 1500000, cl_max: 1.44, alpha_cl_max_deg: 13.5, alpha_zero_lift_deg: -3.0, cl_alpha_per_rad: 6.3, cd_min: 0.0055, cl_at_cd_min: 0.35, cm0: -0.080 },
    { re: 3000000, cl_max: 1.50, alpha_cl_max_deg: 14.0, alpha_zero_lift_deg: -3.0, cl_alpha_per_rad: 6.3, cd_min: 0.0050, cl_at_cd_min: 0.30, cm0: -0.080 },
  ],
};

export const NACA0009_TEST: AirfoilSummary = {
  id: 'naca0009',
  name: 'NACA 0009 (test values)',
  description: 'TEST VALUES: thin symmetric section for tail surfaces.',
  use: 'tail',
  thickness_pct: 9.0,
  x_thickness_pct: 30,
  camber_pct: 0,
  x_camber_pct: 0,
  source: 'Hand-written test values typical of NACA 0009 (not XFOIL output).',
  polar_summary: [
    { re: 60000, cl_max: 0.75, alpha_cl_max_deg: 9.0, alpha_zero_lift_deg: 0, cl_alpha_per_rad: 5.4, cd_min: 0.0160, cl_at_cd_min: 0, cm0: 0 },
    { re: 100000, cl_max: 0.82, alpha_cl_max_deg: 9.5, alpha_zero_lift_deg: 0, cl_alpha_per_rad: 5.6, cd_min: 0.0125, cl_at_cd_min: 0, cm0: 0 },
    { re: 200000, cl_max: 0.92, alpha_cl_max_deg: 10.5, alpha_zero_lift_deg: 0, cl_alpha_per_rad: 5.9, cd_min: 0.0090, cl_at_cd_min: 0, cm0: 0 },
    { re: 400000, cl_max: 1.05, alpha_cl_max_deg: 12.0, alpha_zero_lift_deg: 0, cl_alpha_per_rad: 6.1, cd_min: 0.0068, cl_at_cd_min: 0, cm0: 0 },
    { re: 800000, cl_max: 1.20, alpha_cl_max_deg: 13.5, alpha_zero_lift_deg: 0, cl_alpha_per_rad: 6.2, cd_min: 0.0058, cl_at_cd_min: 0, cm0: 0 },
    { re: 1500000, cl_max: 1.30, alpha_cl_max_deg: 14.5, alpha_zero_lift_deg: 0, cl_alpha_per_rad: 6.3, cd_min: 0.0052, cl_at_cd_min: 0, cm0: 0 },
    { re: 3000000, cl_max: 1.38, alpha_cl_max_deg: 15.5, alpha_zero_lift_deg: 0, cl_alpha_per_rad: 6.4, cd_min: 0.0047, cl_at_cd_min: 0, cm0: 0 },
  ],
};

export const TEST_AIRFOILS: AirfoilSummaryMap = { sd7037: SD7037_TEST, naca0009: NACA0009_TEST };
