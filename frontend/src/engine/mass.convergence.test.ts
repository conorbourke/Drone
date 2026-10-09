import { describe, expect, it, vi } from 'vitest';

// One pass is never enough to settle, so the solver reports that it did not converge.
vi.mock('./constants', async (importOriginal) => ({ ...(await importOriginal<typeof import('./constants')>()), MASS_MAX_ITERATIONS: 1 }));

const { defaultInput } = await import('./__fixtures__/designs');
const { SEA_LEVEL } = await import('./atmosphere');
const { buildGeometry, withDefaults } = await import('./geometry');
const { solveMass } = await import('./mass');

describe('mass fixed point that does not converge', () => {
  it('does not claim convergence in the take-off mass source text', () => {
    const input = defaultInput();
    const p = withDefaults(input.parameters);
    const g = buildGeometry(input.parameters, { render: false });
    const s = solveMass({ p, g, mission: { ...input.mission, target_takeoff_mass_kg: 20 }, settings: input.settings, atm: SEA_LEVEL });
    expect(s.result.converged).toBe(false);
    const source = s.result.takeoff_max_payload.source;
    expect(source).not.toMatch(/iterated to convergence/);
    expect(source).toMatch(/did NOT converge in 1 pass \(/);
  });
});
