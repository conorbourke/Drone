"""Propulsion: first-order DC motor model matched to propeller coefficient curves CT(J), CP(J).

Motor (brushless outrunner treated as a first-order DC machine; Drela, "First-Order DC Electric
Motor Model", MIT 16.01 notes 2007; Lundström, Amadori & Krus, AIAA 2010-483):

    Kt = 60 / (2 pi Kv) [N m/A] = 1 / Kv_rad,   V = I R + omega / Kv_rad,
    Q_shaft = Kt (I - I0),   P_shaft = Q_shaft omega,   eta_motor = P_shaft / (V I).

The ESC delivers V = throttle x V_bus; its own efficiency is 0.95 (Gundlach ch. 7: 0.93-0.97).

Propeller (fixed pitch, axial inflow; Brandt & Selig, "Propeller Performance Data at Low Reynolds
Numbers", AIAA 2011-1255, and the UIUC Propeller Data Site, vols. 1-2):

    T = CT(J) rho n^2 D^4,   P = CP(J) rho n^3 D^5,   J = V / (n D).

Generic propellers (until Phase 4 parts): static coefficients from the pitch-to-diameter ratio,

    CT0 = 0.040 + 0.150 (P/D)  (P/D <= 0.6; 0.130 + 0.050 (P/D - 0.6) above),
    CP0 = 0.005 + 0.085 (P/D) + 0.06 max(0, P/D - 0.5)^2   (two blades, P/D 0.3-1.0),

and a fall-off with advance ratio that reaches zero thrust at J_T = 1.1 (P/D) + 0.05:

    CT(J) = CT0 (1 - 0.5 x - 0.5 x^2),   CP(J) = CP0 (1 - 0.7 x^2),   x = J / J_T.

These are the engine author's fit to the trends of the published UIUC data for small fixed-pitch
propellers (for example APC Slow Flyer 10x4.7: CT0 ~0.11, CP0 ~0.045, zero thrust near J 0.55,
peak efficiency ~0.6 near J 0.4-0.45; static figure of merit about 0.65 for P/D 0.3-0.6, falling
for higher pitch as the blades stall statically). The UIUC data files could not be downloaded from
this build environment, so the coefficients are a reading of the published trends, not a
regression on the files: the uncertainty is +/-15 % on CT and CP (stated in every result). More
blades scale CT0 by (B/2)^0.75 and CP0 by (B/2)^0.9 (solidity trend, estimate).
Reynolds-number and blade-flexibility effects are not modelled.

When a motor with catalogue ``thrust_data`` is supplied, the static CT0 and CP0 are fitted to its
test points instead (CP0 from the shaft torque Kt (I - I0) of the motor model), and the same J
fall-off shape is kept.

Generic motors (until Phase 4): Kv is chosen so the propeller gives the full thrust the hover
thrust-to-weight minimum demands (the Tier 1 sizing thrust) at full throttle with the back-EMF at
88 % of the bus voltage; the winding resistance makes the I R drop 12 % of the bus voltage at that
current, and the no-load current is 2.5 % of it (engine estimate from catalogue values of T-Motor
MN3508/MN4014 and SunnySky X2216-class motors: I R / V 0.11-0.16, I0 / I_max 0.02-0.03; +/-30 %).
So hover sits at the throttle implied by the thrust-to-weight minimum (about 70 % for 2:1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

from scipy.optimize import brentq

from app.engine.quantity import RHO_SL

ETA_ESC = 0.95
PROP_UNCERTAINTY = 0.15
MOTOR_UNCERTAINTY = 0.30
BACK_EMF_FRACTION_AT_MAX = 0.88
IR_FRACTION_AT_MAX = 0.12
I0_FRACTION = 0.025
#: Pusher propeller pitch-to-diameter ratio when not given (schema has no pusher pitch): 0.7,
#: a typical cruise propeller (e.g. 10x7). ASSUMPTION.
PUSHER_PITCH_RATIO = 0.7
PROP_SOURCE = (
    "Generic fixed-pitch propeller fitted to UIUC propeller-database trends (Brandt & Selig, "
    "AIAA 2011-1255; UIUC Propeller Data Site): CT0 ~ 0.040 + 0.150 P/D, CP0 ~ 0.005 + 0.085 P/D, "
    "thrust falling to zero at J = 1.1 P/D + 0.05; +/-15 % on CT and CP."
)
MOTOR_SOURCE = (
    "First-order DC motor model (Kt = 60/(2 pi Kv), V = I R + omega/Kv, Q = Kt (I - I0); Drela "
    "2007; Lundström et al. AIAA 2010-483). Generic motor: Kv for full thrust at 88 % back-EMF, "
    "R for a 12 % I R drop and I0 = 2.5 % of full current (catalogue-based estimate, +/-30 %), "
    "labelled assumed until real motors are chosen in Phase 4."
)


@dataclass
class Propeller:
    diameter_m: float
    pitch_m: float
    blades: int
    ct0: float
    cp0: float
    j_zero_thrust: float
    source: str = PROP_SOURCE
    fitted: bool = False
    ct_factor: float = 1.0
    cp_factor: float = 1.0

    @property
    def pitch_ratio(self) -> float:
        return self.pitch_m / self.diameter_m

    def ct(self, j: float) -> float:
        x = max(0.0, j) / self.j_zero_thrust
        return self.ct0 * self.ct_factor * (1 - 0.5 * x - 0.5 * x * x)

    def cp(self, j: float) -> float:
        x = max(0.0, j) / self.j_zero_thrust
        return self.cp0 * self.cp_factor * max(0.05, 1 - 0.7 * x * x)

    def efficiency(self, j: float) -> float:
        cp = self.cp(j)
        return j * self.ct(j) / cp if cp > 0 and j > 0 else 0.0

    def figure_of_merit(self) -> float:
        """Static figure of merit CT^1.5 / (sqrt(pi/2) CP), n-based coefficients (Leishman)."""
        ct, cp = self.ct(0.0), self.cp(0.0)
        return ct**1.5 / (math.sqrt(math.pi / 2) * cp) if cp > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "diameter_mm": self.diameter_m * 1000,
            "pitch_mm": self.pitch_m * 1000,
            "blades": self.blades,
            "ct0": self.ct0 * self.ct_factor,
            "cp0": self.cp0 * self.cp_factor,
            "j_zero_thrust": self.j_zero_thrust,
            "figure_of_merit_static": self.figure_of_merit(),
            "fitted_to_test_data": self.fitted,
            "source": self.source,
        }


def static_ct0(pd: float) -> float:
    """Two-blade static thrust coefficient against pitch/diameter (UIUC trend fit)."""
    return 0.040 + 0.150 * pd if pd <= 0.6 else 0.130 + 0.050 * (pd - 0.6)


def static_cp0(pd: float) -> float:
    """Two-blade static power coefficient against pitch/diameter (UIUC trend fit)."""
    return 0.005 + 0.085 * pd + 0.06 * max(0.0, pd - 0.5) ** 2


def generic_propeller(diameter_mm: float, pitch_mm: float, blades: int = 2) -> Propeller:
    pd = min(1.2, max(0.2, pitch_mm / diameter_mm))
    b = max(2, int(blades)) / 2
    return Propeller(
        diameter_m=diameter_mm / 1000,
        pitch_m=pitch_mm / 1000,
        blades=int(blades),
        ct0=static_ct0(pd) * b**0.75,
        cp0=static_cp0(pd) * b**0.9,
        j_zero_thrust=1.1 * pd + 0.05,
    )


@dataclass
class Motor:
    kv_rpm_per_v: float
    r_ohm: float
    i0_a: float
    i_max_a: float
    source: str = MOTOR_SOURCE
    assumed: bool = True
    loss_factor: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def kv_rad(self) -> float:
        return self.kv_rpm_per_v * 2 * math.pi / 60

    @property
    def kt(self) -> float:
        return 60 / (2 * math.pi * self.kv_rpm_per_v)

    @property
    def r(self) -> float:
        return self.r_ohm * self.loss_factor

    @property
    def i0(self) -> float:
        return self.i0_a * self.loss_factor

    def to_dict(self) -> dict[str, Any]:
        return {
            "kv_rpm_per_v": self.kv_rpm_per_v,
            "resistance_ohm": self.r_ohm,
            "no_load_current_a": self.i0_a,
            "max_current_a": self.i_max_a,
            "kt_nm_per_a": self.kt,
            "assumed": self.assumed,
            "source": self.source,
            **self.extra,
        }


def fit_thrust_data(
    points: list[dict[str, Any]], diameter_mm: float, motor: Motor | None, rho: float = RHO_SL
) -> dict[str, Any] | None:
    """Static CT0 (and CP0 when the motor constants are known) from catalogue test points."""
    d = diameter_mm / 1000
    cts, cps = [], []
    for pt in points:
        rpm = pt.get("rpm") or 0
        if rpm <= 0 or pt.get("thrust_g", 0) <= 0:
            continue
        n = rpm / 60
        cts.append(pt["thrust_g"] / 1000 * 9.80665 / (rho * n * n * d**4))
        if motor is not None and pt.get("current_a", 0) > motor.i0_a:
            q = motor.kt * (pt["current_a"] - motor.i0_a)
            cps.append(2 * math.pi * q / (rho * n * n * d**5))
    if not cts:
        return None
    ct0 = sum(cts) / len(cts)
    spread = max(abs(c - ct0) for c in cts) / ct0 if ct0 > 0 else 0.0
    return {
        "ct0": ct0,
        "cp0": (sum(cps) / len(cps)) if cps else None,
        "points": len(cts),
        "spread": spread,
    }


def size_generic_motor(
    prop: Propeller, max_thrust_n: float, bus_voltage: float, rho: float = RHO_SL
) -> Motor:
    """Generic motor giving ``max_thrust_n`` statically at full throttle on ``bus_voltage``."""
    d = prop.diameter_m
    n_max = math.sqrt(max_thrust_n / (prop.ct(0.0) * rho * d**4))
    omega = 2 * math.pi * n_max
    q_max = prop.cp(0.0) * rho * n_max**2 * d**5 / (2 * math.pi)
    kv_rad = omega / (BACK_EMF_FRACTION_AT_MAX * bus_voltage)
    kt = 1 / kv_rad
    i_max = q_max / kt / (1 - I0_FRACTION)
    r = IR_FRACTION_AT_MAX * bus_voltage / i_max
    return Motor(
        kv_rpm_per_v=kv_rad * 60 / (2 * math.pi),
        r_ohm=r,
        i0_a=I0_FRACTION * i_max,
        i_max_a=i_max,
        extra={
            "sized_for_thrust_n": max_thrust_n,
            "sized_on_bus_v": bus_voltage,
            "max_shaft_power_w": q_max * omega,
            "max_electrical_power_w": bus_voltage * i_max,
        },
    )


def _state(
    prop: Propeller, motor: Motor, n: float, airspeed: float, rho: float
) -> dict[str, float]:
    d = prop.diameter_m
    j = airspeed / (n * d) if n > 0 else 0.0
    thrust = prop.ct(j) * rho * n * n * d**4
    p_shaft = prop.cp(j) * rho * n**3 * d**5
    omega = 2 * math.pi * n
    q = p_shaft / omega if omega > 0 else 0.0
    i = q / motor.kt + motor.i0
    v = i * motor.r + omega / motor.kv_rad
    p_elec = v * i
    return {
        "n": n,
        "rpm": n * 60,
        "j": j,
        "thrust_n": thrust,
        "torque_nm": q,
        "shaft_power_w": p_shaft,
        "current_a": i,
        "motor_voltage_v": v,
        "motor_power_w": p_elec,
        "eta_motor": p_shaft / p_elec if p_elec > 0 else 0.0,
        "eta_prop": prop.efficiency(j),
        "battery_power_w": p_elec / ETA_ESC,
    }


def operating_point(
    prop: Propeller,
    motor: Motor,
    thrust_n: float,
    airspeed: float,
    bus_voltage: float,
    rho: float = RHO_SL,
) -> dict[str, Any]:
    """Operating point delivering ``thrust_n`` at ``airspeed`` (axial inflow)."""
    d = prop.diameter_m
    n_lo = airspeed / (prop.j_zero_thrust * d) * 1.0001 + 1e-3
    n_hi = max(2 * n_lo, 10.0)

    def f(n: float) -> float:
        return _state(prop, motor, n, airspeed, rho)["thrust_n"] - thrust_n

    while f(n_hi) < 0 and n_hi < 5000:
        n_hi *= 1.6
    if thrust_n <= 0:
        st = _state(prop, motor, n_lo, airspeed, rho)
    else:
        st = _state(prop, motor, brentq(f, n_lo, n_hi, xtol=1e-6), airspeed, rho)
    st["throttle"] = st["motor_voltage_v"] / bus_voltage if bus_voltage > 0 else float("inf")
    st["feasible"] = st["throttle"] <= 1.0 + 1e-9
    st["bus_voltage_v"] = bus_voltage
    st["eta_total"] = (
        (thrust_n * airspeed / st["battery_power_w"]) if st["battery_power_w"] > 0 else 0.0
    )
    st["thrust_required_n"] = thrust_n
    return st


def max_thrust(
    prop: Propeller, motor: Motor, airspeed: float, bus_voltage: float, rho: float = RHO_SL
) -> dict[str, Any]:
    """Full-throttle operating point at ``airspeed``: torque balance Kt (I - I0) = Q_prop."""
    d = prop.diameter_m

    def g(omega: float) -> float:
        n = omega / (2 * math.pi)
        i = (bus_voltage - omega / motor.kv_rad) / motor.r
        q_prop = prop.cp(airspeed / (n * d)) * rho * n * n * d**5 / (2 * math.pi)
        return motor.kt * (i - motor.i0) - q_prop

    w_hi = bus_voltage * motor.kv_rad * 0.9999
    w_lo = w_hi * 1e-3
    if g(w_hi) > 0:  # cannot load the motor (windmilling); no thrust
        st = _state(prop, motor, w_hi / (2 * math.pi), airspeed, rho)
    else:
        st = _state(prop, motor, brentq(g, w_lo, w_hi, xtol=1e-6) / (2 * math.pi), airspeed, rho)
    st["throttle"] = 1.0
    st["bus_voltage_v"] = bus_voltage
    return st


def with_factors(
    prop: Propeller, motor: Motor, ct: float = 1.0, cp: float = 1.0, loss: float = 1.0
) -> tuple[Propeller, Motor]:
    return replace(prop, ct_factor=ct, cp_factor=cp), replace(motor, loss_factor=loss)
