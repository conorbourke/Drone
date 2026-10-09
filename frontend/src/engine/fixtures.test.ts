/**
 * Golden fixture shared/fixtures/tier1_cases.json: regenerate the geometry numbers from the
 * stored parameters and compare. Set UPDATE_FIXTURES=1 to rewrite the file after an intended
 * geometry change (then Phase 3's Python port must follow).
 */

import { describe, expect, it } from 'vitest';
import type { DesignParameters } from '../api/types';
import { buildGoldenFixture, geometryNumbers } from './__fixtures__/golden';
import { buildGeometry } from './geometry';

interface NodeFs {
  readFileSync(path: URL, encoding: 'utf8'): string;
  writeFileSync(path: URL, data: string): void;
  mkdirSync(path: URL, options: { recursive: boolean }): void;
  existsSync(path: URL): boolean;
}

const FIXTURE_URL = new URL('../../../shared/fixtures/tier1_cases.json', import.meta.url);
const env = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env ?? {};

async function loadFs(): Promise<NodeFs> {
  const name = 'node:fs';
  return (await import(/* @vite-ignore */ name)) as NodeFs;
}

/** Compare two JSON trees with a relative tolerance on numbers; returns the mismatching paths. */
function diff(expected: unknown, actual: unknown, path: string, rel: number, out: string[]): void {
  if (typeof expected === 'number' && typeof actual === 'number') {
    const tol = Math.max(rel * Math.abs(expected), 1e-6);
    if (!(Math.abs(expected - actual) <= tol)) out.push(`${path}: expected ${expected}, got ${actual}`);
    return;
  }
  if (Array.isArray(expected)) {
    if (!Array.isArray(actual) || actual.length !== expected.length) {
      out.push(`${path}: array length differs`);
      return;
    }
    expected.forEach((e, i) => diff(e, actual[i], `${path}[${i}]`, rel, out));
    return;
  }
  if (expected && typeof expected === 'object') {
    if (!actual || typeof actual !== 'object') {
      out.push(`${path}: missing object`);
      return;
    }
    for (const k of Object.keys(expected)) diff((expected as Record<string, unknown>)[k], (actual as Record<string, unknown>)[k], `${path}.${k}`, rel, out);
    return;
  }
  if (expected !== actual) out.push(`${path}: expected ${String(expected)}, got ${String(actual)}`);
}

describe('shared/fixtures/tier1_cases.json', () => {
  it('regenerates the three golden geometry cases and matches the committed file to 0.1 %', async () => {
    const fs = await loadFs();
    const generated = buildGoldenFixture();
    if (env.UPDATE_FIXTURES === '1') {
      fs.mkdirSync(new URL('.', FIXTURE_URL), { recursive: true });
      fs.writeFileSync(FIXTURE_URL, JSON.stringify(generated, null, 2) + '\n');
    }
    expect(fs.existsSync(FIXTURE_URL)).toBe(true);
    const stored = JSON.parse(fs.readFileSync(FIXTURE_URL, 'utf8')) as {
      cases: { name: string; parameters: DesignParameters; geometry: unknown }[];
    };
    expect(stored.cases.map((c) => c.name)).toEqual(['default_prototype', 'final_24kg', 'quad_pusher']);
    const problems: string[] = [];
    // 1. The stored file matches what the fixture designs generate now.
    diff(stored, JSON.parse(JSON.stringify(generated)), '$', 0.001, problems);
    // 2. Geometry rebuilt from the stored parameters reproduces the stored numbers (what Phase 3 must do).
    for (const c of stored.cases) {
      const regenerated = JSON.parse(JSON.stringify(geometryNumbers(buildGeometry(c.parameters, { render: false }))));
      diff(c.geometry, regenerated, `$.${c.name}.geometry`, 0.001, problems);
    }
    expect(problems).toEqual([]);
  });
});
