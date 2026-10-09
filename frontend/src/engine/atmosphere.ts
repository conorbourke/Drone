/**
 * Sea-level International Standard Atmosphere (ICAO Doc 7488 / ISO 2533). Tier 1 evaluates
 * everything at sea level; altitude and temperature effects arrive with later phases.
 */

import { MU_SL, RHO_SL, SPEED_OF_SOUND_SL } from './constants';

export interface Atmosphere {
  /** Air density, kg/m^3. */
  rho: number;
  /** Dynamic viscosity, kg/(m s). */
  mu: number;
  /** Speed of sound, m/s. */
  a: number;
}

/** Sea-level ISA air. */
export const SEA_LEVEL: Atmosphere = { rho: RHO_SL, mu: MU_SL, a: SPEED_OF_SOUND_SL };

/** Reynolds number rho V L / mu for a length in metres. */
export function reynolds(atm: Atmosphere, speedMps: number, lengthM: number): number {
  return (atm.rho * speedMps * lengthM) / atm.mu;
}

/** Dynamic pressure q = 0.5 rho V^2, Pa. */
export function dynamicPressure(atm: Atmosphere, speedMps: number): number {
  return 0.5 * atm.rho * speedMps * speedMps;
}

/** Mach number V / a. */
export function mach(atm: Atmosphere, speedMps: number): number {
  return speedMps / atm.a;
}
