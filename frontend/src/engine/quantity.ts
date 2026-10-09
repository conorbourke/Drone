/**
 * Quantity builders and the uncertainty combination rules (docs/ENGINE.md "Uncertainty model").
 *
 * Rule 1 (independent relative factors on a product of powers): for y = c x1^a1 x2^a2 ..., with
 * each xi uncertain by +/-ui (relative), the relative half-range of y is sqrt(sum (ai ui)^2)
 * (first-order propagation, root-sum-square, as in the ISO GUM). The range is applied as
 * y (1 - r) .. y (1 + r).
 *
 * Rule 2 (one-at-a-time sensitivities for non-linear models): evaluate the model with each
 * factor at its low and high end (others nominal); the downward deviations and the upward
 * deviations are combined separately by root-sum-square. Used for power, endurance and range.
 */

import type { Quantity } from './types';

export interface QuantityText {
  unit: string;
  label: string;
  explain: string;
  source: string;
}

/** A Quantity with an explicit range. Non-finite inputs give NaN everywhere. */
export function qRange(value: number, low: number, high: number, text: QuantityText): Quantity {
  if (!Number.isFinite(value)) return { value: NaN, low: NaN, high: NaN, ...text };
  const lo = Number.isFinite(low) ? Math.min(low, value) : value;
  const hi = Number.isFinite(high) ? Math.max(high, value) : value;
  return { value, low: lo, high: hi, ...text };
}

/** A Quantity with a symmetric relative half-range r (0.1 = +/-10 %). */
export function qRel(value: number, r: number, text: QuantityText): Quantity {
  const d = Math.abs(value) * Math.abs(r);
  return qRange(value, value - d, value + d, text);
}

/** A Quantity with an absolute half-range. */
export function qAbs(value: number, halfRange: number, text: QuantityText): Quantity {
  return qRange(value, value - Math.abs(halfRange), value + Math.abs(halfRange), text);
}

/** An exact Quantity (geometry from the owner's own numbers): low = high = value. */
export function qExact(value: number, text: QuantityText): Quantity {
  return qRange(value, value, value, text);
}

/** Rule 1: relative half-range of a power-law product from (exponent, relative uncertainty) pairs. */
export function rssPowers(terms: [exponent: number, relUncertainty: number][]): number {
  let s = 0;
  for (const [a, u] of terms) s += (a * u) ** 2;
  return Math.sqrt(s);
}

/** Root-sum-square of a list. */
export function rss(values: number[]): number {
  let s = 0;
  for (const v of values) s += v * v;
  return Math.sqrt(s);
}

/**
 * Rule 2: given the nominal output and the outputs with each factor at its low and high end,
 * combine the deviations below and above the nominal separately by root-sum-square.
 */
export function oneAtATime(nominal: number, perturbed: [number, number][]): { low: number; high: number } {
  // Each factor contributes its largest deviation on each side of the nominal.
  const down: number[] = [];
  const up: number[] = [];
  for (const [a, b] of perturbed) {
    const da = Number.isFinite(a) ? a - nominal : 0;
    const db = Number.isFinite(b) ? b - nominal : 0;
    down.push(Math.min(0, da, db));
    up.push(Math.max(0, da, db));
  }
  return { low: nominal - rss(down), high: nominal + rss(up) };
}

/** A NaN Quantity used when an input is degenerate. */
export function qNaN(text: QuantityText): Quantity {
  return { value: NaN, low: NaN, high: NaN, ...text };
}
