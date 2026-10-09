/**
 * Unit conversions. Documents use mm, g and degrees; engine maths uses SI (m, kg, N, W, rad).
 */

export const mmToM = (mm: number): number => mm / 1000;
export const mToMm = (m: number): number => m * 1000;
export const m2ToMm2 = (m2: number): number => m2 * 1e6;
export const mm2ToM2 = (mm2: number): number => mm2 / 1e6;
export const gToKg = (g: number): number => g / 1000;
export const kgToG = (kg: number): number => kg * 1000;
export const degToRad = (deg: number): number => (deg * Math.PI) / 180;
export const radToDeg = (rad: number): number => (rad * 180) / Math.PI;
export const mpsToKmh = (mps: number): number => mps * 3.6;
export const sToMin = (s: number): number => s / 60;
/** Watt-hours to joules. */
export const whToJ = (wh: number): number => wh * 3600;
/** Joules to watt-hours. */
export const jToWh = (j: number): number => j / 3600;

/** True for a finite number. */
export function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** Clamp x into [lo, hi]. */
export function clamp(x: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, x));
}

/** Round to a number of significant digits (for fixtures and display-stable tests). */
export function roundSig(x: number, digits = 6): number {
  if (!Number.isFinite(x) || x === 0) return x;
  const p = digits - Math.ceil(Math.log10(Math.abs(x)));
  const f = 10 ** p;
  return Math.round(x * f) / f;
}
