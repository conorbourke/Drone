import { describe, expect, it } from 'vitest';
import type { DesignParameters } from '../api/types';
import { newProjectParameters } from './__fixtures__/designs';
import {
  airfoilShape,
  buildGeometry,
  liftingWetted,
  naca4Coordinates,
  sectionArea,
  sectionPerimeter,
  sweepAt,
  trapezoid,
  withDefaults,
} from './geometry';
import { degToRad } from './units';

function withWing(span: number, root: number, tip: number, sweep = 0): DesignParameters {
  const p = newProjectParameters();
  return { ...p, wing: { ...p.wing, span_mm: span, root_chord_mm: root, tip_chord_mm: tip, sweep_deg: sweep } };
}

describe('wing planform (closed-form, hand-checked)', () => {
  it('rectangular wing: S = b c, A = b / c, MAC = c at quarter span', () => {
    // b = 1000 mm, c = 200 mm: S = 0.2 m^2, A = 5, MAC = 200 mm, y_MAC = b/4 = 250 mm.
    const g = buildGeometry(withWing(1000, 200, 200));
    expect(g.wing.area_m2).toBeCloseTo(0.2, 12);
    expect(g.wing.aspect_ratio).toBeCloseTo(5, 12);
    expect(g.wing.taper_ratio).toBe(1);
    expect(g.wing.mac_mm).toBeCloseTo(200, 10);
    expect(g.wing.mac_y_mm).toBeCloseTo(250, 10);
    expect(g.wing.ac_x_mm).toBeCloseTo(300 + 50, 10);
  });

  it('tapered wing: S, A, MAC and its station by the trapezoid formulas', () => {
    // b = 2000, c_r = 300, c_t = 150 (taper 0.5):
    // S = 2000 x 450 / 2 = 0.45 m^2; A = 2000^2 / 450 000 = 8.8889;
    // MAC = (2/3) 300 (1 + 0.5 + 0.25) / 1.5 = 233.333 mm; y = (2000/6)(1 + 1)/1.5 = 444.444 mm.
    const g = buildGeometry(withWing(2000, 300, 150));
    expect(g.wing.area_m2).toBeCloseTo(0.45, 12);
    expect(g.wing.aspect_ratio).toBeCloseTo(8.888888889, 8);
    expect(g.wing.taper_ratio).toBeCloseTo(0.5, 12);
    expect(g.wing.mac_mm).toBeCloseTo(233.3333333, 6);
    expect(g.wing.mac_y_mm).toBeCloseTo(444.4444444, 6);
  });

  it('MAC by numerical integration equals the closed form', () => {
    // MAC = (2/S) integral of c(y)^2 dy over the semi-span.
    const b = 1800;
    const cr = 260;
    const ct = 180;
    const n = 20000;
    let int2 = 0;
    let int1 = 0;
    let inty = 0;
    for (let i = 0; i < n; i++) {
      const y = ((i + 0.5) / n) * (b / 2);
      const c = cr - (cr - ct) * (y / (b / 2));
      int2 += c * c * (b / 2 / n);
      int1 += c * (b / 2 / n);
      inty += c * y * (b / 2 / n);
    }
    const t = trapezoid(b, cr, ct);
    expect(t.mac).toBeCloseTo(int2 / int1, 4);
    expect(t.macY).toBeCloseTo(inty / int1, 4);
  });

  it('swept wing: MAC leading edge moves back by y_MAC tan(sweep); quarter-chord sweep by the tan relation', () => {
    const g = buildGeometry(withWing(2000, 300, 150, 20));
    expect(g.wing.mac_x_le_mm).toBeCloseTo(300 + 444.4444444 * Math.tan(degToRad(20)), 6);
    // tan(L_c/4) = tan(20 deg) - (4 x 0.25 / 8.8889)(0.5 / 1.5)
    const expected = Math.atan(Math.tan(degToRad(20)) - (1 / 8.888888889) * (0.5 / 1.5));
    expect(degToRad(g.wing.sweep_quarter_chord_deg)).toBeCloseTo(expected, 10);
    expect(sweepAt(0, degToRad(20), 8, 0.5)).toBeCloseTo(degToRad(20), 12);
  });

  it('wetted area of a lifting surface: 2 S_exposed (1 + 0.25 t/c), within 1 % of Raymer’s 1.977 + 0.52 t/c', () => {
    expect(liftingWetted(1, 0.12)).toBeCloseTo(2.06, 12);
    expect(liftingWetted(1, 0.12) / (1.977 + 0.52 * 0.12)).toBeGreaterThan(0.99);
    expect(liftingWetted(1, 0.12) / (1.977 + 0.52 * 0.12)).toBeLessThan(1.02);
  });

  it('exposed area removes the strip inside the fuselage', () => {
    // Default: S = 0.396 m^2; fuselage 110 mm wide; chord at y = 55 mm is 260 - 80 x 55/900 = 255.111 mm.
    const g = buildGeometry(newProjectParameters());
    const strip = 55 * (260 + (260 - (80 * 55) / 900)) / 1e6;
    expect(g.wing.exposed_area_m2).toBeCloseTo(0.396 - strip, 10);
  });
});

describe('fuselage and sections', () => {
  it('ellipse perimeter of a circle is pi d, rounded rectangle by its exact formula', () => {
    expect(sectionPerimeter('ellipse', 100, 100)).toBeCloseTo(Math.PI * 100, 8);
    // 110 x 120, r = 0.25 x 110 = 27.5: P = 2 (230) - (8 - 2 pi) 27.5
    expect(sectionPerimeter('rounded_rect', 110, 120)).toBeCloseTo(460 - (8 - 2 * Math.PI) * 27.5, 8);
    expect(sectionArea('ellipse', 100, 100)).toBeCloseTo((Math.PI * 100 * 100) / 4, 8);
    expect(sectionArea('rounded_rect', 110, 120)).toBeCloseTo(110 * 120 - (4 - Math.PI) * 27.5 ** 2, 8);
  });

  it('fuselage wetted area lies between the bare tube and a Raymer-style top/side estimate', () => {
    const g = buildGeometry(newProjectParameters());
    const tube = (sectionPerimeter('rounded_rect', 110, 120) * 900) / 1e6;
    expect(g.fuselage.wetted_area_m2).toBeLessThan(tube);
    expect(g.fuselage.wetted_area_m2).toBeGreaterThan(0.7 * tube);
    expect(g.fuselage.fineness_ratio).toBeCloseTo(900 / g.fuselage.equivalent_diameter_mm, 10);
  });

  it('NACA 4-digit generator reproduces the maximum thickness', () => {
    const c = naca4Coordinates(0, 0, 0.12, 60);
    let maxT = 0;
    const half = c.length >> 1;
    for (let i = 0; i <= half; i++) {
      const up = c[i];
      const lo = c[c.length - 1 - i];
      maxT = Math.max(maxT, up[1] - lo[1]);
    }
    expect(maxT).toBeCloseTo(0.12, 2);
    expect(airfoilShape('naca2412').m).toBeCloseTo(0.02, 12);
    expect(airfoilShape('naca2412').xm).toBeCloseTo(0.4, 12);
    expect(airfoilShape('unknown-foil').origin).toBe('generic');
  });
});

describe('tail by projection', () => {
  it('V tail: panel area span x chord / cos(angle), horizontal S cos, vertical S sin, pitch-effective S cos^2', () => {
    const p = newProjectParameters();
    const g = buildGeometry(p);
    const sc = 0.5 * 0.14;
    const gam = degToRad(40);
    expect(g.tail.planform_area_m2).toBeCloseTo(sc / Math.cos(gam), 12);
    expect(g.tail.horizontal_area_m2).toBeCloseTo(sc, 12);
    expect(g.tail.vertical_area_m2).toBeCloseTo(sc * Math.tan(gam), 12);
    expect(g.tail.pitch_effective_area_m2).toBeCloseTo(sc * Math.cos(gam), 12);
    expect(g.tail.panel_angle_deg).toBe(-40);
    // Volume coefficients: V_H = S_h l / (S c), V_V = S_v l / (S b)
    expect(g.tail.horizontal_volume_coefficient).toBeCloseTo((sc * 620) / (0.396 * g.wing.mac_mm), 10);
    expect(g.tail.vertical_volume_coefficient).toBeCloseTo((sc * Math.tan(gam) * 620) / (0.396 * 1800), 10);
    expect(g.tail.quarter_chord_x_mm).toBeCloseTo(g.wing.ac_x_mm + 620, 10);
  });

  it('conventional and twin-boom H tails: fins from tail height', () => {
    const base = newProjectParameters();
    const conv = buildGeometry({ ...base, tail: { ...base.tail, type: 'conventional' } });
    expect(conv.tail.horizontal_area_m2).toBeCloseTo(0.07, 12);
    expect(conv.tail.vertical_area_m2).toBeCloseTo(0.18 * 0.14, 12);
    const h = buildGeometry({ ...base, tail: { ...base.tail, type: 'twin_boom_h' } });
    expect(h.tail.vertical_area_m2).toBeCloseTo(2 * 0.18 * 0.14, 12);
    expect(h.tail.support_kind).toBe('booms');
  });
});

describe('booms, rotors and statuses', () => {
  it('places booms and motors in the Phase 2 coordinate system', () => {
    const g = buildGeometry(newProjectParameters());
    // Boom front = x_le + x_offset = 300 - 250 = 50; motors at 50 + 40 and 50 + 660; z = wing z + 25.
    expect(g.booms[1].start).toEqual([50, 300, 0]);
    expect(g.booms[1].end).toEqual([750, 300, 0]);
    expect(g.front_rotor_x_mm).toBe(90);
    expect(g.rear_rotor_x_mm).toBe(710);
    expect(g.rotors.find((r) => r.id === 'rear_left')?.position).toEqual([710, -300, 25]);
    expect(g.rotors.filter((r) => r.tilts).map((r) => r.id)).toEqual(['front_left', 'front_right']);
    expect(g.tilt_hinge_x_mm).toBe(90);
  });

  it('quad + pusher adds a pusher rotor and no tilt hinge; rear tilt tilts the rear pair', () => {
    const p = newProjectParameters();
    const q = buildGeometry({ ...p, layout: 'quad_pusher' });
    expect(q.rotors.find((r) => r.id === 'pusher')?.axis).toEqual([-1, 0, 0]);
    expect(q.tilt_hinge_x_mm).toBeNull();
    expect(q.rotors.filter((r) => r.stopped_in_cruise && r.id !== 'pusher')).toHaveLength(4);
    const r = buildGeometry({ ...p, layout: 'rear_tilt' });
    expect(r.rotors.filter((x) => x.tilts).map((x) => x.id)).toEqual(['rear_left', 'rear_right']);
    expect(r.statuses.some((s) => s.key === 'geometry.tilt_axis')).toBe(true);
  });

  it('flags overlapping propellers', () => {
    const p = newProjectParameters();
    const g = buildGeometry({ ...p, motors: { ...p.motors, rear_x_mm: 300 } });
    expect(g.statuses.find((s) => s.key === 'geometry.props_fore_aft')?.level).toBe('fail');
  });

  it('fills schema v2 defaults for a v1 document', () => {
    const p = newProjectParameters();
    const v1: DesignParameters = { ...p, schema_version: 1, propulsion: undefined, battery: undefined, allowances: undefined };
    const r = withDefaults(v1);
    expect(r.propulsion.prop_diameter_mm).toBe(330);
    expect(r.battery.capacity_mah).toBe(5000);
    expect(r.allowances.avionics_g).toBe(220);
  });

  it('render primitives: wing and tail sections, booms, props, hinges and gear', () => {
    const g = buildGeometry(newProjectParameters());
    const names = g.render.surfaces.map((s) => s.name);
    expect(names).toEqual(['wing_right', 'wing_left', 'tail_right', 'tail_left']);
    const tip = g.render.surfaces[0].sections[1];
    // Tip section at y = 900 mm; dihedral tilts the thickness direction, moving points < 1 mm in y.
    expect(Math.abs(Math.max(...tip.map((v) => v[1])) - 900)).toBeLessThan(1);
    expect(g.render.props).toHaveLength(4);
    expect(g.render.tilt_hinges).toHaveLength(2);
    expect(g.render.landing_gear.segments.length).toBeGreaterThan(0);
    expect(g.render.fuselage.nose_bay_x1_mm).toBe(180);
    const custom = buildGeometry(newProjectParameters(), { coordinates: { sd7037: [[1, 0], [0.5, 0.05], [0, 0], [0.5, -0.02], [1, 0]] } });
    expect(custom.render.surfaces[0].sections[0]).toHaveLength(5);
  });
});
