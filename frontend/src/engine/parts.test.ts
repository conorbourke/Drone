/**
 * Phase 4: selected catalogue parts' masses (the draft's parts_selection.masses_g) replace the
 * statistical motor, propeller, ESC, tilt servo, battery, avionics and tube masses in the Tier 1
 * mass model (docs/phases/PHASE4.md section 6), mirroring the server's app/engine/mass.py.
 */
import { describe, expect, it } from 'vitest';
import { defaultInput, quadPusherInput } from './__fixtures__/designs';
import { PART_MASS_UNCERTAINTY, TILT_HINGE_HARDWARE_G } from './constants';
import { estimate } from './estimate';
import { partMass } from './mass';
import type { MassComponent, PartsMasses } from './types';

/** The masses the backend stores for the default prototype's recommended list (9 Oct 2026 seed). */
const PROTOTYPE_PARTS: PartsMasses = {
  lift_motor_each: 135,
  lift_prop_each: 37.1,
  esc_each: 73,
  tilt_servo_each: 64,
  battery: 907.2,
  spar_tube_per_m: 55,
  boom_tube_per_m: 74.2,
  avionics: 126.3,
};

const byKey = (components: MassComponent[], key: string) => {
  const c = components.find((x) => x.key === key);
  if (!c) throw new Error(`no component ${key}`);
  return c;
};

describe('partMass', () => {
  it('returns finite positive masses only', () => {
    expect(partMass({ esc_each: 73 }, 'esc_each')).toBe(73);
    expect(partMass({ esc_each: 0 }, 'esc_each')).toBeNull();
    expect(partMass({ esc_each: Number.NaN }, 'esc_each')).toBeNull();
    expect(partMass({}, 'esc_each')).toBeNull();
    expect(partMass(null, 'esc_each')).toBeNull();
  });
});

describe('Tier 1 mass with selected parts', () => {
  it('without parts nothing changes and no component is marked', () => {
    const plain = estimate(defaultInput());
    const empty = estimate({ ...defaultInput(), partsMasses: {} });
    expect(plain.mass?.parts_used).toEqual([]);
    expect(empty.mass?.takeoff_max_payload.value).toBeCloseTo(plain.mass!.takeoff_max_payload.value, 12);
    expect(plain.mass?.components.some((c) => c.from_parts)).toBe(false);
  });

  it('every stored key replaces its statistical mass', () => {
    const input = defaultInput();
    const e = estimate({ ...input, partsMasses: PROTOTYPE_PARTS });
    expect(e.valid).toBe(true);
    const comps = e.mass!.components;
    const span = input.parameters.wing.span_mm / 1000;
    const booms = input.parameters.booms;
    expect(byKey(comps, 'motors_front').mass_g).toBeCloseTo(2 * 135, 9);
    expect(byKey(comps, 'motors_rear').mass_g).toBeCloseTo(2 * 135, 9);
    expect(byKey(comps, 'mounts_front').mass_g).toBeCloseTo(2 * 0.2 * 135, 9);
    expect(byKey(comps, 'props_front').mass_g).toBeCloseTo(2 * 37.1, 9);
    expect(byKey(comps, 'escs_rear').mass_g).toBeCloseTo(2 * 73, 9);
    expect(byKey(comps, 'tilt_mechanism').mass_g).toBeCloseTo(2 * (64 + TILT_HINGE_HARDWARE_G), 9);
    expect(byKey(comps, 'battery').mass_g).toBeCloseTo(907.2, 9);
    expect(byKey(comps, 'avionics').mass_g).toBeCloseTo(126.3, 9);
    expect(byKey(comps, 'wing_spar').mass_g).toBeCloseTo(55 * span, 9);
    expect(byKey(comps, 'booms').mass_g).toBeCloseTo(Math.round(booms.count) * 74.2 * (booms.length_mm / 1000), 9);
    for (const key of ['motors_front', 'props_rear', 'escs_front', 'battery', 'avionics', 'wing_spar', 'booms']) {
      expect(byKey(comps, key).from_parts).toBe(true);
      expect(byKey(comps, key).uncertainty).toBeLessThanOrEqual(0.15);
      expect(byKey(comps, key).source).toMatch(/Selected catalogue part/);
    }
    expect(byKey(comps, 'motors_front').uncertainty).toBe(PART_MASS_UNCERTAINTY);
    // Allowances that stay statistical.
    expect(byKey(comps, 'servos_wing').from_parts).toBeUndefined();
    expect(byKey(comps, 'wiring').from_parts).toBeUndefined();
    expect(e.mass!.parts_used.length).toBe(11);
    expect(e.assumptions.join(' ')).toMatch(/Parts tab replace the statistical ones/);
  });

  it('heavier parts raise the take-off mass and lower the endurance; the build-up still converges', () => {
    const base = estimate({ ...defaultInput(), partsMasses: PROTOTYPE_PARTS });
    const heavier = estimate({ ...defaultInput(), partsMasses: { ...PROTOTYPE_PARTS, esc_each: 73 + 50 } });
    const dm = (heavier.mass!.takeoff_max_payload.value - base.mass!.takeoff_max_payload.value) * 1000;
    // Four ESCs, 50 g each, plus the wiring fraction, landing gear and servos that scale with mass.
    expect(dm).toBeGreaterThan(200);
    expect(dm).toBeLessThan(240);
    expect(heavier.mass!.converged).toBe(true);
    expect(heavier.performance!.endurance_cruise.value).toBeLessThan(base.performance!.endurance_cruise.value);
  });

  it('only the keys present are replaced', () => {
    const plain = estimate(defaultInput());
    const e = estimate({ ...defaultInput(), partsMasses: { avionics: 150 } });
    expect(byKey(e.mass!.components, 'avionics').mass_g).toBe(150);
    expect(byKey(e.mass!.components, 'motors_front').from_parts).toBeUndefined();
    expect(e.mass!.parts_used).toEqual(['Avionics (selected parts)']);
    // The 220 g allowance became 150 g: the aircraft is lighter.
    expect(e.mass!.takeoff_max_payload.value).toBeLessThan(plain.mass!.takeoff_max_payload.value);
  });

  it('quad + pusher: the cruise motor and pusher propeller, and the ESC for the pusher', () => {
    const e = estimate({ ...quadPusherInput(), partsMasses: { cruise_motor: 160, pusher_prop: 30, esc_each: 40, tilt_servo_each: 64 } });
    const comps = e.mass!.components;
    expect(byKey(comps, 'pusher_motor').mass_g).toBe(160);
    expect(byKey(comps, 'pusher_mount').mass_g).toBeCloseTo(32, 9);
    expect(byKey(comps, 'pusher_prop').mass_g).toBe(30);
    expect(byKey(comps, 'pusher_esc').mass_g).toBe(40);
    // No tilt mechanism on a quad + pusher, so the servo mass is ignored.
    expect(comps.some((c) => c.key === 'tilt_mechanism')).toBe(false);
  });
});
