import { describe, expect, it } from 'vitest';
import { defaultInput, quadPusherInput } from './__fixtures__/designs';
import { estimate } from './estimate';
import { genericPropeller, propCp, propCt, propOperatingPoint } from './propeller';

/*
 * Reference numbers from the server engine (Phase 3, backend/app/engine/), computed with
 *
 *   cd backend && uv run python -c "...run_analysis(params, DEFAULT_MISSION, DEFAULT_SETTINGS, mode='fast')..."
 *
 * on app.defaults.DEFAULT_DESIGN_PARAMETERS (battery.x_mm 290, the new default) with layout
 * front_tilt and quad_pusher, on 2026-10-09. Values from result.summary and
 * result.propulsion.cruise (the heaviest-camera cruise operating point).
 */
const SERVER = {
  front_tilt: {
    cruisePowerW: 204.103,
    enduranceMin: 20.739,
    staticMarginMax: 16.676,
    staticMarginMin: 7.513,
    dragN: 3.34,
    thrustPerPropN: 1.670025,
    j: 0.478513,
    etaProp: 0.326576,
  },
  quad_pusher: {
    cruisePowerW: 110.521,
    enduranceMin: 38.576,
    staticMarginMax: 1.461,
    staticMarginMin: -9.057,
    dragN: 3.788,
    thrustPerPropN: 3.788032,
    j: 0.556795,
    etaProp: 0.713308,
  },
};

describe('generic propeller (port of backend/app/engine/propulsion.py)', () => {
  it('reproduces the server static coefficients for 330 x 140 mm and the 254 mm pusher (P/D 0.7)', () => {
    // Server propulsion.lift_propeller: ct0 0.103636, cp0 0.041061, J_T 0.516667.
    const lift = genericPropeller(330, 140, 2);
    expect(lift.ct0).toBeCloseTo(0.1036364, 6);
    expect(lift.cp0).toBeCloseTo(0.0410606, 6);
    expect(lift.jZeroThrust).toBeCloseTo(0.5166667, 6);
    // Server propulsion.pusher_propeller: ct0 0.135, cp0 0.0669, J_T 0.82.
    const pusher = genericPropeller(254, 0.7 * 254, 2);
    expect(pusher.ct0).toBeCloseTo(0.135, 9);
    expect(pusher.cp0).toBeCloseTo(0.0669, 9);
    expect(pusher.jZeroThrust).toBeCloseTo(0.82, 9);
  });

  it('thrust falls to zero at J_T; power never below 5 % of static', () => {
    const p = genericPropeller(330, 140, 2);
    expect(propCt(p, p.jZeroThrust)).toBeCloseTo(0, 12);
    expect(propCp(p, 5)).toBeCloseTo(0.05 * p.cp0, 12);
  });

  it('solves the same cruise operating point as the server at the server thrust (J and efficiency)', () => {
    const lift = propOperatingPoint(genericPropeller(330, 140, 2), SERVER.front_tilt.thrustPerPropN, 16, 1.225);
    expect(lift.j).toBeCloseTo(SERVER.front_tilt.j, 4);
    expect(lift.efficiency).toBeCloseTo(SERVER.front_tilt.etaProp, 4);
    const pusher = propOperatingPoint(genericPropeller(254, 177.8, 2), SERVER.quad_pusher.thrustPerPropN, 16, 1.225);
    expect(pusher.j).toBeCloseTo(SERVER.quad_pusher.j, 4);
    expect(pusher.efficiency).toBeCloseTo(SERVER.quad_pusher.etaProp, 4);
  });
});

describe('Tier 1 against the server fast-mode analysis (reference numbers above)', () => {
  const rel = (a: number, b: number) => Math.abs(a - b) / b;

  it('default design (front tilt): cruise power within 10 %, both static margins positive', () => {
    const e = estimate(defaultInput());
    const perf = e.performance!;
    expect(rel(perf.cruise_power.value, SERVER.front_tilt.cruisePowerW)).toBeLessThan(0.1);
    // Tilted hover propellers run near their zero-thrust advance ratio: efficiency ~0.3, not 0.65.
    expect(perf.cruise_propeller_efficiency.value).toBeGreaterThan(0.25);
    expect(perf.cruise_propeller_efficiency.value).toBeLessThan(0.45);
    expect(rel(perf.endurance_cruise.value, SERVER.front_tilt.enduranceMin)).toBeLessThan(0.15);
    expect(e.balance!.static_margin_max_payload.value).toBeGreaterThan(0);
    expect(e.balance!.static_margin_min_payload.value).toBeGreaterThan(0);
    expect(SERVER.front_tilt.staticMarginMin).toBeGreaterThan(0);
  });

  it('quad + pusher: the propeller path matches; the residual is Tier 1 drag', () => {
    const e = estimate(quadPusherInput());
    const perf = e.performance!;
    // Tier 1's CD0 (flat-plate wing and tail friction x form factor) is ~11 % above the server's
    // XFOIL strip-integrated profile drag, so Tier 1 cruise drag is ~13 % higher; near the
    // pusher's peak efficiency that passes straight into power (+15 %). The propeller part alone
    // agrees: at the server's own thrust the efficiency is identical (test above), and Tier 1
    // power with the server drag is within 5 % (motor 0.85 fixed vs the server's 0.87).
    expect(rel(perf.cruise_power.value, SERVER.quad_pusher.cruisePowerW)).toBeLessThan(0.17);
    expect(perf.cruise_power.value).toBeGreaterThan(SERVER.quad_pusher.cruisePowerW); // no longer optimistic
    expect(rel(e.aero!.drag_cruise.value, SERVER.quad_pusher.dragN)).toBeLessThan(0.15);
    const withServerDrag = (SERVER.quad_pusher.dragN * 16) / (SERVER.quad_pusher.etaProp * 0.85 * 0.95) + 8;
    expect(rel(withServerDrag, SERVER.quad_pusher.cruisePowerW)).toBeLessThan(0.05);
    expect(perf.cruise_propeller_efficiency.value).toBeCloseTo(SERVER.quad_pusher.etaProp, 1);
  });
});
