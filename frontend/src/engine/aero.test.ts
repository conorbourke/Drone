import { describe, expect, it } from 'vitest';
import { NACA0009_TEST, SD7037_TEST } from './__fixtures__/airfoils';
import {
  cfLaminar,
  cfTurbulent,
  cutoffReynolds,
  downwashGradient,
  formFactorBody,
  formFactorLifting,
  inducedDragCoefficient,
  interpolatePolar,
  liftCurveSlope,
  munkFactor,
  oswaldEfficiency,
  skinFriction,
  stallSpeed,
  stoppedPropDragArea,
  wingClMax,
} from './aero';

describe('stall speed (Anderson)', () => {
  it('reproduces a CP-1 style textbook case by hand', () => {
    // Inputs of Anderson's CP-1 light-aircraft example (Introduction to Flight; Aircraft Performance
    // and Design ch. 5): W = 2950 lb, S = 174 ft^2, CL,max = 1.62, sea level rho = 0.002377 slug/ft^3.
    // V_stall = sqrt(2 W / (rho S CL,max)) = sqrt(5900 / 0.670 03) = 93.84 ft/s (hand calculation).
    expect(stallSpeed(2950, 0.002377, 174, 1.62)).toBeCloseTo(93.838, 2);
    // Same case in SI: W = 13 122.25 N, S = 16.165 m^2, rho = 1.225 kg/m^3 -> 28.60 m/s.
    expect(stallSpeed(13122.25, 1.225, 16.16513, 1.62)).toBeCloseTo(28.60, 2);
  });

  it('3D maximum lift is 0.9 x section cl_max x cos(sweep) (Raymer)', () => {
    expect(wingClMax(1.3, 0)).toBeCloseTo(1.17, 12);
    expect(wingClMax(1.3, Math.PI / 6)).toBeCloseTo(1.17 * Math.cos(Math.PI / 6), 12);
  });
});

describe('induced drag (Anderson)', () => {
  it('CP-1 style: b = 35.8 ft, S = 174 ft^2, e = 0.8, CL = 0.5 -> CDi = 0.01350', () => {
    // A = 35.8^2 / 174 = 7.3657; CDi = 0.25 / (pi x 0.8 x 7.3657) = 0.013505 (hand calculation).
    expect(inducedDragCoefficient(0.5, 0.8, (35.8 * 35.8) / 174)).toBeCloseTo(0.013505, 5);
  });

  it('Oswald efficiency by Raymer’s straight-wing fit: A = 8 -> 0.8106', () => {
    // 1.78 (1 - 0.045 x 8^0.68) - 0.64 = 0.81059 (hand calculation).
    expect(oswaldEfficiency(8, 0)).toBeCloseTo(0.81059, 4);
    // Blended, continuous across the 25-35 degree band and clamped.
    expect(Math.abs(oswaldEfficiency(8, 29.9) - oswaldEfficiency(8, 30.1))).toBeLessThan(0.01);
    expect(oswaldEfficiency(30, 0)).toBeGreaterThanOrEqual(0.5);
  });
});

describe('skin friction and form factors (Raymer ch. 12.5)', () => {
  it('flat-plate skin friction at Re = 1e6', () => {
    // Laminar 1.328 / sqrt(1e6) = 0.001328; turbulent 0.455 / 6^2.58 = 0.0044708 (M = 0),
    // 0.0044335 at M = 0.3 (hand calculation).
    expect(cfLaminar(1e6)).toBeCloseTo(0.001328, 9);
    expect(cfTurbulent(1e6, 0)).toBeCloseTo(0.0044708, 7);
    expect(cfTurbulent(1e6, 0.3)).toBeCloseTo(0.0044335, 7);
    // Laminar-fraction weighting with no roughness limit (very smooth surface).
    expect(skinFriction(1e6, 0, 0.25, 1, 1e-12)).toBeCloseTo(0.25 * 0.001328 + 0.75 * 0.0044708, 7);
  });

  it('roughness cutoff Reynolds number 38.21 (l/k)^1.053 limits the turbulent Reynolds number', () => {
    // l = 1 m, k = 4.05e-5 m (production sheet metal) -> 1.6126e6 (hand calculation).
    expect(cutoffReynolds(1, 4.05e-5)).toBeCloseTo(1.6126e6, -3);
    expect(skinFriction(1e7, 0, 0, 1, 4.05e-5)).toBeCloseTo(cfTurbulent(1.6126e6, 0), 6);
  });

  it('form factors: 12 % wing at 30 % chord, M 0.3 -> 1.3602; body of fineness 6 -> 1.2552', () => {
    // [1 + (0.6/0.3) 0.12 + 100 x 0.12^4] [1.34 x 0.3^0.18] = 1.260736 x 1.078901 = 1.36023.
    expect(formFactorLifting(0.12, 0.3, 0.3, 0)).toBeCloseTo(1.36023, 4);
    // 0.9 + 5 / 6^1.5 + 6 / 400 = 1.25521.
    expect(formFactorBody(6)).toBeCloseTo(1.25521, 4);
    // Below M = 0.2 the Mach term is held at its M = 0.2 value (~1.0).
    expect(formFactorLifting(0.12, 0.3, 0.05, 0)).toBeCloseTo(formFactorLifting(0.12, 0.3, 0.2, 0), 12);
  });

  it('stopped propeller drag area by the stated edge-on flat-plate method', () => {
    // D = 0.33 m, pitch 0.14 m: c = 0.0264 m, theta = atan(0.14 / (0.75 pi 0.33)) = 10.20 deg,
    // h = c sin(theta) + 0.12 c cos(theta) = 0.0077961 m;
    // D/q = 1.2 x 2 x 0.85 x 0.165 x h x 2/pi = 0.0016706 m^2 (hand calculation).
    expect(stoppedPropDragArea(330, 140, 2)).toBeCloseTo(0.0016706, 6);
  });
});

describe('lift-curve slope (Helmbold / DATCOM)', () => {
  it('A = 6, 2D slope 2 pi, unswept, incompressible: 4.5287 /rad, equal to Anderson’s Helmbold form', () => {
    const a0 = 2 * Math.PI;
    const anderson = a0 / (Math.sqrt(1 + (a0 / (Math.PI * 6)) ** 2) + a0 / (Math.PI * 6));
    expect(liftCurveSlope(6, 0, a0, 0)).toBeCloseTo(4.52866, 4);
    expect(liftCurveSlope(6, 0, a0, 0)).toBeCloseTo(anderson, 10);
  });

  it('sweep and a lower section slope reduce it; the fuselage factor above 1 is replaced by 0.98', () => {
    expect(liftCurveSlope(8, 0.5, 6, 0)).toBeLessThan(liftCurveSlope(8, 0, 6, 0));
    expect(liftCurveSlope(8, 0, 5.5, 0)).toBeLessThan(liftCurveSlope(8, 0, 6.2, 0));
    expect(liftCurveSlope(8, 0, 6, 0, 1.1)).toBeCloseTo(0.98 * liftCurveSlope(8, 0, 6, 0), 12);
    expect(liftCurveSlope(8, 0, 6, 0, 0.95)).toBeCloseTo(0.95 * liftCurveSlope(8, 0, 6, 0), 12);
  });

  it('downwash gradient 2 CLa / (pi A) and Munk factor monotonic in fineness', () => {
    expect(downwashGradient(4.5, 8)).toBeCloseTo(9 / (8 * Math.PI), 12);
    expect(munkFactor(4)).toBeCloseTo(0.78, 6);
    expect(munkFactor(7)).toBeGreaterThan(munkFactor(5));
  });
});

describe('airfoil polar interpolation', () => {
  it('interpolates linearly in log(Re) and clamps outside the table', () => {
    const mid = interpolatePolar(SD7037_TEST, Math.sqrt(100000 * 200000), 'wing');
    expect(mid.quality).toBe('table');
    expect(mid.polar.cl_max).toBeCloseTo((1.14 + 1.22) / 2, 10);
    const low = interpolatePolar(SD7037_TEST, 20000, 'wing');
    expect(low.quality).toBe('clamped');
    expect(low.polar.cl_max).toBe(1.05);
    const none = interpolatePolar(undefined, 1e5, 'tail');
    expect(none.quality).toBe('generic');
    expect(interpolatePolar(NACA0009_TEST, 100000, 'tail').polar.cl_alpha_per_rad).toBe(5.6);
  });
});

describe('section lift-curve slope cap (laminar-bubble XFOIL fits)', () => {
  it('slopes above 2 pi are treated as 2 pi; lower slopes are used as given', () => {
    // SD7037 at Re 60k: the XFOIL fit gives 8.57 /rad.
    expect(liftCurveSlope(8, 0, 8.57, 0)).toBeCloseTo(liftCurveSlope(8, 0, 2 * Math.PI, 0), 12);
    expect(liftCurveSlope(8, 0, 8.57, 0.05, 0.95)).toBeCloseTo(liftCurveSlope(8, 0, 2 * Math.PI, 0.05, 0.95), 12);
    expect(liftCurveSlope(8, 0, 6.0, 0)).toBeLessThan(liftCurveSlope(8, 0, 2 * Math.PI, 0));
  });
});

describe('cl_max at the end of the XFOIL sweep', () => {
  const flagged = (flags: boolean[]) => ({
    ...SD7037_TEST,
    polar_summary: SD7037_TEST.polar_summary.map((r, i) => ({ ...r, cl_max_at_sweep_end: flags[i] ?? false })),
  });

  it('marks the lookup as a lower bound when a row used for it is flagged', () => {
    // Rows: 60k, 100k, 200k, 400k, ...; flag only 100k.
    const one = flagged([false, true]);
    expect(interpolatePolar(one, 80_000, 'wing').clMaxLowerBound).toBe(true);
    expect(interpolatePolar(one, 150_000, 'wing').clMaxLowerBound).toBe(true);
    expect(interpolatePolar(one, 300_000, 'wing').clMaxLowerBound).toBe(false);
    expect(interpolatePolar(one, 100_000, 'wing').clMaxLowerBound).toBe(true);
    expect(interpolatePolar(flagged([true]), 10_000, 'wing').clMaxLowerBound).toBe(true);
    expect(interpolatePolar(SD7037_TEST, 150_000, 'wing').clMaxLowerBound).toBe(false);
    expect(interpolatePolar(undefined, 150_000, 'wing').clMaxLowerBound).toBe(false);
  });
});
