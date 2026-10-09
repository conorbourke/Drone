import { describe, expect, it } from 'vitest';
import { defaultInput } from './__fixtures__/designs';
import { estimate } from './estimate';
import { idealHoverPower } from './mass';
import { performanceCore, type PerfInputs } from './performance';

describe('momentum theory hover power (Leishman ch. 2)', () => {
  it('2.5 kg on four 330 mm rotors: 33.15 W ideal per rotor, P = T v_i', () => {
    // T = 2.5 x 9.80665 / 4 = 6.12916 N, A = pi 0.165^2 = 0.085530 m^2.
    // P_ideal = T^1.5 / sqrt(2 rho A) = 33.148 W; induced velocity v_i = sqrt(T / (2 rho A)) = 5.4083 m/s.
    const T = (2.5 * 9.80665) / 4;
    const A = Math.PI * 0.165 ** 2;
    expect(idealHoverPower(T, A, 1.225)).toBeCloseTo(33.148, 3);
    expect(idealHoverPower(T, A, 1.225)).toBeCloseTo(T * Math.sqrt(T / (2 * 1.225 * A)), 10);
    // With figure of merit 0.65, motor 0.85 and ESC 0.95: 4 x 33.148 / 0.65 / 0.8075 = 252.6 W electrical.
    expect((4 * idealHoverPower(T, A, 1.225)) / 0.65 / (0.85 * 0.95)).toBeCloseTo(252.62, 1);
  });
});

describe('endurance arithmetic against a hand calculation', () => {
  const inputs: PerfInputs = {
    massKg: 3,
    frontShare: 0.5,
    rho: 1.225,
    speed: 16,
    wingArea: 0.4,
    cd0: 0.04,
    oswald: 0.8,
    aspectRatio: 8,
    etaProp: 0.65,
    discArea: Math.PI * 0.165 ** 2,
    avionicsW: 8,
    energyWh: 111,
    reserveFraction: 0.2,
    packVoltage: 22.2,
    capacityAh: 5,
  };
  it('matches the step-by-step hand numbers', () => {
    // Hand calculation (docs/ENGINE.md mission profile):
    //   W = 29.420 N, q = 156.8 Pa, CL = 0.46907, CDi = CL^2 / (pi 0.8 x 8) = 0.010943
    //   D = q S (0.04 + 0.010943) = 3.1952 N; P_cruise = D V / (0.65 x 0.85 x 0.95) + 8 = 105.40 W
    //   hover: T = 1.03 W, 4 x (T/4)^1.5 / sqrt(2 rho A) / 0.65 / 0.8075 + 8 = 355.13 W
    //   transition 1.25 x (355.13 - 8) + 8 = 441.91 W
    //   usable = 111 x 0.8 x 0.95 = 84.36 Wh; VTOL = (355.13 x 90 + 441.91 x 30) / 3600 = 12.561 Wh
    //   cruise time = (84.36 - 12.561) x 60 / 105.40 = 40.873 min; range = 39.24 km; peak 19.906 A = 3.98 C
    const c = performanceCore(inputs);
    expect(c.cl).toBeCloseTo(0.469068, 5);
    expect(c.cdInduced).toBeCloseTo(0.0109431, 6);
    expect(c.dragN).toBeCloseTo(3.19515, 4);
    expect(c.cruisePowerW).toBeCloseTo(105.399, 2);
    expect(c.hoverPowerW).toBeCloseTo(355.129, 2);
    expect(c.transitionPowerW).toBeCloseTo(441.911, 2);
    expect(c.usableWh).toBeCloseTo(84.36, 9);
    expect(c.vtolWh).toBeCloseTo(12.5608, 3);
    expect(c.cruiseTimeS / 60).toBeCloseTo(40.873, 2);
    expect(c.rangeM / 1000).toBeCloseTo(39.238, 2);
    expect(c.peakCurrentA).toBeCloseTo(19.906, 2);
    expect(c.cRate).toBeCloseTo(3.981, 2);
    expect(c.segments.reduce((s, x) => s + x.energy_wh, 0)).toBeCloseTo(c.usableWh, 9);
  });
  it('uneven hover loading costs power (momentum theory is convex in thrust)', () => {
    expect(performanceCore({ ...inputs, frontShare: 0.6 }).hoverPowerW).toBeGreaterThan(performanceCore(inputs).hoverPowerW);
  });
});

describe('monotonic sanity on the full estimate', () => {
  const base = estimate(defaultInput());

  it('more span lowers induced drag', () => {
    const i = defaultInput();
    i.parameters.wing = { ...i.parameters.wing, span_mm: 2100 };
    const e = estimate(i);
    expect(e.aero!.cd_induced.value).toBeLessThan(base.aero!.cd_induced.value);
    expect(e.aero!.aspect_ratio.value).toBeGreaterThan(base.aero!.aspect_ratio.value);
  });

  it('more mass raises stall speed and hover power', () => {
    const i = defaultInput();
    i.mission = { ...i.mission, payload_max_g: 900 };
    const e = estimate(i);
    expect(e.mass!.takeoff_max_payload.value).toBeGreaterThan(base.mass!.takeoff_max_payload.value);
    expect(e.aero!.stall_speed.value).toBeGreaterThan(base.aero!.stall_speed.value);
    expect(e.performance!.hover_power.value).toBeGreaterThan(base.performance!.hover_power.value);
    expect(e.performance!.cruise_power.value).toBeGreaterThan(base.performance!.cruise_power.value);
  });

  it('a bigger battery adds both energy and mass', () => {
    const i = defaultInput();
    i.parameters.battery = { ...i.parameters.battery!, capacity_mah: 8000 };
    const e = estimate(i);
    expect(e.performance!.battery_energy.value).toBeGreaterThan(base.performance!.battery_energy.value);
    expect(e.mass!.battery.value).toBeGreaterThan(base.mass!.battery.value);
    expect(e.mass!.takeoff_max_payload.value).toBeGreaterThan(base.mass!.takeoff_max_payload.value);
    expect(e.performance!.endurance_cruise.value).toBeGreaterThan(base.performance!.endurance_cruise.value);
  });

  it('endurance range brackets the nominal and widens with the stated factors', () => {
    const q = base.performance!.endurance_cruise;
    expect(q.low).toBeLessThan(q.value);
    expect(q.high).toBeGreaterThan(q.value);
    expect((q.high - q.low) / q.value).toBeGreaterThan(0.2);
    expect((q.high - q.low) / q.value).toBeLessThan(0.8);
  });
});
