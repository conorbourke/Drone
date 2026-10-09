import { describe, expect, it } from 'vitest';
import { defaultInput } from './__fixtures__/designs';
import { SEA_LEVEL } from './atmosphere';
import { estimate } from './estimate';
import { buildGeometry, withDefaults } from './geometry';
import {
  carbonTubeMassPerM,
  centreOfGravity,
  escMassG,
  frontThrustShare,
  massSigma,
  motorMassG,
  packElectrics,
  propMassG,
  solveMass,
} from './mass';
import type { MassComponent } from './types';

const comp = (key: string, mass_g: number, x_mm: number, group: MassComponent['group'] = 'systems', uncertainty = 0): MassComponent => ({
  key, label: key, group, mass_g, x_mm, uncertainty, source: 'test', explain: 'test',
});

describe('statistical relations', () => {
  it('carbon tube 20 mm: 1550 x pi x 0.019 x 0.001 = 92.52 g/m; 8 mm: 34.1 g/m', () => {
    expect(carbonTubeMassPerM(20)).toBeCloseTo(92.5199, 3);
    expect(carbonTubeMassPerM(8)).toBeCloseTo(1550 * Math.PI * 0.007 * 0.001 * 1000, 6);
  });
  it('motor, ESC and propeller relations', () => {
    expect(motorMassG(400)).toBeCloseTo(1.2 * 400 ** 0.73, 9);
    expect(escMassG(40)).toBe(45);
    expect(propMassG(304.8, 2)).toBeCloseTo(20, 9);
    expect(propMassG(304.8, 3)).toBeCloseTo(30, 9);
  });
  it('battery: 6S1P 5000 mAh LiPo = 111 Wh, 765.5 g at 145 Wh/kg', () => {
    const pack = packElectrics(withDefaults(defaultInput().parameters));
    expect(pack.voltage).toBeCloseTo(22.2, 12);
    expect(pack.energyWh).toBeCloseTo(111, 12);
    expect(pack.massG).toBeCloseTo(765.517, 2);
  });
});

describe('centre of gravity arithmetic', () => {
  it('mass-weighted mean: 1 kg at 100 mm and 3 kg at 500 mm -> 400 mm', () => {
    const c = centreOfGravity([comp('a', 1000, 100), comp('b', 3000, 500)]);
    expect(c.total).toBe(4000);
    expect(c.x).toBeCloseTo(400, 12);
  });
  it('uncertainty: structure items add linearly, others root-sum-square', () => {
    const list = [comp('s1', 100, 0, 'structure', 0.3), comp('s2', 100, 0, 'structure', 0.3), comp('o1', 300, 0, 'systems', 0.1), comp('o2', 400, 0, 'systems', 0.1)];
    expect(massSigma(list)).toBeCloseTo(Math.sqrt(60 ** 2 + 30 ** 2 + 40 ** 2), 10);
  });
  it('hover thrust share from the moment balance', () => {
    expect(frontThrustShare(400, 90, 710)).toBeCloseTo(310 / 620, 12);
    expect(frontThrustShare(90, 90, 710)).toBe(1);
  });
});

describe('mass fixed point', () => {
  it('converges within 20 passes and is a fixed point of the build-up', () => {
    const input = defaultInput();
    const p = withDefaults(input.parameters);
    const g = buildGeometry(input.parameters, { render: false });
    const s = solveMass({ p, g, mission: input.mission, settings: input.settings, atm: SEA_LEVEL });
    expect(s.result.converged).toBe(true);
    expect(s.result.iterations).toBeLessThanOrEqual(20);
    // Starting from a very different guess lands on the same mass.
    const s2 = solveMass({ p, g, mission: { ...input.mission, target_takeoff_mass_kg: 10 }, settings: input.settings, atm: SEA_LEVEL });
    expect(s2.totalMaxG).toBeCloseTo(s.totalMaxG, 0);
    // The sum of components equals the reported take-off mass.
    const sum = s.result.components.reduce((a, c) => a + c.mass_g, 0);
    expect(s.result.takeoff_max_payload.value * 1000).toBeCloseTo(sum, 6);
    expect(s.totalMaxG - s.totalMinG).toBeCloseTo(input.mission.payload_max_g - input.mission.payload_min_g, 6);
    // Wiring is f / (1 - f) of the rest of the empty mass, i.e. f of the empty mass.
    const wiring = s.result.components.find((c) => c.key === 'wiring')!.mass_g;
    expect(wiring / (s.result.empty.value * 1000)).toBeCloseTo(0.06, 6);
  });

  it('every component has a source, explanation and position', () => {
    const e = estimate(defaultInput());
    for (const c of e.mass!.components) {
      expect(c.source.length).toBeGreaterThan(10);
      expect(c.explain.length).toBeGreaterThan(10);
      expect(Number.isFinite(c.x_mm)).toBe(true);
    }
  });
});

describe('balance', () => {
  it('moving the battery forward moves the CG forward and increases the static margin', () => {
    const base = defaultInput();
    const fwd = defaultInput();
    fwd.parameters.battery = { ...fwd.parameters.battery!, x_mm: fwd.parameters.battery!.x_mm - 80 };
    const a = estimate(base);
    const b = estimate(fwd);
    expect(b.balance!.cg_max_payload_x.value).toBeLessThan(a.balance!.cg_max_payload_x.value);
    expect(b.balance!.static_margin_max_payload.value).toBeGreaterThan(a.balance!.static_margin_max_payload.value);
    expect(b.balance!.static_margin_min_payload.value).toBeGreaterThan(a.balance!.static_margin_min_payload.value);
    // The neutral point does not depend on the battery position.
    expect(b.balance!.neutral_point_x.value).toBeCloseTo(a.balance!.neutral_point_x.value, 9);
  });

  it('a heavier camera in the nose moves the CG forward (more static margin)', () => {
    const e = estimate(defaultInput());
    expect(e.balance!.cg_max_payload_x.value).toBeLessThan(e.balance!.cg_min_payload_x.value);
    expect(e.balance!.static_margin_max_payload.value).toBeGreaterThan(e.balance!.static_margin_min_payload.value);
  });

  it('a bigger tail moves the neutral point aft', () => {
    const big = defaultInput();
    big.parameters.tail = { ...big.parameters.tail, chord_mm: 180 };
    expect(estimate(big).balance!.neutral_point_x.value).toBeGreaterThan(estimate(defaultInput()).balance!.neutral_point_x.value);
  });
});
