/**
 * Performance (docs/ENGINE.md "Performance"): cruise power from drag through the generic
 * propeller CT(J), CP(J) model at the cruise thrust and speed, hover power from momentum
 * theory with a figure of merit, transition power, battery current and C-rate, and the mission
 * energy budget giving endurance and range. Ranges come from one-at-a-time perturbation of the
 * stated uncertainty factors, combined by root-sum-square (quantity.ts, rule 2).
 */

import {
  ETA_ESC,
  ETA_MOTOR,
  FIGURE_OF_MERIT_HOVER,
  G0,
  HOVER_DOWNLOAD_FRACTION,
  MISSION_PROFILE,
  PROP_COEFF_UNCERTAINTY,
  TRANSITION_POWER_FACTOR,
  UNCERTAINTY,
} from './constants';
import { idealHoverPower } from './mass';
import { propOperatingPoint, type GenericPropeller } from './propeller';
import { oneAtATime, qRange, type QuantityText } from './quantity';
import type { MissionSegment, Quantity } from './types';
import { clamp } from './units';

export interface PerfInputs {
  massKg: number;
  /** Front-pair share of hover thrust, 0-1. */
  frontShare: number;
  rho: number;
  speed: number;
  wingArea: number;
  cd0: number;
  oswald: number;
  aspectRatio: number;
  /**
   * The cruise propeller(s): the generic propeller model, solved at thrust = drag / count and the
   * cruise speed for its efficiency; or a fixed efficiency (hand-calculation tests only).
   */
  cruiseProp: CruiseProp;
  /** Area of one lift propeller disc, m^2. */
  discArea: number;
  avionicsW: number;
  energyWh: number;
  reserveFraction: number;
  packVoltage: number;
  capacityAh: number;
}

export type CruiseProp = { propeller: GenericPropeller; count: number } | { fixedEfficiency: number };

export interface PerfFactors {
  drag: number;
  mass: number;
  energy: number;
  fmDelta: number;
  eta: number;
  /** Cruise propeller thrust and power coefficient factors (+/-15 % each, as the server). */
  ct: number;
  cp: number;
}

export const NOMINAL_FACTORS: PerfFactors = { drag: 1, mass: 1, energy: 1, fmDelta: 0, eta: 1, ct: 1, cp: 1 };

export interface PerfCore {
  weightN: number;
  cl: number;
  cdInduced: number;
  cd: number;
  dragN: number;
  liftToDrag: number;
  /** Cruise propeller efficiency J CT / CP at the cruise operating point (or the fixed value). */
  cruisePropEfficiency: number;
  /** Cruise advance ratio J (0 with a fixed efficiency). */
  cruisePropJ: number;
  cruisePowerW: number;
  hoverPowerW: number;
  transitionPowerW: number;
  energyNominalWh: number;
  usableWh: number;
  vtolWh: number;
  cruiseWh: number;
  cruiseTimeS: number;
  totalTimeS: number;
  rangeM: number;
  peakCurrentA: number;
  cRate: number;
  discLoading: number;
  segments: MissionSegment[];
}

/** Closed-form performance for one set of inputs and uncertainty factors. */
export function performanceCore(i: PerfInputs, f: PerfFactors = NOMINAL_FACTORS): PerfCore {
  const W = i.massKg * f.mass * G0;
  const q = 0.5 * i.rho * i.speed * i.speed;
  const cl = W / (q * i.wingArea);
  const cdi = (cl * cl) / (Math.PI * i.oswald * i.aspectRatio);
  const cd = (i.cd0 + cdi) * f.drag;
  const drag = q * i.wingArea * cd;
  const prop = i.cruiseProp;
  const op = 'propeller' in prop
    ? propOperatingPoint(prop.propeller, drag / Math.max(1, prop.count), i.speed, i.rho, f.ct, f.cp)
    : { j: 0, efficiency: prop.fixedEfficiency };
  const etaCruise = op.efficiency * ETA_MOTOR * ETA_ESC * f.eta;
  const cruisePower = (drag * i.speed) / etaCruise + i.avionicsW;

  const thrust = W * (1 + HOVER_DOWNLOAD_FRACTION);
  const share = clamp(i.frontShare, 0, 1);
  const fm = FIGURE_OF_MERIT_HOVER + f.fmDelta;
  const shaft =
    (2 * idealHoverPower((thrust * share) / 2, i.discArea, i.rho) + 2 * idealHoverPower((thrust * (1 - share)) / 2, i.discArea, i.rho)) / fm;
  const hoverMotorPower = shaft / (ETA_MOTOR * ETA_ESC * f.eta);
  const hoverPower = hoverMotorPower + i.avionicsW;
  const transitionPower = TRANSITION_POWER_FACTOR * hoverMotorPower + i.avionicsW;

  const energy = i.energyWh * f.energy;
  const usable = energy * (1 - i.reserveFraction) * MISSION_PROFILE.usable_energy_factor;
  const mp = MISSION_PROFILE;
  const vtolWh = (hoverPower * (mp.takeoff_hover_s + mp.landing_hover_s) + transitionPower * 2 * mp.transition_s) / 3600;
  const cruiseWh = Math.max(0, usable - vtolWh);
  const cruiseTime = (cruiseWh * 3600) / cruisePower;
  const peakCurrent = transitionPower / i.packVoltage;
  const segments: MissionSegment[] = [
    { key: 'takeoff_hover', label: 'Take-off hover', duration_s: mp.takeoff_hover_s, power_w: hoverPower, energy_wh: (hoverPower * mp.takeoff_hover_s) / 3600 },
    { key: 'transition_out', label: 'Transition to wing flight', duration_s: mp.transition_s, power_w: transitionPower, energy_wh: (transitionPower * mp.transition_s) / 3600 },
    { key: 'cruise', label: 'Cruise', duration_s: cruiseTime, power_w: cruisePower, energy_wh: cruiseWh },
    { key: 'transition_in', label: 'Transition back to hover', duration_s: mp.transition_s, power_w: transitionPower, energy_wh: (transitionPower * mp.transition_s) / 3600 },
    { key: 'landing_hover', label: 'Landing hover', duration_s: mp.landing_hover_s, power_w: hoverPower, energy_wh: (hoverPower * mp.landing_hover_s) / 3600 },
  ];
  return {
    weightN: W,
    cl,
    cdInduced: cdi,
    cd,
    dragN: drag,
    liftToDrag: cl / cd,
    cruisePropEfficiency: op.efficiency,
    cruisePropJ: op.j,
    cruisePowerW: cruisePower,
    hoverPowerW: hoverPower,
    transitionPowerW: transitionPower,
    energyNominalWh: energy,
    usableWh: usable,
    vtolWh,
    cruiseWh,
    cruiseTimeS: cruiseTime,
    totalTimeS: cruiseTime + 2 * mp.transition_s + mp.takeoff_hover_s + mp.landing_hover_s,
    rangeM: cruiseTime * i.speed,
    peakCurrentA: peakCurrent,
    cRate: peakCurrent / i.capacityAh,
    discLoading: thrust / (4 * i.discArea),
    segments,
  };
}

/** Nominal result plus the one-at-a-time perturbed results for each uncertainty factor. */
export function performanceWithRanges(i: PerfInputs, massRel: number) {
  const nominal = performanceCore(i);
  const mr = Number.isFinite(massRel) ? massRel : 0.1;
  const perturb = (fl: Partial<PerfFactors>, fh: Partial<PerfFactors>): [PerfCore, PerfCore] => [
    performanceCore(i, { ...NOMINAL_FACTORS, ...fl }),
    performanceCore(i, { ...NOMINAL_FACTORS, ...fh }),
  ];
  const sets = {
    drag: perturb({ drag: 1 - UNCERTAINTY.drag }, { drag: 1 + UNCERTAINTY.drag }),
    mass: perturb({ mass: 1 - mr }, { mass: 1 + mr }),
    energy: perturb({ energy: 1 - UNCERTAINTY.battery_energy }, { energy: 1 + UNCERTAINTY.battery_energy }),
    fm: perturb({ fmDelta: -UNCERTAINTY.figure_of_merit_abs }, { fmDelta: UNCERTAINTY.figure_of_merit_abs }),
    eta: perturb({ eta: 1 - UNCERTAINTY.propulsive_efficiency }, { eta: 1 + UNCERTAINTY.propulsive_efficiency }),
    ct: perturb({ ct: 1 - PROP_COEFF_UNCERTAINTY }, { ct: 1 + PROP_COEFF_UNCERTAINTY }),
    cp: perturb({ cp: 1 - PROP_COEFF_UNCERTAINTY }, { cp: 1 + PROP_COEFF_UNCERTAINTY }),
  };
  /** Build a Quantity for one output, using only the named factors. */
  const quantity = (
    pick: (c: PerfCore) => number,
    factors: (keyof typeof sets)[],
    text: QuantityText,
    scale = 1,
  ): Quantity => {
    const v = pick(nominal) * scale;
    const r = oneAtATime(v, factors.map((k) => [pick(sets[k][0]) * scale, pick(sets[k][1]) * scale]));
    return qRange(v, r.low, r.high, text);
  };
  return { nominal, quantity };
}
