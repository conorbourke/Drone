/**
 * Generic fixed-pitch propeller CT(J), CP(J): a compact port of the server engine's model
 * (backend/app/engine/propulsion.py, same coefficients and sources), used for the cruise
 * propeller efficiency. docs/ENGINE.md "Performance".
 *
 * Propeller (fixed pitch, axial inflow; [Brandt] and the UIUC Propeller Data Site, vols. 1-2):
 *
 *   T = CT(J) rho n^2 D^4,   P = CP(J) rho n^3 D^5,   J = V / (n D),   eta = J CT / CP.
 *
 * Static coefficients from the pitch-to-diameter ratio (two blades):
 *
 *   CT0 = 0.040 + 0.150 (P/D)  (P/D <= 0.6; 0.130 + 0.050 (P/D - 0.6) above),
 *   CP0 = 0.005 + 0.085 (P/D) + 0.06 max(0, P/D - 0.5)^2,
 *
 * and a fall-off with advance ratio that reaches zero thrust at J_T = 1.1 (P/D) + 0.05:
 *
 *   CT(J) = CT0 (1 - 0.5 x - 0.5 x^2),   CP(J) = CP0 max(0.05, 1 - 0.7 x^2),   x = J / J_T.
 *
 * These are the server engine author's fit to the trends of the published UIUC data for small
 * fixed-pitch propellers (APC Slow Flyer 10x4.7: CT0 ~0.11, CP0 ~0.045, zero thrust near J 0.55,
 * peak efficiency ~0.6 near J 0.4-0.45), not a regression on the data files: +/-15 % on CT and CP.
 * More blades scale CT0 by (B/2)^0.75 and CP0 by (B/2)^0.9 (solidity trend, estimate).
 * Reynolds-number and blade-flexibility effects are not modelled.
 */

export interface GenericPropeller {
  diameterM: number;
  pitchRatio: number;
  blades: number;
  ct0: number;
  cp0: number;
  /** Advance ratio of zero thrust. */
  jZeroThrust: number;
}

/** Two-blade static thrust coefficient against pitch/diameter (UIUC trend fit). */
export function staticCt0(pd: number): number {
  return pd <= 0.6 ? 0.04 + 0.15 * pd : 0.13 + 0.05 * (pd - 0.6);
}

/** Two-blade static power coefficient against pitch/diameter (UIUC trend fit). */
export function staticCp0(pd: number): number {
  return 0.005 + 0.085 * pd + 0.06 * Math.max(0, pd - 0.5) ** 2;
}

export function genericPropeller(diameterMm: number, pitchMm: number, blades = 2): GenericPropeller {
  const pd = Math.min(1.2, Math.max(0.2, pitchMm / diameterMm));
  const b = Math.max(2, Math.trunc(blades)) / 2;
  return {
    diameterM: diameterMm / 1000,
    pitchRatio: pd,
    blades: Math.trunc(blades),
    ct0: staticCt0(pd) * b ** 0.75,
    cp0: staticCp0(pd) * b ** 0.9,
    jZeroThrust: 1.1 * pd + 0.05,
  };
}

/** Thrust coefficient at advance ratio J (ctFactor scales it for the uncertainty range). */
export function propCt(p: GenericPropeller, j: number, ctFactor = 1): number {
  const x = Math.max(0, j) / p.jZeroThrust;
  return p.ct0 * ctFactor * (1 - 0.5 * x - 0.5 * x * x);
}

/** Power coefficient at advance ratio J. */
export function propCp(p: GenericPropeller, j: number, cpFactor = 1): number {
  const x = Math.max(0, j) / p.jZeroThrust;
  return p.cp0 * cpFactor * Math.max(0.05, 1 - 0.7 * x * x);
}

export interface PropOperatingPoint {
  j: number;
  efficiency: number;
  rpm: number;
  shaftPowerW: number;
}

/**
 * Operating point of one propeller giving thrust `thrustN` at airspeed `speed` (axial inflow).
 * With n = V / (J D), T = CT(J) rho V^2 D^2 / J^2, which falls monotonically from infinity at
 * J -> 0 to zero at J_T, so J is found by bisection. The efficiency J CT / CP follows; shaft
 * power is T V / eta. No motor is needed for this (the motor and ESC efficiencies are applied
 * separately in performance.ts).
 */
export function propOperatingPoint(
  p: GenericPropeller,
  thrustN: number,
  speed: number,
  rho: number,
  ctFactor = 1,
  cpFactor = 1,
): PropOperatingPoint {
  const D = p.diameterM;
  if (!(speed > 0) || !(thrustN > 0) || !(D > 0)) return { j: 0, efficiency: 0, rpm: 0, shaftPowerW: 0 };
  const target = thrustN / (rho * speed * speed * D * D);
  let lo = 1e-6;
  let hi = p.jZeroThrust;
  for (let k = 0; k < 80; k++) {
    const mid = 0.5 * (lo + hi);
    if (propCt(p, mid, ctFactor) / (mid * mid) > target) lo = mid;
    else hi = mid;
  }
  const j = 0.5 * (lo + hi);
  const efficiency = (j * propCt(p, j, ctFactor)) / propCp(p, j, cpFactor);
  const n = speed / (j * D);
  return { j, efficiency, rpm: n * 60, shaftPowerW: (thrustN * speed) / efficiency };
}
