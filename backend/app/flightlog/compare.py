"""Predicted against measured, phase by phase, for one processed log and one analysis result.

The predictions are re-evaluated at the flight's conditions with the analysis' own models (the
generic or catalogue propeller and motor, the battery model and the drag polar stored in the
result), not read off the design point:

* hover power at the logged mass: each lift-motor pair carries its share of the weight
  (+3 % download) through the motor and propeller model, with battery sag;
* cruise power at the measured airspeed and mass: drag = q S (CD_profile + CD_parasite +
  CD_i), with CD_i scaled by (mass / design mass)² (V_design / V)⁴ (CL² law, profile drag held
  at its design value), then the cruise propeller and motor operating point and battery sag;
* transition peak power scaled from the analysis' transition sweep with the hover-power ratio;
* the stall and transition speeds scaled with √(mass ratio).

The range of each prediction keeps the relative band of the analysis' own Quantity (its
one-at-a-time uncertainty). Each comparison has the predicted value and range, the measured
value, the error in %, whether the measurement is inside the range, and, when it is outside, a
plain list of likely causes.
"""

from __future__ import annotations

import math
from typing import Any

from scipy.optimize import brentq

from app.engine import battery as bat
from app.engine import propulsion as prp
from app.engine.analysis import AVIONICS_POWER_W, HOVER_DOWNLOAD_FRACTION
from app.engine.quantity import G0, RHO_SL
from app.flightlog.ocv import ocv_integral, soc_from_ocv

SCHEMA = "flightlog-compare/1"
CURRENT_SENSOR_UNC = 0.03  # relative, a calibrated analog or digital power monitor (estimate)
MASS_SCALE_UNC = 0.01  # relative, a kitchen or hanging scale (estimate)
HOVER_KEYS = ("takeoff_hover", "landing_hover", "hover")
TRANSITION_KEYS = ("transition",)
BACK_KEYS = ("back_transition",)

# ---------------------------------------------------------------------------------------------
# Design models rebuilt from the analysis result
# ---------------------------------------------------------------------------------------------


class Design:
    """What the comparison needs from a Phase 3 ``AnalysisResult``."""

    def __init__(self, result: dict[str, Any]):
        if not result or not result.get("valid"):
            raise ValueError("The analysis result is not valid; run the analysis first.")
        self.r = result
        pr = result["propulsion"]
        self.layout = result.get("layout") or result["inputs"]["parameters"]["layout"]
        self.lift_prop = _prop(pr["lift_propeller"])
        self.lift_motor = _motor(pr["lift_motor"])
        self.pusher_prop = _prop(pr["pusher_propeller"]) if pr.get("pusher_propeller") else None
        self.pusher_motor = _motor(pr["pusher_motor"]) if pr.get("pusher_motor") else None
        self.cruise_rotors = int(
            pr.get("cruise_rotors") or (1 if self.layout == "quad_pusher" else 2)
        )
        self.pack = dict(result["battery"]["pack"])
        self.s_ref = result["geometry"]["wing"]["area_m2"]
        self.mass_kg = result["mass"]["takeoff_max_payload"]["value"]
        mission = result["inputs"]["mission"]
        self.v_design = mission["cruise_speed_mps"]
        self.avionics_w = AVIONICS_POWER_W.get(mission.get("scale", "prototype"), 8.0)
        d = result["drag"]
        self.cd0 = d["cd_profile"] + d["cd_parasite"]
        self.cdi_design = d["cd_induced"]
        self.front_share = result["balance"]["hover_front_share"]["value"]
        self.summary = result["summary"]
        self.reserve = result["inputs"]["settings"]["checks"]["battery_reserve_fraction"]

    def rel_band(self, key: str) -> tuple[float, float]:
        q = self.summary.get(key) or {}
        v = q.get("value")
        if not v:
            return 0.85, 1.15
        return (q.get("low", v) / v, q.get("high", v) / v)

    def hover(self, mass_kg: float) -> dict[str, float]:
        w = mass_kg * G0 * (1 + HOVER_DOWNLOAD_FRACTION)
        share = min(1.0, max(0.0, self.front_share))
        vbus = self.pack["v_nominal"]
        total = self.avionics_w
        lv: dict[str, float] = {"current_a": math.nan, "voltage_v": vbus}
        for _ in range(3):
            total = self.avionics_w
            for sh in (share, 1 - share):
                op = prp.operating_point(self.lift_prop, self.lift_motor, w * sh / 2, 0.0, vbus)
                total += 2 * op["battery_power_w"]
            lv = bat.loaded_voltage(self.pack, total)
            vbus = lv["voltage_v"]
        return {"power_w": total, "current_a": lv["current_a"], "voltage_v": lv["voltage_v"]}

    def drag_n(self, v: float, mass_kg: float, factor: float = 1.0) -> float:
        cdi = self.cdi_design * (mass_kg / self.mass_kg) ** 2 * (self.v_design / v) ** 4
        return 0.5 * RHO_SL * v * v * self.s_ref * (self.cd0 + cdi) * factor

    def cruise(self, v: float, mass_kg: float, drag_factor: float = 1.0) -> dict[str, float]:
        if self.layout == "quad_pusher" and self.pusher_prop is not None:
            prop, motor = self.pusher_prop, self.pusher_motor
        else:
            prop, motor = self.lift_prop, self.lift_motor
        assert motor is not None
        drag = self.drag_n(v, mass_kg, drag_factor)
        vbus = self.pack["v_nominal"]
        p = math.nan
        op: dict[str, Any] = {}
        lv: dict[str, float] = {}
        for _ in range(3):
            op = prp.operating_point(prop, motor, drag / self.cruise_rotors, v, vbus)
            p = self.cruise_rotors * op["battery_power_w"] + self.avionics_w
            lv = bat.loaded_voltage(self.pack, p)
            vbus = lv["voltage_v"]
        return {
            "power_w": p,
            "drag_n": drag,
            "eta_prop": op.get("eta_prop", math.nan),
            "eta_motor": op.get("eta_motor", math.nan),
            "throttle": op.get("throttle", math.nan),
            "current_a": lv.get("current_a", math.nan),
        }

    def drag_factor_for(self, v: float, mass_kg: float, power_w: float) -> float | None:
        """Drag factor k with cruise(v, m, k) = measured power (propulsive chain included)."""

        def f(k: float) -> float:
            return self.cruise(v, mass_kg, k)["power_w"] - power_w

        try:
            lo, hi = 0.1, 6.0
            if f(lo) > 0 or f(hi) < 0:
                return None
            return brentq(f, lo, hi, xtol=1e-5)
        except (ValueError, ZeroDivisionError):
            return None


def _prop(d: dict[str, Any]) -> prp.Propeller:
    return prp.Propeller(
        diameter_m=d["diameter_mm"] / 1000,
        pitch_m=d["pitch_mm"] / 1000,
        blades=int(d.get("blades") or 2),
        ct0=d["ct0"],
        cp0=d["cp0"],
        j_zero_thrust=d["j_zero_thrust"],
    )


def _motor(d: dict[str, Any]) -> prp.Motor:
    return prp.Motor(
        kv_rpm_per_v=d["kv_rpm_per_v"],
        r_ohm=d["resistance_ohm"],
        i0_a=d["no_load_current_a"],
        i_max_a=d["max_current_a"],
    )


# ---------------------------------------------------------------------------------------------
# Measured values from the processed log
# ---------------------------------------------------------------------------------------------


def _phases(processed: dict[str, Any], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    return [p for p in processed.get("phases", []) if p["key"] in keys]


def _weighted(
    phases: list[dict[str, Any]], block: str, field: str, wfield: str
) -> tuple[float | None, float]:
    tot = 0.0
    acc = 0.0
    for p in phases:
        b = p.get(block) or {}
        v = b.get(field)
        w = b.get(wfield) if wfield in b else p.get("duration_s")
        if v is None or not w:
            continue
        acc += v * w
        tot += w
    return (acc / tot if tot > 0 else None), tot


def _rel_sem(phases: list[dict[str, Any]]) -> float:
    """Relative standard error of the steady mean power over these phases."""
    num = 0.0
    tot = 0.0
    for p in phases:
        pw = p.get("power_w") or {}
        sem, mean, w = pw.get("steady_sem"), pw.get("steady_mean"), pw.get("steady_s")
        if sem is None or not mean or not w:
            continue
        num += (sem / mean * w) ** 2
        tot += w
    return math.sqrt(num) / tot if tot > 0 else 0.05


EXPLAIN = {
    ("hover_power", "high"): [
        "the aircraft is heavier than the mass used (weigh it ready to fly and pass the mass)",
        "the propellers are less efficient in hover than the generic model (figure of merit)",
        "more download on the wing and booms under the propellers than the 3 % assumed",
        "wind or gusts during the hover make the motors work harder",
        "a weak or cold battery (more sag means more current for the same power)",
    ],
    ("hover_power", "low"): [
        "the aircraft is lighter than the mass used",
        "better propellers or motors than the generic model",
        "ground effect if the hover was close to the ground",
        "a current sensor reading low (check BATT_AMP_PERVLT against a clamp meter)",
    ],
    ("cruise_power", "high"): [
        "more drag than predicted: surface finish, gaps, the stopped propellers not aligned "
        "with the flow, landing gear or antennas",
        "the cruise propeller is less efficient at this speed than the model (it may run close "
        "to its zero-thrust advance ratio)",
        "climbing, turning or turbulence during the leg (the steady filter removes most of it)",
        "airspeed sensor reading low, so the comparison is made at too low a speed",
        "the aircraft is heavier than the mass used (induced drag grows with mass squared)",
    ],
    ("cruise_power", "low"): [
        "less drag than predicted (a cleaner airframe than the build-up assumes)",
        "a better cruise propeller operating point than the generic model",
        "descending or a tail wind gust during the leg",
        "airspeed sensor reading high, so the comparison is made at too high a speed",
    ],
    ("transition_peak_power", "high"): [
        "a faster or more aggressive transition than the 1 m/s² of the model",
        "the forward motor and the lift motors both at high power at the start of the transition",
        "a heavier aircraft or a weak battery",
    ],
    ("transition_peak_power", "low"): [
        "a gentler transition than the model, or the lift motors throttled back early",
    ],
    ("energy", "high"): [
        "the phase took more power than predicted (see the power rows) or was flown differently "
        "(climbs, turns, a longer hover)",
    ],
    ("energy", "low"): [
        "the phase took less power than predicted (see the power rows)",
    ],
    ("endurance", "high"): [
        "the measured cruise power is lower than predicted (see the cruise row)",
    ],
    ("endurance", "low"): [
        "the measured cruise or hover power is higher than predicted (see those rows)",
    ],
    ("transition_speed", "low"): [
        "the lift motors stopped before the wing alone could carry the aircraft: raise "
        "AIRSPEED_MIN (or ARSPD_FBW_MIN) or Q_ASSIST_SPEED, or check the airspeed calibration",
    ],
    ("transition_speed", "high"): [
        "the transition finished well above the speed the wing needs: lowering AIRSPEED_MIN "
        "would save lift-motor energy if the stall margin allows",
    ],
    ("stall_speed", "low"): [
        "the aircraft flew on the wing below the predicted stall speed: the wing gives more "
        "lift than predicted (CL_max) or the airspeed sensor reads low",
    ],
    ("current", "high"): [
        "see the hover power row; the battery voltage was also lower than modelled"
    ],
    ("current", "low"): [
        "see the hover power row; the battery voltage was also higher than modelled"
    ],
}


def _row(
    key: str,
    label: str,
    phase: str,
    unit: str,
    pred: float | None,
    band: tuple[float, float],
    measured: float | None,
    basis: str,
    explain_key: str | None = None,
    kind: str = "value",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "key": key,
        "label": label,
        "phase": phase,
        "unit": unit,
        "kind": kind,
        "basis": basis,
    }
    if pred is None or measured is None or not math.isfinite(pred) or not math.isfinite(measured):
        row.update(
            predicted=None,
            measured=measured,
            error_pct=None,
            inside=None,
            status="unavailable",
            explanation="Not enough data in the log (or the analysis) for this comparison.",
        )
        return row
    lo, hi = sorted((pred * band[0], pred * band[1]))
    err = (measured - pred) / pred * 100 if pred else None
    inside = measured >= lo if kind == "lower_bound" else lo <= measured <= hi
    row.update(
        predicted={"value": round(pred, 4), "low": round(lo, 4), "high": round(hi, 4)},
        measured=round(measured, 4),
        error_pct=round(err, 2) if err is not None else None,
        inside=inside,
        status="inside" if inside else "outside",
    )
    if inside:
        row["explanation"] = "Inside the predicted range."
    else:
        direction = "high" if measured > hi else "low"
        causes = EXPLAIN.get((explain_key or key, direction), [])
        row["explanation"] = (
            f"Measured {'above' if direction == 'high' else 'below'} the predicted range. "
            + ("Likely causes: " + "; ".join(causes) + "." if causes else "")
        )
    return row


def _battery_factor(processed: dict[str, Any], d: Design) -> dict[str, Any]:
    pack = d.pack
    chem = pack.get("chemistry", "lipo")
    s = int(pack["cells_series"])
    cap_ah = pack["capacity_ah"]
    flight = processed.get("whole_log") or {}
    e_meas = flight.get("energy_wh")
    q_mah = flight.get("charge_mah")
    batt = processed.get("battery") or {}
    v0 = batt.get("first_rest_voltage_v") or batt.get("first_voltage_v")
    base = {"name": "battery_usable_energy", "label": "Battery usable-energy factor"}
    if not e_meas or not q_mah or not v0:
        return {
            **base,
            "valid": False,
            "reason": "No battery energy, charge or start voltage logged.",
        }
    v_cell = v0 / s
    if not 3.2 <= v_cell <= 4.35:
        n_guess = max(1, round(v0 / 3.85))
        return {
            **base,
            "valid": False,
            "reason": f"The log's pack (about {n_guess}S from its {v0:.1f} V start voltage) is not "
            f"the design's {s}S pack, so its energy says nothing about the design's battery.",
        }
    used = q_mah / 1000 / cap_ah
    if used < 0.15:
        return {
            **base,
            "valid": False,
            "reason": f"Only {used * 100:.0f} % of the pack's capacity was used; at least 15 % is "
            "needed to judge the usable energy.",
        }
    soc0 = soc_from_ocv(chem, v_cell)
    soc1 = max(0.0, soc0 - used)
    full = ocv_integral(chem, 0.0, 1.0)
    scale = bat.CELL_NOMINAL_V.get(chem, 3.7) / full
    expected = s * cap_ah * ocv_integral(chem, soc1, soc0) * scale
    loss = (batt.get("integrated_i2_dt_a2s") or 0.0) * pack["r_pack_ohm"] / 3600
    value = (e_meas + loss) / expected if expected > 0 else None
    unc = math.sqrt(CURRENT_SENSOR_UNC**2 + 0.02**2 + (0.03 / v_cell) ** 2 / max(used, 0.15))
    return {
        **base,
        "valid": value is not None,
        "value": value,
        "uncertainty": unc,
        "weight": used,
        "basis": (
            f"Energy drawn {e_meas:.1f} Wh plus modelled internal losses {loss:.2f} Wh over "
            f"{q_mah:.0f} mAh ({used * 100:.0f} % of {cap_ah:g} Ah), against the design pack's "
            f"energy for the same charge window (state of charge {soc0 * 100:.0f} -> "
            f"{soc1 * 100:.0f} % from the {v_cell:.2f} V/cell resting start voltage, typical "
            f"{chem} open-circuit curve scaled to the {bat.CELL_NOMINAL_V.get(chem, 3.7)} V "
            "nominal). Assumes the rated capacity; only a flight to the reserve also tests it."
        ),
    }


def compare_log(
    processed: dict[str, Any],
    analysis_result: dict[str, Any],
    *,
    logged_mass_kg: float | None = None,
) -> dict[str, Any]:
    """Compare one processed log (``process_log``) with an analysis result (``run_analysis``).

    Returns ``{schema, mass_kg, mass_source, design, conditions, comparisons: [...],
    factors: {...}, notes}``; each comparison is ``{key, label, phase, unit, kind, predicted:
    {value, low, high}, measured, error_pct, inside, status, explanation, basis}``."""
    d = Design(analysis_result)
    notes: list[str] = []
    if logged_mass_kg is not None and logged_mass_kg > 0:
        mass, mass_src, mass_unc = float(logged_mass_kg), "weighed (given)", MASS_SCALE_UNC
    else:
        mass = d.mass_kg
        mass_src = "predicted take-off mass with the heaviest camera (no weighed mass given)"
        q = analysis_result["mass"]["takeoff_max_payload"]
        mass_unc = (q["high"] - q["low"]) / 2 / q["value"] if q.get("value") else 0.1
        notes.append(
            "No weighed take-off mass was given: the comparison uses the predicted mass, so a "
            "mass error shows up as a power error. Weigh the aircraft ready to fly."
        )
    rows: list[dict[str, Any]] = []
    factors: dict[str, Any] = {}

    # ---- hover ----
    hov = _phases(processed, HOVER_KEYS)
    hover_meas, hover_s = _weighted(hov, "power_w", "steady_mean", "steady_s")
    if hover_meas is None:
        hover_meas, hover_s = _weighted(hov, "power_w", "mean", "duration_s")
    hp = d.hover(mass)
    hband = d.rel_band("hover_power")
    rows.append(
        _row(
            "hover_power",
            "Hover power",
            "hover",
            "W",
            hp["power_w"],
            hband,
            hover_meas,
            f"Steady hover (climb within ±0.5 m/s) over {hover_s:.0f} s of the hover phases; "
            f"predicted at {mass:.2f} kg with the analysis' motor and propeller model.",
        )
    )
    cur_meas, _ = _weighted(hov, "current_a", "steady_mean", "duration_s")
    rows.append(
        _row(
            "hover_current",
            "Hover battery current",
            "hover",
            "A",
            hp["current_a"],
            hband,
            cur_meas,
            "Steady hover current against the predicted hover power over the modelled loaded "
            "pack voltage.",
            explain_key="current",
        )
    )
    if hover_meas and hp["power_w"]:
        rel = hover_meas / hp["power_w"]
        sem = _rel_sem(hov)
        factors["hover_power"] = {
            "name": "hover_power",
            "label": "Hover power factor",
            "valid": True,
            "value": rel,
            "uncertainty": rel * math.sqrt(sem**2 + CURRENT_SENSOR_UNC**2 + (1.5 * mass_unc) ** 2),
            "weight": hover_s,
            "basis": f"Measured steady hover power / predicted at {mass:.2f} kg ({mass_src}).",
        }

    # ---- transition peak ----
    tr_ph = _phases(processed, TRANSITION_KEYS)
    tr = analysis_result.get("transition") or {}
    peak_meas = max((p.get("power_w") or {}).get("max") or 0 for p in tr_ph) if tr_ph else None
    peak_pred = None
    if tr.get("peak_power_w"):
        peak_pred = tr["peak_power_w"] * hp["power_w"] / d.hover(d.mass_kg)["power_w"]
    rows.append(
        _row(
            "transition_peak_power",
            "Transition peak power",
            "transition",
            "W",
            peak_pred,
            hband,
            peak_meas or None,
            "Highest 0.2 s battery power in the transition against the analysis' transition "
            "sweep peak, scaled to the logged mass with the hover-power ratio.",
        )
    )

    # ---- cruise at the measured airspeed and mass ----
    cr = _phases(processed, ("cruise",))
    v_meas = None
    if cr:
        num = sum(
            ((p.get("airspeed_mps") or {}).get("steady_median") or 0)
            * ((p.get("power_w") or {}).get("steady_s") or 0)
            for p in cr
        )
        den = sum(((p.get("power_w") or {}).get("steady_s") or 0) for p in cr)
        v_meas = num / den if den > 0 else None
    cruise_meas, cruise_s = _weighted(cr, "power_w", "steady_mean", "steady_s")
    cband = d.rel_band("cruise_power")
    cp = d.cruise(v_meas, mass) if v_meas and v_meas > 1 else None
    aspd_src = (cr[0].get("airspeed_mps") or {}).get("source") if cr else None
    rows.append(
        _row(
            "cruise_power",
            "Cruise power at the measured airspeed",
            "cruise",
            "W",
            cp["power_w"] if cp else None,
            cband,
            cruise_meas,
            (
                f"Steady level cruise over {cruise_s:.0f} s at a median {v_meas:.1f} m/s "
                f"({aspd_src}); predicted by re-evaluating the analysis' drag polar and cruise "
                f"propeller at that speed and {mass:.2f} kg (design point "
                f"{d.v_design:g} m/s, {d.mass_kg:.2f} kg: "
                f"{d.summary['cruise_power']['value']:.0f} W)."
            )
            if cp and v_meas
            else "No steady cruise in this log.",
        )
    )
    if cp and cruise_meas:
        ratio = cruise_meas / cp["power_w"]
        sem = _rel_sem(cr)
        unc_ratio = ratio * math.sqrt(sem**2 + CURRENT_SENSOR_UNC**2 + (0.5 * mass_unc) ** 2)
        factors["cruise_power"] = {
            "name": "cruise_power",
            "label": "Cruise power factor",
            "valid": True,
            "value": ratio,
            "uncertainty": unc_ratio,
            "weight": cruise_s,
            "airspeed_mps": v_meas,
            "basis": f"Measured steady cruise power / predicted at {v_meas:.1f} m/s.",
        }
        k = d.drag_factor_for(v_meas, mass, cruise_meas)
        if k is not None:
            k_hi = d.drag_factor_for(v_meas, mass, cruise_meas * (1 + unc_ratio / ratio))
            k_lo = d.drag_factor_for(v_meas, mass, cruise_meas * (1 - unc_ratio / ratio))
            unc_k = (
                (k_hi - k_lo) / 2
                if k_hi is not None and k_lo is not None
                else unc_ratio * k / ratio
            )
            at_k = d.cruise(v_meas, mass, k)
            factors["cruise_drag"] = {
                "name": "cruise_drag",
                "label": "Cruise drag factor",
                "valid": True,
                "value": k,
                "uncertainty": unc_k,
                "weight": cruise_s,
                "basis": (
                    f"Drag multiplier that makes the analysis' cruise model (propeller and motor "
                    f"included) draw the measured {cruise_meas:.0f} W at {v_meas:.1f} m/s: the "
                    f"power ratio {ratio:.3f} corrected for the propulsive efficiency, which "
                    f"moves from {cp['eta_prop']:.3f} to {at_k['eta_prop']:.3f} as the thrust "
                    "changes."
                ),
            }
        else:
            notes.append(
                "The cruise drag factor could not be solved (the measured power is outside what "
                "the cruise propeller and motor model can produce between 0.1 and 6 x drag)."
            )

    # ---- energy per phase ----
    tr_mean = tr.get("mean_power_w")
    tr_pred_power = tr_mean * hp["power_w"] / d.hover(d.mass_kg)["power_w"] if tr_mean else None
    for p in processed.get("phases", []):
        key = p["key"]
        if key in HOVER_KEYS:
            pp, band = hp["power_w"], hband
        elif key in TRANSITION_KEYS + BACK_KEYS:
            pp, band = tr_pred_power, hband
        elif key == "cruise" and cp:
            pp, band = cp["power_w"], cband
        else:
            continue
        dur = p.get("duration_s") or 0
        rows.append(
            _row(
                f"energy_{key}_{p.get('flight', 1)}_{p['start_s']:.0f}",
                f"Energy: {p['label']}",
                key,
                "Wh",
                pp * dur / 3600 if pp is not None and dur else None,
                band,
                p.get("energy_wh"),
                f"Predicted power x the measured {dur:.0f} s (battery terminal energy).",
                explain_key="energy",
            )
        )

    # ---- endurance extrapolated from the measured powers ----
    usable = analysis_result["battery"]["usable_energy"]["value"]
    profile = analysis_result.get("mission", {}).get("profile", {})
    hover_time = profile.get("takeoff_hover_s", 45) + profile.get("landing_hover_s", 45)
    tr_energy_meas = [p.get("energy_wh") for p in tr_ph if p.get("energy_wh") is not None]
    bt_energy_meas = [
        p.get("energy_wh") for p in _phases(processed, BACK_KEYS) if p.get("energy_wh") is not None
    ]
    if cruise_meas and hover_meas and cp:
        tr_out = sum(tr_energy_meas) / len(tr_energy_meas) if tr_energy_meas else None
        tr_in = sum(bt_energy_meas) / len(bt_energy_meas) if bt_energy_meas else None
        tr_pred_e = (tr_pred_power or 0) * (tr.get("duration_s") or 0) / 3600
        vtol_meas = (
            hover_meas * hover_time / 3600
            + (tr_out if tr_out is not None else tr_pred_e)
            + (tr_in if tr_in is not None else tr_pred_e)
        )
        vtol_pred = hp["power_w"] * hover_time / 3600 + 2 * tr_pred_e
        end_meas = max(0.0, usable - vtol_meas) * 60 / cruise_meas
        end_pred = max(0.0, usable - vtol_pred) * 60 / cp["power_w"]
        rows.append(
            _row(
                "endurance_cruise",
                "Wing-flight endurance (extrapolated)",
                "mission",
                "min",
                end_pred,
                d.rel_band("endurance_cruise"),
                end_meas,
                f"Usable energy {usable:.1f} Wh minus {hover_time:.0f} s of hover and two "
                f"transitions, divided by the cruise power at {v_meas:.1f} m/s: measured powers "
                "against predicted ones at the same speed and mass (battery terminal energy).",
                explain_key="endurance",
            )
        )

    # ---- speeds ----
    if tr_ph and tr.get("speed_wing_80pct_mps"):
        sc = math.sqrt(mass / d.mass_kg)
        v80 = tr["speed_wing_80pct_mps"] * sc
        v100 = tr["speed_wing_100pct_mps"] * sc
        last = tr_ph[-1]
        v_tr = last.get("airspeed_reached_mps")
        how = "the 'Transition airspeed reached' message"
        if v_tr is None:
            v_tr = (last.get("airspeed_mps") or {}).get("max")
            how = "the highest airspeed in the transition phase"
        rows.append(
            _row(
                "transition_speed",
                "Airspeed when the transition completed",
                "transition",
                "m/s",
                v100,
                (v80 / v100, 1.5),
                v_tr,
                f"Measured from {how}; predicted range from the speed where the wing carries "
                f"80 % of the weight ({v80:.1f} m/s) up, around the speed where it carries all "
                f"of it ({v100:.1f} m/s) at {mass:.2f} kg.",
                kind="lower_bound",
            )
        )
    if cr:
        stall_q = d.summary.get("stall_speed") or {}
        v_min = min(((p.get("airspeed_mps") or {}).get("min") or math.inf) for p in cr)
        if stall_q.get("value") and math.isfinite(v_min):
            sc = math.sqrt(mass / d.mass_kg)
            row = _row(
                "stall_speed",
                "Lowest airspeed flown on the wing",
                "cruise",
                "m/s",
                stall_q["value"] * sc,
                (stall_q["low"] / stall_q["value"], stall_q["high"] / stall_q["value"]),
                v_min,
                "The aircraft flew on the wing (lift motors off) down to this airspeed, so the "
                "real stall speed is at or below it; inside means the predicted stall speed does "
                "not contradict the flight.",
                kind="lower_bound",
            )
            if row["status"] == "inside":
                row["explanation"] = (
                    "Consistent: the lowest wing-borne airspeed is above the predicted stall "
                    "speed range's low end (a stall was not approached, so this is only a bound)."
                )
            rows.append(row)

    bf = _battery_factor(processed, d)
    factors["battery_usable_energy"] = bf
    for name in ("hover_power", "cruise_power", "cruise_drag"):
        factors.setdefault(
            name,
            {"name": name, "valid": False, "reason": "No steady phase of this kind in the log."},
        )
    return {
        "schema": SCHEMA,
        "log": processed.get("file", {}).get("name"),
        "mass_kg": mass,
        "mass_source": mass_src,
        "mass_uncertainty_rel": mass_unc,
        "design": {
            "layout": d.layout,
            "design_mass_kg": d.mass_kg,
            "design_cruise_speed_mps": d.v_design,
            "pack": d.pack.get("label"),
            "analysis_mode": analysis_result.get("mode"),
            "engine_version": analysis_result.get("engine_version"),
        },
        "conditions": {
            "cruise_airspeed_mps": v_meas,
            "airspeed_source": aspd_src,
            "predicted_cruise_at_measured": cp,
            "predicted_hover_at_mass": hp,
        },
        "comparisons": rows,
        "counts": {
            "inside": sum(1 for r in rows if r["status"] == "inside"),
            "outside": sum(1 for r in rows if r["status"] == "outside"),
            "unavailable": sum(1 for r in rows if r["status"] == "unavailable"),
        },
        "factors": factors,
        "notes": notes,
        "method": __doc__.strip() if __doc__ else "",
    }
