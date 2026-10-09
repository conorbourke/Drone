import { describe, expect, it } from 'vitest';
import { defaultInput, finalScaleInput } from './__fixtures__/designs';
import { setAtPath } from '../lib/draft';
import type { DesignParameters } from '../api/types';
import { compareLayouts } from './compare';
import { estimate } from './estimate';

describe('compareLayouts', () => {
  it('returns the three layouts, with the pusher variant heavier and the tilt variants flagged', () => {
    const c = compareLayouts(defaultInput());
    expect(c.map((x) => x.layout)).toEqual(['front_tilt', 'rear_tilt', 'quad_pusher']);
    const [front, rear, pusher] = c;
    expect(pusher.takeoff_mass.value).toBeGreaterThan(front.takeoff_mass.value);
    expect(pusher.takeoff_mass.value).toBeGreaterThan(rear.takeoff_mass.value);
    expect(front.has_tilt_mechanism).toBe(true);
    expect(rear.has_tilt_mechanism).toBe(true);
    expect(pusher.has_tilt_mechanism).toBe(false);
    expect(front.complexity.reasons.join(' ')).toMatch(/[Tt]ilt mechanism/);
    expect(pusher.complexity.rating).toBe('low');
    expect(rear.ardupilot_note).toMatch(/Less common in ArduPilot than front tilt/);
    for (const x of c) {
      expect(Number.isFinite(x.endurance.value)).toBe(true);
      expect(x.cruise_power.value).toBeGreaterThan(0);
      expect(x.hover_power.value).toBeGreaterThan(0);
      expect(x.ardupilot_note.length).toBeGreaterThan(20);
    }
    // The pusher layout stops all four lift propellers, so it cruises with more drag.
    expect(pusher.cruise_power.value).toBeGreaterThan(front.cruise_power.value);
  });

  it('re-balances each layout to the current balance point and moves the tilt axis for rear tilt', () => {
    const c = compareLayouts(defaultInput());
    expect(c[0].adjustments).toEqual([]);
    expect(c[1].adjustments.join(' ')).toMatch(/Tilt axis moved to the rear motors/);
    expect(c[2].adjustments.join(' ')).toMatch(/Battery moved/);
    expect(c[2].problems.some((s) => s.key === 'check.static_margin_max_payload')).toBe(false);
  });

  it('"Use this layout" changes give exactly the design the card describes', () => {
    const input = defaultInput();
    const c = compareLayouts(input);
    expect(c[0].changes).toEqual([['layout', 'front_tilt']]);
    for (const card of c.slice(1)) {
      expect(card.changes[0]).toEqual(['layout', card.layout]);
      const applied = card.changes.reduce<DesignParameters>((p, [path, value]) => setAtPath(p, path, value), input.parameters);
      const e = estimate({ ...input, parameters: applied });
      expect(e.mass!.takeoff_max_payload.value).toBeCloseTo(card.takeoff_mass.value, 9);
      expect(e.performance!.endurance_cruise.value).toBeCloseTo(card.endurance.value, 9);
      expect(e.statuses.filter((s) => s.level === 'fail' || s.level === 'warn').map((s) => s.key)).toEqual(card.problems.map((s) => s.key));
    }
    // Quad + pusher: the battery move is part of the change, and it keeps the design stable.
    const pusher = c[2];
    const battery = pusher.changes.find(([path]) => path === 'battery.x_mm');
    expect(battery?.[1]).toBe(pusher.battery_x_mm);
    const applied = pusher.changes.reduce<DesignParameters>((p, [path, value]) => setAtPath(p, path, value), input.parameters);
    expect(estimate({ ...input, parameters: applied }).balance!.static_margin_max_payload.value).toBeGreaterThan(0);
    // Rear tilt carries its tilt-axis move.
    expect(c[1].changes.some(([path]) => path === 'tilt.axis_x_mm')).toBe(true);
  });

  it('works at the 24 kg final scale', () => {
    const c = compareLayouts(finalScaleInput());
    expect(c).toHaveLength(3);
    expect(c[2].takeoff_mass.value).toBeGreaterThan(c[0].takeoff_mass.value);
  });
});
