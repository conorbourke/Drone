import { describe, expect, it } from 'vitest';
import { ApiError } from '../api/client';
import type { PartsListRole } from '../api/partsList';
import {
  budgetGeometry,
  deltaTexts,
  flagInfo,
  formatSignedEur,
  groupBySystem,
  linkState,
  partsMassNames,
  quantityText,
  refreshActive,
  refreshErrorMessage,
  relativeTime,
} from './partsList';

const role = (r: string, system: PartsListRole['system']) => ({ role: r, system }) as PartsListRole;

describe('groupBySystem', () => {
  it('keeps the fixed system order and drops empty systems', () => {
    const groups = groupBySystem([role('spar_tube', 'structure'), role('lift_motor', 'propulsion'), role('gps', 'flight_control'), role('esc', 'propulsion')]);
    expect(groups.map((g) => g.system)).toEqual(['propulsion', 'flight_control', 'structure']);
    expect(groups[0].roles.map((r) => r.role)).toEqual(['lift_motor', 'esc']);
  });
});

describe('linkState', () => {
  it('broken beats stale beats working; null is not checked', () => {
    expect(linkState({ url_ok: false, stale: true, url_status: 404 }).key).toBe('broken');
    expect(linkState({ url_ok: false, stale: false, url_status: 404 }).explain).toContain('HTTP 404');
    expect(linkState({ url_ok: true, stale: true, url_status: 200 }).key).toBe('stale');
    expect(linkState({ url_ok: true, stale: false, url_status: 200 }).key).toBe('working');
    expect(linkState({ url_ok: null, stale: false, url_status: null }).key).toBe('unchecked');
  });
});

describe('relativeTime', () => {
  const now = new Date('2026-10-09T12:00:00Z');
  it('reads naturally', () => {
    expect(relativeTime('2026-10-09T11:59:40Z', now)).toBe('just now');
    expect(relativeTime('2026-10-09T11:15:00Z', now)).toBe('45 min ago');
    expect(relativeTime('2026-10-09T00:00:00Z', now)).toBe('12 h ago');
    expect(relativeTime('2026-10-08T06:00:00Z', now)).toBe('yesterday');
    expect(relativeTime('2026-10-01T12:00:00Z', now)).toBe('8 days ago');
    expect(relativeTime('2026-07-01T12:00:00Z', now)).toBe('3 months ago');
    expect(relativeTime(null, now)).toBe('never');
  });
});

describe('refresh helpers', () => {
  it('429 says when to try again, in whole minutes', () => {
    const err = new ApiError(429, 'This part was refreshed less than an hour ago.', [], true, { detail: 'x', retry_after_s: 3540 });
    expect(refreshErrorMessage(err)).toBe('Checked within the last hour; try again in 59 minutes.');
    const soon = new ApiError(429, 'x', [], true, { detail: 'x', retry_after_s: 20 });
    expect(refreshErrorMessage(soon)).toBe('Checked within the last hour; try again in 1 minute.');
  });
  it('503 passes the server message on', () => {
    const msg = 'Add the ANTHROPIC_API_KEY secret in GitHub and redeploy to enable supplier lookups';
    expect(refreshErrorMessage(new ApiError(503, msg))).toBe(msg);
  });
  it('queued and running keep polling', () => {
    expect(refreshActive('queued')).toBe(true);
    expect(refreshActive('running')).toBe(true);
    expect(refreshActive('done')).toBe(false);
    expect(refreshActive(null)).toBe(false);
  });
});

describe('budgetGeometry', () => {
  it('the budget is the full track until the cost exceeds it', () => {
    expect(budgetGeometry({ cost_eur: 1750, budget_eur: 5000 })).toEqual({ fillPct: 35, budgetPct: 100, scaleMax: 5000 });
    const over = budgetGeometry({ cost_eur: 6250, budget_eur: 5000 });
    expect(over.fillPct).toBe(100);
    expect(over.budgetPct).toBe(80);
  });
});

describe('formatting', () => {
  it('signed euro and deltas', () => {
    expect(formatSignedEur(41.76)).toBe('+€41.76');
    expect(formatSignedEur(-241.16)).toBe('−€241.16');
    expect(deltaTexts({ mass_g: 120, price_eur: -241.16, hover_g_per_w: 8.1358, max_thrust_g: 3293.6 })).toEqual([
      'mass +120 g',
      'price −€241.16',
      'hover 8.1 g/W',
      'full thrust 3,294 g',
    ]);
    expect(deltaTexts({ margin: 0.3335 })).toEqual(['strength margin +0.33']);
  });
  it('flag chips have plain labels, unknown codes are readable', () => {
    expect(flagInfo('no_listing').label).toBe('No Irish or UK listing');
    expect(flagInfo('build').label).toBe('Needs building');
    expect(flagInfo('something_new').label).toBe('Something new');
  });
  it('parts mass names and quantities', () => {
    expect(partsMassNames({ esc_each: 73, battery: 907.2, avionics: 0 })).toEqual(['ESCs', 'battery']);
    expect(partsMassNames(null)).toEqual([]);
    expect(quantityText({ role: 'battery', quantity: 12, custom_pack: { cells_series: 6, cells_parallel: 2, cell: 'x', overhead_fraction: 0.08 } })).toBe('12 cells (6S2P)');
    expect(quantityText({ role: 'boom_tube', quantity: 2, custom_pack: null })).toBe('2 x 1 m');
    expect(quantityText({ role: 'esc', quantity: 4, custom_pack: null })).toBe('4');
  });
});
