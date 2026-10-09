import { describe, expect, it } from 'vitest';
import { buildGeometry } from '../engine';
import { defaultParameters, quadPusherParameters } from '../engine/__fixtures__/designs';
import { boundsOf, convexHull, handlePositions, project, unprojectDelta, viewShapes } from './drawing';

describe('projection', () => {
  it('maps aircraft axes to each drawing and back', () => {
    expect(project('top', [100, 200, 30])).toEqual([200, 100]);
    expect(project('side', [100, 200, 30])).toEqual([100, -30]);
    expect(project('front', [100, 200, 30])).toEqual([-200, -30]);
    expect(unprojectDelta('front', 10, -5)).toEqual({ dx: 0, dy: -10, dz: 5 });
    expect(unprojectDelta('top', 10, -5)).toEqual({ dx: -5, dy: 10, dz: 0 });
  });

  it('convex hull of a square with an interior point', () => {
    const hull = convexHull([[0, 0], [1, 0], [1, 1], [0, 1], [0.5, 0.5]]);
    expect(hull).toHaveLength(4);
  });
});

describe('viewShapes', () => {
  it('top view spans the wing tip to tip and the fuselage nose to tail', () => {
    const g = buildGeometry(defaultParameters());
    const shapes = viewShapes('top', g);
    const wing = boundsOf(shapes.filter((s) => s.role === 'wing'));
    expect(wing.maxU - wing.minU).toBeCloseTo(1800, 0);
    const fus = boundsOf(shapes.filter((s) => s.role === 'fuselage'));
    expect(fus.minV).toBeCloseTo(0, 6);
    expect(fus.maxV).toBeCloseTo(900, 6);
    expect(shapes.filter((s) => s.role === 'prop')).toHaveLength(4);
    expect(shapes.some((s) => s.role === 'nosebay')).toBe(true);
  });

  it('quad + pusher draws five propellers and the pusher is a line in the top view', () => {
    const g = buildGeometry(quadPusherParameters());
    const props = viewShapes('top', g).filter((s) => s.role === 'prop');
    expect(props).toHaveLength(5);
    const pusher = boundsOf([props[4]]);
    expect(pusher.maxV - pusher.minV).toBeLessThan(1e-6);
    expect(pusher.maxU - pusher.minU).toBeCloseTo(254, 0);
  });

  it('handles sit on the geometry', () => {
    const g = buildGeometry(defaultParameters());
    const h = handlePositions(g);
    expect(h.span).toEqual([300, 900, 900 * Math.tan((3 * Math.PI) / 180)]);
    expect(h.root_chord[0]).toBeCloseTo(560);
    expect(h.boom_offset).toEqual([50, 300, 0]);
  });
});
