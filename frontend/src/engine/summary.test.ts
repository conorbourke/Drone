/**
 * Prints a readable summary of estimate() for the three fixture designs when ENGINE_SUMMARY=1
 * (`ENGINE_SUMMARY=1 npx vitest run src/engine/summary.test.ts`), and always checks that the
 * headline numbers stay in broad plausibility bands for these aircraft classes.
 */

import { describe, expect, it } from 'vitest';
import { defaultInput, finalScaleInput, quadPusherInput } from './__fixtures__/designs';
import { compareLayouts, estimate } from './index';
import type { Estimates, Quantity } from './types';

const env = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env ?? {};
const PRINT = env.ENGINE_SUMMARY === '1';

function q(x: Quantity, digits = 1): string {
  const f = (v: number) => v.toFixed(digits);
  return x.low === x.high ? `${f(x.value)} ${x.unit}` : `${f(x.value)} ${x.unit} (${f(x.low)}-${f(x.high)})`;
}

function summary(name: string, e: Estimates): string {
  if (!e.valid || !e.mass || !e.aero || !e.balance || !e.performance) return `${name}: invalid\n${JSON.stringify(e.statuses, null, 2)}`;
  const m = e.mass;
  const lines = [`=== ${name} (${e.layout}) in ${e.elapsed_ms.toFixed(2)} ms ===`, 'Mass breakdown (heaviest camera):'];
  for (const c of m.components) lines.push(`  ${c.label.padEnd(46)} ${c.mass_g.toFixed(0).padStart(6)} g  x=${c.x_mm.toFixed(0).padStart(5)} mm  +/-${(c.uncertainty * 100).toFixed(0)} %`);
  lines.push(
    `  empty ${q(m.empty, 3)}, battery ${q(m.battery, 3)}, structure ${q(m.structure, 3)} (fraction ${q(m.structure_fraction, 3)})`,
    `  take-off ${q(m.takeoff_max_payload, 3)} (lightest camera ${q(m.takeoff_min_payload, 3)}); converged=${m.converged} in ${m.iterations} passes`,
    `  lift motor ${m.lift_motor_max_power_w.toFixed(0)} W max, ${m.lift_motor_mass_g.toFixed(0)} g each, max thrust ${m.lift_motor_max_thrust_n.toFixed(1)} N; pusher ${m.pusher_motor_max_power_w.toFixed(0)} W`,
    `Balance: CG ${q(e.balance.cg_max_payload_x, 0)} / ${q(e.balance.cg_min_payload_x, 0)} (max/min payload); ${q(e.balance.cg_max_payload_mac)} / ${q(e.balance.cg_min_payload_mac)}`,
    `  NP ${q(e.balance.neutral_point_x, 0)}; static margin ${q(e.balance.static_margin_max_payload)} / ${q(e.balance.static_margin_min_payload)}; hover front share ${q(e.balance.hover_front_share, 2)}`,
    `Aero: S ${q(e.aero.wing_area, 3)}, AR ${q(e.aero.aspect_ratio, 2)}, MAC ${q(e.aero.mac, 1)}, W/S ${q(e.aero.wing_loading, 2)}, Re ${e.aero.reynolds_cruise.value.toFixed(0)}`,
    `  CLa ${q(e.aero.lift_curve_slope, 2)}, CLmax ${q(e.aero.cl_max, 2)}, stall ${q(e.aero.stall_speed, 1)}, CL cruise ${q(e.aero.cl_cruise, 3)}, cruise/stall ${q(e.aero.cruise_to_stall, 2)}`,
    `  CD0 ${q(e.aero.cd0, 4)}, e ${q(e.aero.oswald, 3)}, CDi ${q(e.aero.cd_induced, 4)}, L/D ${q(e.aero.lift_to_drag, 1)}, drag ${q(e.aero.drag_cruise, 2)}`,
    `  tail Vh ${q(e.aero.tail_volume_h, 3)}, Vv ${q(e.aero.tail_volume_v, 4)}, deps/da ${q(e.aero.downwash_gradient, 3)}`,
    '  drag items: ' + e.aero.drag_items.map((d) => `${d.key} ${(d.cd0 * 1e4).toFixed(1)}`).join(', ') + ' (x1e-4)',
    `Performance: cruise ${q(e.performance.cruise_power, 0)}, hover ${q(e.performance.hover_power, 0)}, transition ${q(e.performance.transition_power, 0)}`,
    `  disc loading ${q(e.performance.hover_disc_loading, 0)}, peak ${q(e.performance.peak_current, 1)}, ${q(e.performance.battery_c_rate, 1)}`,
    `  energy ${q(e.performance.battery_energy, 0)}, usable ${q(e.performance.usable_energy, 0)}, VTOL ${q(e.performance.vtol_energy, 1)}`,
    `  ENDURANCE (wing) ${q(e.performance.endurance_cruise, 1)}, total ${q(e.performance.endurance_total, 1)}, range ${q(e.performance.range, 1)}, light camera ${q(e.performance.endurance_cruise_min_payload, 1)}`,
    'Statuses:',
    ...e.statuses.map((s) => `  [${s.level}] ${s.label}: ${s.message}`),
  );
  return lines.join('\n');
}

describe('estimate summary (plausibility bands)', () => {
  it('default 2.5 kg prototype lands in the small-QuadPlane band', () => {
    const e = estimate(defaultInput());
    if (PRINT) console.log(summary('Default prototype', e));
    expect(e.valid).toBe(true);
    const m = e.mass!.takeoff_max_payload.value;
    expect(m).toBeGreaterThan(1.8);
    expect(m).toBeLessThan(3.5);
    expect(e.aero!.stall_speed.value).toBeGreaterThan(7);
    expect(e.aero!.stall_speed.value).toBeLessThan(14);
    expect(e.performance!.hover_power.value).toBeGreaterThan(200);
    expect(e.performance!.hover_power.value).toBeLessThan(700);
    expect(e.performance!.cruise_power.value).toBeGreaterThan(50);
    expect(e.performance!.cruise_power.value).toBeLessThan(250);
  });

  it('24 kg final scale lands in the large-QuadPlane band', () => {
    const e = estimate(finalScaleInput());
    if (PRINT) console.log(summary('Final scale 24 kg', e));
    expect(e.valid).toBe(true);
    const m = e.mass!.takeoff_max_payload.value;
    expect(m).toBeGreaterThan(14);
    expect(m).toBeLessThan(32);
    expect(e.performance!.hover_power.value).toBeGreaterThan(2000);
    expect(e.performance!.hover_power.value).toBeLessThan(9000);
  });

  it('quad + pusher variant', () => {
    const e = estimate(quadPusherInput());
    if (PRINT) {
      console.log(summary('Quad + pusher', e));
      for (const c of compareLayouts(defaultInput())) {
        console.log(`${c.label}: ${c.takeoff_mass.value.toFixed(3)} kg, endurance ${c.endurance.value.toFixed(1)} min (${c.endurance.low.toFixed(1)}-${c.endurance.high.toFixed(1)}), cruise ${c.cruise_power.value.toFixed(0)} W, hover ${c.hover_power.value.toFixed(0)} W, complexity ${c.complexity.rating}`);
      }
    }
    expect(e.valid).toBe(true);
  });
});
