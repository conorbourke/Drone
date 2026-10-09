import { describe, expect, it } from 'vitest';
import type { DesignParameters, Layout } from '../api/types';
import { defaultInput, finalScaleInput } from './__fixtures__/designs';
import { estimate } from './index';
import type { EngineInput, Estimates, Quantity } from './types';

const LAYOUTS: Layout[] = ['front_tilt', 'rear_tilt', 'quad_pusher'];
const TAILS: DesignParameters['tail']['type'][] = ['conventional', 'v_tail', 'inverted_v', 'twin_boom_h'];

function isQuantity(v: unknown): v is Quantity {
  return !!v && typeof v === 'object' && 'value' in v && 'explain' in v && 'source' in v && 'low' in v;
}

/** Every Quantity in the result: finite, low <= value <= high, with unit, label, explain and source. */
function checkQuantities(e: Estimates): number {
  let n = 0;
  const walk = (o: unknown, path: string) => {
    if (isQuantity(o)) {
      n++;
      expect(Number.isFinite(o.value), `${path} finite`).toBe(true);
      expect(o.low, `${path} low`).toBeLessThanOrEqual(o.value);
      expect(o.high, `${path} high`).toBeGreaterThanOrEqual(o.value);
      expect(o.label.length, `${path} label`).toBeGreaterThan(2);
      expect(o.explain.length, `${path} explain`).toBeGreaterThan(30);
      expect(o.source.length, `${path} source`).toBeGreaterThan(5);
      expect(typeof o.unit).toBe('string');
      return;
    }
    if (o && typeof o === 'object' && !Array.isArray(o)) for (const [k, v] of Object.entries(o)) walk(v, `${path}.${k}`);
  };
  walk({ mass: e.mass, balance: e.balance, aero: e.aero, performance: e.performance }, '$');
  return n;
}

describe('estimate on every layout, tail type and scale', () => {
  for (const scale of ['prototype', 'final'] as const) {
    for (const layout of LAYOUTS) {
      for (const tail of TAILS) {
        it(`${scale} ${layout} ${tail}`, () => {
          const base = scale === 'final' ? finalScaleInput() : defaultInput();
          const input: EngineInput = { ...base, parameters: { ...base.parameters, layout, tail: { ...base.parameters.tail, type: tail } } };
          const e = estimate(input);
          expect(e.valid).toBe(true);
          expect(e.layout).toBe(layout);
          expect(e.mass!.converged).toBe(true);
          expect(checkQuantities(e)).toBeGreaterThan(40);
          for (const s of e.statuses) {
            expect(['ok', 'warn', 'fail', 'info']).toContain(s.level);
            expect(s.message.length).toBeGreaterThan(15);
          }
          expect(e.statuses.some((s) => s.key === 'check.mtow')).toBe(true);
          expect(e.statuses.some((s) => s.key === 'check.battery_c_rate')).toBe(true);
        });
      }
    }
  }
});

describe('statuses and assumptions', () => {
  it('checks come before notes and include the contract checks', () => {
    const e = estimate(defaultInput());
    const keys = e.statuses.map((s) => s.key);
    for (const k of ['check.mtow', 'check.static_margin_max_payload', 'check.static_margin_min_payload', 'check.cruise_to_stall', 'check.endurance', 'check.battery_c_rate', 'check.hover_thrust_to_weight']) {
      expect(keys).toContain(k);
    }
    const order = { fail: 0, warn: 1, ok: 2, info: 3 };
    for (let i = 1; i < e.statuses.length; i++) expect(order[e.statuses[i].level]).toBeGreaterThanOrEqual(order[e.statuses[i - 1].level]);
    expect(e.assumptions.length).toBeGreaterThan(3);
  });

  it('flags a design over the legal mass limit', () => {
    const i = finalScaleInput();
    i.mission = { ...i.mission, payload_max_g: 9000 };
    const e = estimate(i);
    expect(e.statuses.find((s) => s.key === 'check.mtow')?.level).toBe('fail');
  });

  it('warns when airfoil data are missing and still produces numbers', () => {
    const e = estimate({ ...defaultInput(), airfoils: {} });
    expect(e.valid).toBe(true);
    expect(e.statuses.some((s) => s.key === 'airfoil.wing_generic' && s.level === 'warn')).toBe(true);
  });

  it('accepts a schema v1 document (v2 fields filled with defaults)', () => {
    const i = defaultInput();
    const p: DesignParameters = { ...i.parameters, schema_version: 1, propulsion: undefined, battery: undefined, allowances: undefined };
    delete p.wing.twist_deg;
    const e = estimate({ ...i, parameters: p });
    expect(e.valid).toBe(true);
    expect(e.performance!.battery_energy.value).toBeCloseTo(111, 9);
  });
});

describe('degenerate input never throws', () => {
  const cases: [string, (i: EngineInput) => EngineInput][] = [
    ['zero span', (i) => ({ ...i, parameters: { ...i.parameters, wing: { ...i.parameters.wing, span_mm: 0 } } })],
    ['NaN chord', (i) => ({ ...i, parameters: { ...i.parameters, wing: { ...i.parameters.wing, root_chord_mm: Number.NaN } } })],
    ['negative cruise speed', (i) => ({ ...i, mission: { ...i.mission, cruise_speed_mps: -3 } })],
    ['zero battery capacity', (i) => ({ ...i, parameters: { ...i.parameters, battery: { ...i.parameters.battery!, capacity_mah: 0 } } })],
    ['payload range reversed', (i) => ({ ...i, mission: { ...i.mission, payload_min_g: 500, payload_max_g: 100 } })],
    ['rear motor ahead of front', (i) => ({ ...i, parameters: { ...i.parameters, motors: { ...i.parameters.motors, rear_x_mm: 10 } } })],
    ['fuselage wider than span', (i) => ({ ...i, parameters: { ...i.parameters, fuselage: { ...i.parameters.fuselage, width_mm: 5000 } } })],
    ['missing wing block', (i) => ({ ...i, parameters: { ...i.parameters, wing: undefined as unknown as DesignParameters['wing'] } })],
    ['no settings', (i) => ({ ...i, settings: undefined as unknown as EngineInput['settings'] })],
    ['vertical V tail', (i) => ({ ...i, parameters: { ...i.parameters, tail: { ...i.parameters.tail, v_angle_deg: 90 } } })],
    ['null input', () => null as unknown as EngineInput],
  ];
  for (const [name, make] of cases) {
    it(name, () => {
      let e: Estimates | undefined;
      expect(() => {
        e = estimate(make(defaultInput()));
      }).not.toThrow();
      expect(e!.valid).toBe(false);
      expect(e!.statuses.some((s) => s.level === 'fail' && s.message.length > 15)).toBe(true);
    });
  }

  it('extreme but valid values still produce statuses rather than errors', () => {
    const i = defaultInput();
    i.parameters.propulsion = { ...i.parameters.propulsion!, prop_diameter_mm: 40 };
    i.parameters.battery = { ...i.parameters.battery!, x_mm: 2000 };
    const e = estimate(i);
    expect(e.valid).toBe(true);
    expect(e.statuses.some((s) => s.level === 'warn' || s.level === 'fail')).toBe(true);
  });
});

describe('performance budget', () => {
  it('estimate() runs in under 5 ms for one design', () => {
    const input = defaultInput();
    for (let i = 0; i < 20; i++) estimate(input); // warm up the JIT
    const n = 200;
    const t0 = performance.now();
    for (let i = 0; i < n; i++) estimate(input);
    const perCall = (performance.now() - t0) / n;
    console.log(`estimate(): ${perCall.toFixed(3)} ms per call (mean of ${n})`);
    expect(perCall).toBeLessThan(5);
    const finalInput = finalScaleInput();
    const t1 = performance.now();
    for (let i = 0; i < n; i++) estimate(finalInput);
    expect((performance.now() - t1) / n).toBeLessThan(5);
  });
});

describe('cl_max that is only a lower bound (XFOIL sweep ended before the stall)', () => {
  it('keeps the stall speed but widens its range downwards and says why', () => {
    const base = defaultInput();
    const plain = estimate(base);
    const wingId = base.parameters.wing.airfoil;
    const summary = base.airfoils[wingId]!;
    const flaggedInput: EngineInput = {
      ...base,
      airfoils: {
        ...base.airfoils,
        [wingId]: { ...summary, polar_summary: summary.polar_summary.map((r) => ({ ...r, cl_max_at_sweep_end: true })) },
      },
    };
    const flagged = estimate(flaggedInput);
    const a = plain.aero!.stall_speed;
    const b = flagged.aero!.stall_speed;
    expect(b.value).toBeCloseTo(a.value, 12);
    expect(b.high).toBeCloseTo(a.high, 12);
    expect(b.low).toBeLessThan(a.low);
    expect(flagged.aero!.cl_max.high).toBeGreaterThan(plain.aero!.cl_max.high);
    expect(flagged.aero!.cruise_to_stall.high).toBeGreaterThan(plain.aero!.cruise_to_stall.high);
    expect(b.source).toMatch(/lower bound/);
    expect(flagged.assumptions.some((s) => s.includes('lower bound'))).toBe(true);
    expect(plain.assumptions.some((s) => s.includes('lower bound'))).toBe(false);
  });
});
