"""Full (Tier 2) analysis: AVL + XFOIL + drag build-up + propulsion + battery + transition +
structure + checks, as one JSON-ready ``AnalysisResult`` (docs/phases/PHASE3.md section 2).

Public entry point::

    run_analysis(parameters, mission, settings, *, mode="full", cache_dir=None,
                 progress=None, settings_meta=None, parts=None) -> dict

``mode="full"`` runs XFOIL for any polar not yet cached; ``mode="fast"`` (recommendation sweep,
assistant quick analysis) never runs XFOIL and uses cached polars or the Phase 2 table. Every
reported number is a Quantity ``{value, low, high, unit, label, explain, source}``.

Uncertainty: ranges on power, endurance and range come from one-factor-at-a-time re-runs of the
performance model (rule 2 of docs/ENGINE.md) with these factors:

=========================  ==================================  =====================================
factor                     range                               basis
=========================  ==================================  =====================================
profile drag (XFOIL)       -10 % / +30 % printed, +/-10 % CF   XFOIL assumes a smooth section
non-lifting parasite drag  +/-20 %                             Raymer: build-ups within 10-20 %
induced drag (AVL)         +/-5 %                              vortex lattice vs flight data
mass                       +/- mass model sigma                docs/ENGINE.md rule 2
battery energy             +/-5 %                              cell spread, temperature
propeller CT and CP        +/-15 % each                        generic UIUC-trend propeller
motor losses (R, I0)       +/-30 %                             generic motor estimate
pack resistance            +/-30 %                             battery module
=========================  ==================================  =====================================
"""

from __future__ import annotations

import copy
import itertools
import math
import time
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from app.engine import avl_model
from app.engine import battery as bat
from app.engine import checks as chk
from app.engine import drag as drg
from app.engine import propulsion as prp
from app.engine import structure as stc
from app.engine import transition as trn
from app.engine.avl_model import (
    SolverError,
    run_avl,
    tail_strips,
    wing_strips,
)
from app.engine.geometry import build_geometry
from app.engine.mass import (
    HOVER_DOWNLOAD_FRACTION,
    clamp,
    solve_mass,
)
from app.engine.polars import PolarStore, SectionPolar, polar_at, strip_profile_drag
from app.engine.quantity import (
    G0,
    MU_SL,
    RHO_SL,
    one_at_a_time,
    q_abs,
    q_asym,
    q_exact,
    q_range,
    q_rel,
    rss,
    rss_powers,
    sort_statuses,
    status,
)

ENGINE_VERSION = "tier2-1.0"
RESULT_SCHEMA = "analysis-result/1"
MISSION_PROFILE = {"takeoff_hover_s": 45.0, "landing_hover_s": 45.0}
AVIONICS_POWER_W = {"prototype": 8.0, "final": 25.0}
A3_NOTE = (
    "Visual line of sight; flight beyond needs IAA authorisation; at least 150 m from "
    "residential, commercial, industrial or recreational areas (EU Open category A3)."
)
PHASE3_SETTINGS_DEFAULTS = {
    "checks": {
        "manoeuvre_load_factor": 3.0,
        "structural_safety_factor": 1.5,
        "transition_thrust_margin_min": 1.3,
    },
    "analysis": {"ncrit": 9.0},
}

ProgressFn = Callable[[float, str], None]


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def resolve_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    """Settings document with every value the engine reads (Phase 3 additions defaulted)."""
    from app.defaults import DEFAULT_SETTINGS

    s = copy.deepcopy(DEFAULT_SETTINGS)
    for block, values in (settings or {}).items():
        if isinstance(values, dict) and isinstance(s.get(block), dict):
            s[block].update(values)
        else:
            s[block] = values
    for block, values in PHASE3_SETTINGS_DEFAULTS.items():
        s.setdefault(block, {})
        for k, v in values.items():
            if s[block].get(k) is None:
                s[block][k] = v
    return s


def validate_inputs(
    parameters: dict[str, Any], mission: dict[str, Any]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
    """Pydantic validation (schema upgrade, defaults, sanity rules) of parameters and mission."""
    from app.schemas.design import DesignParameters
    from app.schemas.mission import Mission

    problems: list[dict[str, Any]] = []
    p = m = None
    try:
        p = DesignParameters.model_validate(parameters).model_dump()
    except ValidationError as exc:
        for e in exc.errors():
            loc = ".".join(str(x) for x in e["loc"])
            problems.append(
                status(
                    f"input.{loc or 'parameters'}",
                    "Design parameters",
                    "fail",
                    f"{loc}: {e['msg']}" if loc else e["msg"],
                )
            )
    try:
        m = Mission.model_validate(mission).model_dump()
    except ValidationError as exc:
        for e in exc.errors():
            loc = ".".join(str(x) for x in e["loc"])
            problems.append(
                status(
                    f"input.mission.{loc}",
                    "Mission",
                    "fail",
                    f"{loc}: {e['msg']}" if loc else e["msg"],
                )
            )
    return p, m, problems


# ---------------------------------------------------------------------------
# Tier 1 reference (port of the browser formulas, for the comparison table only)
# ---------------------------------------------------------------------------


def _helmbold(ar: float, sweep_t: float, cla: float, m: float, body: float) -> float:
    beta2 = max(1e-4, 1 - m * m)
    eta = cla / (2 * math.pi / math.sqrt(beta2))
    root = math.sqrt(4 + ar * ar * beta2 / (eta * eta) * (1 + math.tan(sweep_t) ** 2 / beta2))
    return 2 * math.pi * ar / (2 + root) * (0.98 if body > 1 else body)


def _oswald(ar: float, sweep_le: float) -> float:
    base = 1 - 0.045 * ar**0.68
    straight = 1.78 * base - 0.64
    swept = 4.61 * base * math.cos(math.radians(min(abs(sweep_le), 89))) ** 0.15 - 3.1
    s = abs(sweep_le)
    t = 0 if s <= 25 else 1 if s >= 35 else (s - 25) / 10
    return min(0.95, max(0.5, (1 - t) * straight + t * swept))


def _munk(f: float) -> float:
    table = [
        (1, 0),
        (2, 0.49),
        (3, 0.68),
        (4, 0.78),
        (5, 0.83),
        (6, 0.87),
        (8, 0.92),
        (10, 0.94),
        (15, 0.97),
        (20, 0.98),
    ]
    if not f > 1:
        return 0.0
    for (f0, k0), (f1, k1) in itertools.pairwise(table):
        if f <= f1:
            return k0 + (f - f0) / (f1 - f0) * (k1 - k0)
    return 0.98


def tier1_reference(
    g: dict[str, Any],
    table_wing: SectionPolar,
    table_tail: SectionPolar,
    mass_kg: float,
    speed: float,
    cd0_tier1: float,
) -> dict[str, float]:
    """Tier 1 lift slope, Oswald, CL_max, neutral point and the fixed-efficiency powers."""
    w, t, f = g["wing"], g["tail"], g["fuselage"]
    m = speed / 340.294
    body = (
        1.07
        * (1 + f["equivalent_diameter_mm"] / w["span_mm"]) ** 2
        * w["exposed_area_m2"]
        / w["area_m2"]
    )
    cla_sec = table_wing.summary["cl_alpha_per_rad"] or 5.9
    aw = _helmbold(w["aspect_ratio"], math.radians(w["sweep_max_thickness_deg"]), cla_sec, m, body)
    at = _helmbold(t["aspect_ratio"], 0.0, table_tail.summary["cl_alpha_per_rad"] or 5.7, m, 1.0)
    de = 2 * aw / (math.pi * w["aspect_ratio"])
    at_eff = 0.9 * at * t["pitch_effective_area_m2"] / w["area_m2"] * (1 - de)
    # Multhopp fuselage moment (Nelson ch. 2), as aero.ts.
    st = f["stations"]
    root_le = w["root_le"][0]
    root_te = root_le + w["root_chord_mm"]
    root_c4 = root_le + 0.25 * w["root_chord_mm"]
    lh = max(1.0, t["quarter_chord_x_mm"] - root_te)

    def width_at(x: float) -> float:
        for a, b in itertools.pairwise(st):
            if a["x_mm"] <= x <= b["x_mm"]:
                tt = (x - a["x_mm"]) / max(1e-9, b["x_mm"] - a["x_mm"])
                return a["width_mm"] + tt * (b["width_mm"] - a["width_mm"])
        return 0.0

    total = 0.0
    n = 40
    length = f["length_mm"]
    for i in range(n):
        x = (i + 0.5) * length / n
        if x < root_le:
            fac = 1 + w["root_chord_mm"] * aw / (
                4 * math.pi * max(root_c4 - x, 0.25 * w["root_chord_mm"])
            )
        elif x <= root_te:
            fac = 0.0
        else:
            fac = (x - root_te) / lh * (1 - de)
        total += (width_at(x) / 1000) ** 2 * fac * (length / n / 1000)
    cmf = _munk(f["fineness_ratio"]) * math.pi / 2 * total / (w["area_m2"] * w["mac_mm"] / 1000)
    x_np = (aw * w["ac_x_mm"] + at_eff * t["quarter_chord_x_mm"]) / (aw + at_eff) - w[
        "mac_mm"
    ] * cmf / (aw + at_eff)
    e = _oswald(w["aspect_ratio"], w["sweep_le_deg"])
    weight = mass_kg * G0
    q = 0.5 * RHO_SL * speed * speed
    cl = weight / (q * w["area_m2"])
    cd = cd0_tier1 + cl * cl / (math.pi * e * w["aspect_ratio"])
    return {
        "lift_slope": aw,
        "oswald": e,
        "cl_max": 0.9
        * (table_wing.summary["cl_max"] or 1.15)
        * math.cos(math.radians(w["sweep_quarter_chord_deg"])),
        "neutral_point_x_mm": x_np,
        "cd0": cd0_tier1,
        "cd_induced": cl * cl / (math.pi * e * w["aspect_ratio"]),
        "lift_to_drag": cl / cd,
        "drag_n": q * w["area_m2"] * cd,
    }


# ---------------------------------------------------------------------------
# Performance core (re-run for every uncertainty factor)
# ---------------------------------------------------------------------------

NOMINAL = {
    "profile": 1.0,
    "parasite": 1.0,
    "induced": 1.0,
    "mass": 1.0,
    "energy": 1.0,
    "ct": 1.0,
    "cp": 1.0,
    "motor_loss": 1.0,
    "pack_r": 1.0,
}


def performance_core(
    st: dict[str, Any], f: dict[str, float] | None = None, with_transition: bool = True
) -> dict[str, Any]:
    """Propulsion, battery, transition and mission energy for one set of factors."""
    f = {**NOMINAL, **(f or {})}
    p = st["p"]
    mission = st["mission"]
    v = mission["cruise_speed_mps"]
    s_ref = st["s_ref"]
    q = 0.5 * RHO_SL * v * v
    out: dict[str, Any] = {}
    pack = bat.pack_model(p["battery"], f["pack_r"])
    pack = {**pack, "energy_wh": pack["energy_wh"] * f["energy"]}
    lift_prop, lift_motor = prp.with_factors(
        st["lift_prop"], st["lift_motor"], f["ct"], f["cp"], f["motor_loss"]
    )
    pusher_prop = pusher_motor = None
    if st.get("pusher_prop") is not None:
        pusher_prop, pusher_motor = prp.with_factors(
            st["pusher_prop"], st["pusher_motor"], f["ct"], f["cp"], f["motor_loss"]
        )
    avionics = st["avionics_w"]
    reserve = st["settings"]["checks"]["battery_reserve_fraction"]
    usable = bat.usable_energy_wh(pack, reserve)
    for case in ("max", "min"):
        mass = st[f"mass_{case}_kg"] * f["mass"]
        w = mass * G0
        cdi = st[f"cdi_{case}"] * f["induced"] * f["mass"] ** 2
        cd = st["cd_profile_" + case] * f["profile"] + st["cd_parasite"] * f["parasite"] + cdi
        drag = q * s_ref * cd
        cl = w / (q * s_ref)
        # Hover
        share = clamp(st[f"front_share_{case}"], 0.0, 1.0)
        thrust_total = w * (1 + HOVER_DOWNLOAD_FRACTION)
        vbus = pack["v_nominal"]
        hover_ops: dict[str, Any] = {}
        for _ in range(3):
            total = avionics
            for name, sh in (("front", share), ("rear", 1 - share)):
                op = prp.operating_point(lift_prop, lift_motor, thrust_total * sh / 2, 0.0, vbus)
                hover_ops[name] = op
                total += 2 * op["battery_power_w"]
            lv = bat.loaded_voltage(pack, total)
            vbus = lv["voltage_v"]
        hover_power = total
        hover_lv = lv
        # Cruise
        if p["layout"] == "quad_pusher":
            assert pusher_prop is not None and pusher_motor is not None
            cprop, cmotor, n_c = pusher_prop, pusher_motor, 1
        else:
            cprop, cmotor, n_c = lift_prop, lift_motor, 2
        vbus = pack["v_nominal"]
        for _ in range(3):
            cop = prp.operating_point(cprop, cmotor, drag / n_c, v, vbus)
            cruise_power = n_c * cop["battery_power_w"] + avionics
            clv = bat.loaded_voltage(pack, cruise_power)
            vbus = clv["voltage_v"]
        cruise_avail = prp.max_thrust(cprop, cmotor, v, vbus)["thrust_n"] * n_c
        # Transition (at this payload)
        tr = None
        if with_transition:
            tr = trn.transition_sweep(
                p["layout"],
                mass,
                s_ref,
                st["cl_max"],
                st["cd_profile_" + case] * f["profile"] + st["cd_parasite"] * f["parasite"],
                st["span_efficiency"],
                st["aspect_ratio"],
                v,
                share,
                p["tilt"]["max_angle_deg"],
                lift_prop,
                lift_motor,
                pack,
                st["settings"]["checks"]["transition_thrust_margin_min"],
                pusher_prop,
                pusher_motor,
                avionics,
            )
            tr_energy = tr["energy_wh"]
            tr_time = tr["duration_s"]
            tr_power = tr["mean_power_w"]
        else:
            tr_time = v / trn.ACCELERATION
            tr_power = 1.25 * hover_power
            tr_energy = tr_power * tr_time / 3600
        # Mission energy (energy drawn from the pack, including its internal losses)
        mp = MISSION_PROFILE
        hov = bat.phase_energy_wh(pack, hover_power, mp["takeoff_hover_s"] + mp["landing_hover_s"])
        trans_factor = 1.0
        if tr is not None:
            lvt = bat.loaded_voltage(pack, tr_power)
            trans_factor = lvt["voc"] / lvt["voltage_v"] if lvt["voltage_v"] > 0 else 1.0
        trans_wh = 2 * tr_energy * trans_factor
        cr_factor = clv["voc"] / clv["voltage_v"] if clv["voltage_v"] > 0 else 1.0
        cruise_pack_w = cruise_power * cr_factor
        vtol_wh = hov["energy_wh"] + trans_wh
        cruise_wh = max(0.0, usable - vtol_wh)
        cruise_s = cruise_wh * 3600 / cruise_pack_w if cruise_pack_w > 0 else 0.0
        total_s = cruise_s + 2 * tr_time + mp["takeoff_hover_s"] + mp["landing_hover_s"]
        out[case] = {
            "mass_kg": mass,
            "cl": cl,
            "cd": cd,
            "cd_induced": cdi,
            "drag_n": drag,
            "lift_to_drag": cl / cd,
            "hover_power_w": hover_power,
            "hover_ops": hover_ops,
            "hover_current_a": hover_lv["current_a"],
            "hover_voltage_v": hover_lv["voltage_v"],
            "cruise_power_w": cruise_power,
            "cruise_op": cop,
            "cruise_current_a": clv["current_a"],
            "cruise_thrust_available_n": cruise_avail,
            "cruise_thrust_required_n": drag,
            "transition": tr,
            "transition_power_w": tr_power,
            "transition_s": tr_time,
            "usable_wh": usable,
            "vtol_wh": vtol_wh,
            "hover_wh": hov["energy_wh"],
            "transition_wh": trans_wh,
            "cruise_wh": cruise_wh,
            "cruise_s": cruise_s,
            "total_s": total_s,
            "range_m": cruise_s * v,
            "vtol_exceeds_usable": vtol_wh >= usable,
        }
    out["pack"] = pack
    return out


def _factor_sets(
    scale: str, mass_rel: float
) -> dict[str, tuple[dict[str, float], dict[str, float]]]:
    lo, hi = drg.PROFILE_UNCERTAINTY[scale]
    mr = mass_rel if math.isfinite(mass_rel) else 0.1
    return {
        "profile": ({"profile": 1 - lo}, {"profile": 1 + hi}),
        "parasite": (
            {"parasite": 1 - drg.PARASITE_UNCERTAINTY},
            {"parasite": 1 + drg.PARASITE_UNCERTAINTY},
        ),
        "induced": ({"induced": 0.95}, {"induced": 1.05}),
        "mass": ({"mass": 1 - mr}, {"mass": 1 + mr}),
        "energy": ({"energy": 0.95}, {"energy": 1.05}),
        "ct": ({"ct": 1 - prp.PROP_UNCERTAINTY}, {"ct": 1 + prp.PROP_UNCERTAINTY}),
        "cp": ({"cp": 1 - prp.PROP_UNCERTAINTY}, {"cp": 1 + prp.PROP_UNCERTAINTY}),
        "motor_loss": ({"motor_loss": 0.7}, {"motor_loss": 1.3}),
        "pack_r": ({"pack_r": 0.7}, {"pack_r": 1.3}),
    }


# ---------------------------------------------------------------------------
# The analysis
# ---------------------------------------------------------------------------


def _invalid(problems: list[dict[str, Any]], mode: str, t0: float) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "engine_version": ENGINE_VERSION,
        "valid": False,
        "mode": mode,
        "checks": sort_statuses(problems),
        "notes": [],
        "summary": {},
        "timings": {"total_s": round(time.time() - t0, 3)},
    }


def run_analysis(
    parameters: dict[str, Any],
    mission: dict[str, Any],
    settings: dict[str, Any] | None = None,
    *,
    mode: str = "full",
    cache_dir: str | None = None,
    progress: ProgressFn | None = None,
    settings_meta: dict[str, Any] | None = None,
    parts: dict[str, Any] | None = None,
    polar_store: PolarStore | None = None,
    uncertainty: bool = True,
) -> dict[str, Any]:
    """Analyse one design. Never raises for bad input: returns ``valid: False`` with fail checks.

    ``parts`` (optional, Phase 4): ``{"lift_motor": MotorSpec dict, "lift_prop": PropellerSpec
    dict}``; a motor with ``thrust_data`` replaces the generic propeller static coefficients.
    """
    t0 = time.time()
    timings: dict[str, float] = {}

    def tick(name: str, start: float) -> float:
        now = time.time()
        timings[name] = round(now - start, 3)
        return now

    def report(frac: float, label: str) -> None:
        if progress:
            progress(frac, label)

    report(0.02, "Checking the inputs")
    p, mission_doc, problems = validate_inputs(parameters, mission)
    if p is None or mission_doc is None:
        return _invalid(problems, mode, t0)
    settings_doc = resolve_settings(settings)
    try:
        st = _prepare(
            p, mission_doc, settings_doc, mode, cache_dir, polar_store, report, timings, parts
        )
    except SolverError as exc:
        return _invalid(
            [
                status(
                    "engine.solver",
                    "Analysis engine",
                    "fail",
                    "The aerodynamic solver (AVL/XFOIL worker) failed for this design "
                    f"({exc}). Undo the last change; if it persists, report it.",
                )
            ],
            mode,
            t0,
        )
    result = _assemble(st, settings_meta, uncertainty, report, timings)
    timings["total_s"] = round(time.time() - t0, 3)
    result["timings"] = timings
    report(1.0, "Done")
    return json_safe(result)


def _prepare(
    p: dict[str, Any],
    mission: dict[str, Any],
    settings: dict[str, Any],
    mode: str,
    cache_dir: str | None,
    polar_store: PolarStore | None,
    report: ProgressFn,
    timings: dict[str, float],
    parts: dict[str, Any] | None,
) -> dict[str, Any]:
    t = time.time()
    scale = "final" if mission.get("scale") == "final" else "prototype"
    g = build_geometry(p)
    w = g["wing"]
    v = mission["cruise_speed_mps"]
    s_ref = w["area_m2"]
    mac_m = w["mac_mm"] / 1000
    report(0.06, "Weights and balance")
    ms = solve_mass(p, g, mission, settings)
    t = _tick(timings, "mass_s", t)

    # ----- Polars at the operating Reynolds numbers -----
    ncrit = float(settings.get("analysis", {}).get("ncrit", 9.0))
    store = polar_store or PolarStore(cache_dir, ncrit=ncrit, allow_xfoil=(mode == "full"))
    wing_af = w["airfoil"]
    tail_af = g["tail"]["airfoil"]
    re = lambda chord_mm, speed: RHO_SL * speed * chord_mm / 1000 / MU_SL  # noqa: E731
    table_wing = store.table_polar(wing_af, re(w["mac_mm"], v)) if wing_af else None
    clmax_guess = (table_wing.summary["cl_max"] if table_wing else 1.15) * 0.9
    weight_max = ms["total_max_g"] / 1000 * G0
    vs_guess = math.sqrt(2 * weight_max / (RHO_SL * s_ref * clmax_guess))
    jobs = [
        ("wing root", wing_af, re(w["root_chord_mm"], v)),
        ("wing MAC", wing_af, re(w["mac_mm"], v)),
        ("wing tip", wing_af, re(w["tip_chord_mm"], v)),
        ("tail", tail_af, re(g["tail"]["chord_mm"], v)),
        ("wing MAC at stall speed", wing_af, re(w["mac_mm"], vs_guess)),
        ("wing tip at stall speed", wing_af, re(w["tip_chord_mm"], vs_guess)),
    ]
    polars: dict[str, SectionPolar] = {}
    for i, (name, af, rn) in enumerate(jobs):
        report(0.10 + 0.45 * i / len(jobs), f"Airfoil polars: {name} (Re {round(rn / 1000)}k)")
        polars[name] = store.get(af, rn, "tail" if name == "tail" else "wing")
    t = _tick(timings, "polars_s", t)
    wing_polars = sorted(
        {id(pp): pp for k, pp in polars.items() if k.startswith("wing")}.values(),
        key=lambda pp: pp.re,
    )
    tail_polars = [polars["tail"]]
    claf_w = clamp(
        (polars["wing MAC"].summary["cl_alpha_per_rad"] or 2 * math.pi) / (2 * math.pi), 0.8, 1.1
    )
    claf_t = clamp(
        (polars["tail"].summary["cl_alpha_per_rad"] or 2 * math.pi) / (2 * math.pi), 0.8, 1.1
    )

    # ----- AVL pass 1, spar sizing, mass with the sized spar, AVL pass 2 -----
    report(0.58, "Vortex-lattice analysis (AVL)")
    spar_sizing = None
    sf = settings["checks"]["structural_safety_factor"]
    n_man = settings["checks"]["manoeuvre_load_factor"]
    if scale == "prototype":
        # The spar tube is sized on Schrenk's span loading (NACA TM 948) so that a single AVL
        # pass is needed; the strength check below then uses AVL's own span loading.
        spar_sizing = stc.size_spar_tube(g, stc.schrenk_strips(g), weight_max, 1.0, n_man, sf)
        tube = {"outer_mm": spar_sizing["outer_mm"], "wall_mm": spar_sizing["wall_mm"]}
        ms = solve_mass(p, g, mission, settings, spar_tube=tube)
    avl = _avl_cases(g, ms, mission, s_ref, claf_w, claf_t)
    t = _tick(timings, "avl_s", t)
    cr_max, cr_min, hi = avl["cases"]

    # ----- Stall: critical-section method on AVL strips -----
    report(0.66, "Stall speed and profile drag")
    stall = _stall(g, cr_max, hi, ms["total_max_g"] / 1000 * G0, s_ref, wing_polars)
    # ----- Profile drag (XFOIL strip integration) -----
    prof = {}
    for case, res in (("max", cr_max), ("min", cr_min)):
        ws = wing_strips(res)
        ts = tail_strips(res)
        pw = strip_profile_drag(ws, wing_polars, v, s_ref)
        pt = strip_profile_drag(ts, tail_polars, v, s_ref)
        prof[case] = {"wing": pw, "tail": pt}
    t = _tick(timings, "profile_s", t)

    # ----- Drag build-up -----
    report(0.72, "Drag build-up")
    note = "; ".join(sorted({pp.note for pp in wing_polars}))
    db = drg.drag_buildup(
        p,
        g,
        v,
        scale,
        ms["lift_motor_mass_g"],
        prof["max"]["wing"]["cd"],
        prof["max"]["tail"]["cd"],
        f"Polars: {note}.",
    )
    q_tail = drg.tail_interference(g["tail"]["type"])
    cd_profile_max = prof["max"]["wing"]["cd"] + prof["max"]["tail"]["cd"] * q_tail
    cd_profile_min = prof["min"]["wing"]["cd"] + prof["min"]["tail"]["cd"] * q_tail
    leak = 1 + drg.LEAKAGE_PROTUBERANCE_FRACTION
    # Express the leakage allowance on each part: parasite (non-lifting) x 1.1, profile x 1.1.
    non_lifting_raw = (
        sum(i["drag_area_m2"] for i in db["items"] if i["key"] not in ("wing", "tail", "leakage"))
        / s_ref
    )
    cd_parasite = non_lifting_raw * leak
    cd_profile_max *= leak
    cd_profile_min *= leak

    # ----- Propulsion: propellers and motors -----
    report(0.76, "Propulsion and battery")
    pr = p["propulsion"]
    lift_prop = prp.generic_propeller(
        pr["prop_diameter_mm"], pr["prop_pitch_mm"], pr["prop_blades"]
    )
    pack = bat.pack_model(p["battery"])
    max_thrust_req = ms["lift_motor_max_thrust_n"]
    vbus = pack["v_nominal"] * 0.95
    lift_motor = prp.size_generic_motor(lift_prop, max_thrust_req, vbus)
    for _ in range(2):
        full_power = 4 * lift_motor.i_max_a * vbus / prp.ETA_ESC
        vbus = bat.loaded_voltage(pack, full_power)["voltage_v"]
        lift_motor = prp.size_generic_motor(lift_prop, max_thrust_req, vbus)
    parts_note = None
    if parts and parts.get("lift_motor", {}).get("thrust_data"):
        spec = parts["lift_motor"]
        motor = prp.Motor(
            spec["kv_rpm_per_v"],
            spec["resistance_ohm"],
            spec["no_load_current_a"],
            spec["max_current_a"],
            source="Catalogue motor",
            assumed=False,
        )
        fit = prp.fit_thrust_data(spec["thrust_data"], pr["prop_diameter_mm"], motor)
        if fit:
            lift_prop.ct0 = fit["ct0"]
            if fit["cp0"]:
                lift_prop.cp0 = fit["cp0"]
            lift_prop.fitted = True
            lift_prop.source = (
                f"Static CT0/CP0 fitted to {fit['points']} catalogue thrust-test "
                "points; advance-ratio fall-off from the generic UIUC-trend shape."
            )
            lift_motor = motor
            parts_note = lift_prop.source
    pusher_prop = pusher_motor = None
    if p["layout"] == "quad_pusher":
        dp = p["pusher"]["prop_diameter_mm"]
        pusher_prop = prp.generic_propeller(dp, prp.PUSHER_PITCH_RATIO * dp, 2)
        pusher_motor = prp.size_generic_motor(pusher_prop, 0.5 * weight_max, vbus)
    t = _tick(timings, "propulsion_setup_s", t)

    state = {
        "p": p,
        "g": g,
        "mission": mission,
        "settings": settings,
        "scale": scale,
        "ms": ms,
        "s_ref": s_ref,
        "mac_m": mac_m,
        "polars": polars,
        "store": store,
        "avl": avl,
        "stall": stall,
        "prof": prof,
        "drag": db,
        "cd_parasite": cd_parasite,
        "cd_profile_max": cd_profile_max,
        "cd_profile_min": cd_profile_min,
        "cdi_max": cr_max["totals"]["CDff"],
        "cdi_min": cr_min["totals"]["CDff"],
        "span_efficiency": cr_max["totals"]["e"],
        "aspect_ratio": w["aspect_ratio"],
        "cl_max": stall["cl_max"],
        "lift_prop": lift_prop,
        "lift_motor": lift_motor,
        "pusher_prop": pusher_prop,
        "pusher_motor": pusher_motor,
        "pack_nominal": pack,
        "avionics_w": AVIONICS_POWER_W[scale],
        "mass_max_kg": ms["total_max_g"] / 1000,
        "mass_min_kg": ms["total_min_g"] / 1000,
        "front_share_max": ms["front_share_max"],
        "front_share_min": ms["front_share_min"],
        "spar_sizing": spar_sizing,
        "table_wing": table_wing,
        "parts_note": parts_note,
        "mode": mode,
        "claf": {"wing": claf_w, "tail": claf_t},
    }
    return state


def json_safe(v: Any) -> Any:
    """Deep copy with NaN/inf replaced by None and tuples as lists (strict JSON)."""
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, dict):
        return {k: json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [json_safe(x) for x in v]
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        return json_safe(v.item())
    return v


def _tick(timings: dict[str, float], name: str, start: float) -> float:
    now = time.time()
    timings[name] = round(timings.get(name, 0.0) + now - start, 3)
    return now


def _wing_share(res: dict[str, Any]) -> float:
    surf = res.get("surfaces", {})
    cl_w = sum((v.get("CL") or 0.0) for k, v in surf.items() if k.startswith("Wing"))
    total = res["totals"]["CL"] or 1.0
    return clamp(cl_w / total, 0.5, 1.5) if total else 1.0


def _avl_cases(
    g: dict[str, Any],
    ms: dict[str, Any],
    mission: dict[str, Any],
    s_ref: float,
    claf_w: float,
    claf_t: float,
    only_high_lift: bool = False,
) -> dict[str, Any]:
    v = mission["cruise_speed_mps"]
    q = 0.5 * RHO_SL * v * v
    cl_max_payload = ms["total_max_g"] / 1000 * G0 / (q * s_ref)
    cl_min_payload = ms["total_min_g"] / 1000 * G0 / (q * s_ref)
    cases = [
        {
            "name": "cruise_max_payload",
            "xref": ms["cg_max_x"] / 1000,
            "cl": cl_max_payload,
            "trim": True,
        },
        {
            "name": "cruise_min_payload",
            "xref": ms["cg_min_x"] / 1000,
            "cl": cl_min_payload,
            "trim": True,
        },
        {"name": "high_lift", "xref": ms["cg_max_x"] / 1000, "cl": 1.0, "trim": True},
    ]
    if only_high_lift:
        cases = cases[2:]
    return run_avl(g, cases, claf_w, claf_t)


def _stall(
    g: dict[str, Any],
    low: dict[str, Any],
    high: dict[str, Any],
    weight_n: float,
    s_ref: float,
    wing_polars: list[SectionPolar],
) -> dict[str, Any]:
    """Critical-section CL_max: the aircraft CL at which the first exposed wing strip reaches its
    local section cl_max (XFOIL polars at the cruise and stall Reynolds numbers, interpolated to
    the strip's stall-speed Reynolds number). AVL is linear, so each strip's cl is a linear
    function of the trimmed aircraft CL, from the cruise and high-lift cases. Strips inside the
    fuselage are skipped. Iterated twice for the stall Reynolds number. The method ignores the
    lift the rest of the wing still adds after the first section stalls, so it is conservative
    (it tends to under-predict CL_max by up to about 10 %; Phillips & Alley, J. Aircraft 44(3),
    2007, discuss its limits)."""
    sl = wing_strips(low)
    sh = wing_strips(high)
    cl_lo, cl_hi = low["totals"]["CL"], high["totals"]["CL"]
    vs = 12.0
    cl_max = 1.0
    crit_y = 0.0
    margins = []
    for _ in range(2):
        best = None
        margins = []
        for a, b in zip(sl, sh, strict=True):
            if a["y"] < g["fuselage"]["width_mm"] / 2000:
                continue
            slope = (b["cl"] - a["cl"]) / (cl_hi - cl_lo) if cl_hi != cl_lo else 0.0
            re = RHO_SL * vs * a["chord"] / MU_SL
            clmax_local = polar_at(wing_polars, re).cl_max
            if slope <= 0:
                continue
            cl_at = cl_lo + (clmax_local - a["cl"]) / slope
            margins.append(
                {"y_mm": a["y"] * 1000, "cl_max_local": clmax_local, "cl_per_CL": slope, "re": re}
            )
            if best is None or cl_at < best[0]:
                best = (cl_at, a["y"])
        if best is None:
            break
        cl_max, crit_y = best
        vs = math.sqrt(2 * weight_n / (RHO_SL * s_ref * cl_max))
    return {
        "cl_max": cl_max,
        "stall_speed_mps": vs,
        "critical_y_mm": crit_y * 1000,
        "strips": margins,
        "elevator_at_high_lift_deg": high.get("elevator_deg"),
    }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _q_oat(
    nominal: float,
    perturbed: list[tuple[float, float]],
    unit: str,
    label: str,
    explain: str,
    source: str,
) -> dict[str, Any]:
    lo, hi = one_at_a_time(nominal, perturbed)
    return q_range(nominal, lo, hi, unit, label, explain, source)


def _assemble(
    st: dict[str, Any],
    settings_meta: dict[str, Any] | None,
    uncertainty: bool,
    report: ProgressFn,
    timings: dict[str, float],
) -> dict[str, Any]:
    t = time.time()
    p, g, ms, mission, settings = st["p"], st["g"], st["ms"], st["mission"], st["settings"]
    w = g["wing"]
    v = mission["cruise_speed_mps"]
    scale = st["scale"]
    report(0.80, "Performance and mission")
    nominal = performance_core(st)
    t = _tick(timings, "performance_s", t)
    sets = _factor_sets(scale, ms["mass_rel"]) if uncertainty else {}
    perturbed: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for i, (name, (flo, fhi)) in enumerate(sets.items()):
        report(0.82 + 0.08 * i / max(1, len(sets)), "Uncertainty ranges")
        with_tr = name in ("mass", "ct", "cp", "motor_loss", "pack_r", "parasite", "profile")
        perturbed[name] = (performance_core(st, flo, with_tr), performance_core(st, fhi, with_tr))
    t = _tick(timings, "uncertainty_s", t)

    def oat(
        get: Callable[[dict[str, Any]], float], factors: list[str]
    ) -> tuple[float, list[tuple[float, float]]]:
        nom = get(nominal)
        pert = [(get(perturbed[k][0]), get(perturbed[k][1])) for k in factors if k in perturbed]
        return nom, pert

    all_f = list(sets.keys())
    drag_f = ["profile", "parasite", "induced", "mass"]
    nmax = nominal["max"]

    # ----- Mass and balance -----
    mass = ms["result"]
    cg_max, cg_min = ms["cg_max_x"], ms["cg_min_x"]
    cr_max, cr_min, hi = st["avl"]["cases"]
    x_np = cr_max["stab"]["neutral point"] * 1000
    x_np_min = cr_min["stab"]["neutral point"] * 1000
    mac = w["mac_mm"]
    sm_max = (x_np - cg_max) / mac
    sm_min = (x_np_min - cg_min) / mac
    # Neutral point: AVL's slender-body fuselage and Multhopp's strip method (Tier 1) differ by up
    # to ~10 % MAC on short, wide fuselages; the engine keeps AVL's (forward, conservative) value
    # with +/-4 % MAC (estimate).
    np_unc = 0.04 * mac
    balance = {
        "cg_max_payload_x": q_abs(
            cg_max,
            ms["cg_max_sigma"],
            "mm",
            "Balance point (heaviest camera)",
            "Where the aircraft balances, measured from the nose.",
            "Mass-weighted average of the component positions.",
        ),
        "cg_min_payload_x": q_abs(
            cg_min,
            ms["cg_min_sigma"],
            "mm",
            "Balance point (lightest camera)",
            "The balance point moves back with a lighter camera.",
            "Mass-weighted average of the component positions.",
        ),
        "neutral_point_x": q_abs(
            x_np,
            np_unc,
            "mm",
            "Neutral point (AVL)",
            "The balance point at which the aircraft would be neither stable "
            "nor unstable in pitch. The real balance point must be ahead of it.",
            "AVL vortex lattice with the fuselage as a slender body (Xnp); "
            "+/-4 % MAC (AVL body model vs other fuselage methods, estimate).",
        ),
        "static_margin_max_payload": q_abs(
            sm_max * 100,
            rss([np_unc / mac * 100, ms["cg_max_sigma"] / mac * 100]),
            "% MAC",
            "Static margin (heaviest camera)",
            "How far the balance point is ahead of the neutral point, "
            "in % of the wing chord. Positive is stable; 5-20 % is "
            "the usual target.",
            "(x_np - x_cg) / MAC with AVL's neutral point (Nelson ch. 2).",
        ),
        "static_margin_min_payload": q_abs(
            sm_min * 100,
            rss([np_unc / mac * 100, ms["cg_min_sigma"] / mac * 100]),
            "% MAC",
            "Static margin (lightest camera)",
            "Stability is lowest with the lightest camera because the balance point moves back.",
            "(x_np - x_cg) / MAC with AVL's neutral point.",
        ),
        "hover_front_share": q_exact(
            ms["front_share_max"],
            "",
            "Front motors' share of hover load",
            "Fraction of the weight the front pair carries in hover (0.5 is even).",
            "Moment balance of the two motor pairs about the CG.",
        ),
    }

    # ----- Aerodynamics -----
    mr = ms["mass_rel"]
    stall = st["stall"]
    vs_rel = rss_powers([(0.5, mr), (0.5, 0.10)])
    cl_cruise = nmax["cl"]
    l_d = oat(lambda r: r["max"]["lift_to_drag"], drag_f)
    d_n = oat(lambda r: r["max"]["drag_n"], drag_f)
    sd = cr_max["stab"]
    cd0_total = st["cd_parasite"] + st["cd_profile_max"]
    aero = {
        "wing_area": q_exact(
            w["area_m2"],
            "m²",
            "Wing area",
            "The area of the wing seen from above.",
            "Trapezoidal planform: span x (root + tip chord) / 2.",
        ),
        "aspect_ratio": q_exact(
            w["aspect_ratio"],
            "",
            "Aspect ratio",
            "Span compared with average chord. Long, slender wings make less drag from lift.",
            "Span² / wing area.",
        ),
        "wing_loading": q_rel(
            st["mass_max_kg"] / w["area_m2"],
            mr,
            "kg/m²",
            "Wing loading",
            "Weight carried per square metre of wing.",
            "Take-off mass (heaviest camera) / wing area.",
        ),
        "reynolds_cruise": q_exact(
            st["polars"]["wing MAC"].re,
            "",
            "Reynolds number (cruise, MAC)",
            "How 'big and fast' the wing is to the air; below about 200,000 "
            "airfoil choice matters more.",
            "rho V c / mu, sea-level ISA.",
        ),
        "lift_curve_slope": q_rel(
            sd["dCL/dalpha"],
            0.05,
            "/rad",
            "Lift-curve slope (AVL)",
            "How quickly the lift grows with angle of attack, whole aircraft.",
            "AVL vortex lattice, section slope scaled by XFOIL (CLAF); +/-5 %.",
        ),
        "cl_max": q_rel(
            stall["cl_max"],
            0.10,
            "",
            "Maximum lift coefficient (trimmed)",
            "The most lift the wing gives before the first part of it stalls.",
            "Critical-section method: AVL local lift vs XFOIL section cl_max at the "
            "local stall Reynolds number; +/-10 %.",
        ),
        "stall_speed": q_rel(
            stall["stall_speed_mps"],
            vs_rel,
            "m/s",
            "Stall speed",
            f"The slowest the wing can hold the aircraft up "
            f"({stall['stall_speed_mps'] * 3.6:.0f} km/h) with the heaviest camera.",
            "V_s = sqrt(2 W / (rho S CL_max)) with the critical-section CL_max.",
        ),
        "cl_cruise": q_rel(
            cl_cruise,
            mr,
            "",
            "Cruise lift coefficient",
            "How hard the wing works at cruise speed.",
            "CL = W / (q S).",
        ),
        "cruise_to_stall": q_rel(
            v / stall["stall_speed_mps"],
            vs_rel,
            "",
            "Cruise / stall speed",
            "How many times faster than the stall the aircraft cruises.",
            "Cruise speed / stall speed.",
        ),
        "cd_induced": q_rel(
            st["cdi_max"],
            rss([0.05, 2 * mr]),
            "",
            "Induced drag coefficient (AVL)",
            "Drag that comes from making lift, including the tail's trim load.",
            "AVL Trefftz-plane induced drag at the trimmed cruise point; +/-5 %.",
        ),
        "span_efficiency": q_abs(
            st["span_efficiency"],
            0.03,
            "",
            "Span efficiency (AVL)",
            "How close the lift distribution comes to the ideal (1.0).",
            "AVL Trefftz-plane span efficiency, trimmed.",
        ),
        "cd_profile": q_asym(
            st["cd_profile_max"],
            *drg.PROFILE_UNCERTAINTY[scale],
            "",
            "Wing and tail profile drag coefficient",
            "Friction and shape drag of the wing and tail sections.",
            "XFOIL polars strip-integrated with AVL local lift (incl. 10 % leakage).",
        ),
        "cd_parasite": q_rel(
            st["cd_parasite"],
            drg.PARASITE_UNCERTAINTY,
            "",
            "Parasite drag coefficient (fuselage, booms, rotors, gear)",
            "Drag of everything that is not wing or tail.",
            "Raymer component build-up and Hoerner drag areas (incl. 10 % leakage).",
        ),
        "cd0": q_rel(
            cd0_total,
            0.15,
            "",
            "Zero-lift drag coefficient",
            "Profile plus parasite drag at the cruise point.",
            "Sum of the build-up.",
        ),
        "lift_to_drag": _q_oat(
            *l_d,
            "",
            "Lift-to-drag ratio (cruise)",
            "Newtons of lift per newton of drag at cruise.",
            "CL / (CD_profile + CD_parasite + CD_induced).",
        ),
        "drag_cruise": _q_oat(
            *d_n,
            "N",
            "Cruise drag",
            "The force the propellers must overcome.",
            "q S CD at the trimmed cruise point.",
        ),
        "trim_elevator_max_payload": q_abs(
            cr_max["elevator_deg"] or float("nan"),
            1.0,
            "°",
            "Elevator for trim (heaviest camera)",
            "Elevator angle needed to fly level at cruise; small is "
            "good (positive = trailing edge down).",
            "AVL trim to Cm = 0 about the CG.",
        ),
        "trim_elevator_min_payload": q_abs(
            cr_min["elevator_deg"] or float("nan"),
            1.0,
            "°",
            "Elevator for trim (lightest camera)",
            "Elevator angle needed to fly level with the lightest camera.",
            "AVL trim to Cm = 0 about the CG.",
        ),
        "alpha_cruise": q_abs(
            cr_max["alpha_deg"] or float("nan"),
            0.5,
            "°",
            "Cruise angle of attack",
            "Fuselage angle to the airflow in cruise.",
            "AVL trimmed.",
        ),
        "tail_volume_h": q_exact(
            g["tail"]["horizontal_volume_coefficient"],
            "",
            "Horizontal tail volume",
            "Tail area x arm relative to wing area x chord; 0.3-0.8 is usual.",
            "S_h l_t / (S c), Raymer ch. 6.",
        ),
        "tail_volume_v": q_exact(
            g["tail"]["vertical_volume_coefficient"],
            "",
            "Vertical tail volume",
            "Fin area x arm relative to wing area x span; at least 0.02.",
            "S_v l_v / (S b), Raymer ch. 6.",
        ),
    }
    stability = {
        "CL_alpha": sd.get("dCL/dalpha"),
        "Cm_alpha": sd.get("dCm/dalpha"),
        "Cn_beta": sd.get("dCn'/dbeta"),
        "Cl_beta": sd.get("dCl'/dbeta"),
        "CY_beta": sd.get("dCY/dbeta"),
        "Cm_q": sd.get("dCm/dq'"),
        "Cl_p": sd.get("dCl'/dp'"),
        "Cn_r": sd.get("dCn'/dr'"),
        "Cm_elevator_per_deg": cr_max["control_derivs"].get("dCm/delevator"),
        "neutral_point_x_mm": x_np,
        "source": "AVL stability-axis derivatives at the trimmed "
        "cruise point (heaviest camera), per radian unless stated.",
    }

    # ----- Propulsion and battery -----
    pack = st["pack_nominal"]
    lift_motor = st["lift_motor"]
    lift_prop = st["lift_prop"]
    max_static = prp.max_thrust(
        lift_prop,
        lift_motor,
        0.0,
        bat.loaded_voltage(pack, 4 * lift_motor.i_max_a * pack["v_nominal"] / prp.ETA_ESC)[
            "voltage_v"
        ],
    )
    weight_max = st["mass_max_kg"] * G0
    share = clamp(ms["front_share_max"], 0, 1)
    tw_total = 4 * max_static["thrust_n"] / weight_max
    busier = max(share, 1 - share)
    tw_pair = 2 * max_static["thrust_n"] / (weight_max * busier) / 2 if busier > 0 else float("inf")
    hp = oat(lambda r: r["max"]["hover_power_w"], ["mass", "ct", "cp", "motor_loss", "pack_r"])
    cp_ = oat(lambda r: r["max"]["cruise_power_w"], [*drag_f, "ct", "cp", "motor_loss", "pack_r"])
    hc = oat(lambda r: r["max"]["hover_current_a"], ["mass", "ct", "cp", "motor_loss", "pack_r"])
    tr_nom = nmax["transition"]
    peak_i = oat(
        lambda r: (r["max"]["transition"] or {}).get("peak_current_a", r["max"]["hover_current_a"]),
        ["mass", "ct", "cp", "motor_loss", "pack_r"],
    )
    cop = nmax["cruise_op"]
    ops_h = nmax["hover_ops"]
    propulsion = {
        "lift_propeller": lift_prop.to_dict(),
        "lift_motor": lift_motor.to_dict(),
        "pusher_propeller": st["pusher_prop"].to_dict() if st["pusher_prop"] else None,
        "pusher_motor": st["pusher_motor"].to_dict() if st["pusher_motor"] else None,
        "hover": {name: _op_summary(op) for name, op in ops_h.items()},
        "cruise": _op_summary(cop),
        "cruise_rotors": 1 if p["layout"] == "quad_pusher" else 2,
        "hover_power": _q_oat(
            *hp,
            "W",
            "Hover power",
            "Electrical power to hover with the heaviest camera, including avionics.",
            "Motor model (Kv, R, I0) matched to the propeller CT, CP at each pair's "
            "share of the weight (+3 % download), ESC 0.95, battery sag included.",
        ),
        "cruise_power": _q_oat(
            *cp_,
            "W",
            "Cruise power",
            "Electrical power drawn from the battery in level wing flight, including avionics.",
            "Cruise drag x speed through the propeller CT(J), CP(J) and motor "
            "model at the cruise advance ratio; ESC 0.95; avionics "
            f"{st['avionics_w']:g} W.",
        ),
        "cruise_propeller_efficiency": q_rel(
            cop["eta_prop"],
            0.15,
            "",
            "Cruise propeller efficiency",
            "Share of the shaft power that becomes useful thrust power in cruise.",
            "J CT / CP at the cruise operating point (generic UIUC-trend propeller).",
        ),
        "cruise_throttle": q_rel(
            cop["throttle"],
            0.1,
            "",
            "Cruise throttle",
            "Fraction of full throttle needed in cruise.",
            "Motor model.",
        ),
        "hover_throttle": q_rel(
            max(o["throttle"] for o in ops_h.values()),
            0.1,
            "",
            "Hover throttle (busier pair)",
            "Throttle of the motor pair carrying more weight in hover. Around "
            "0.5-0.75 leaves room for control.",
            "Motor model.",
        ),
        "hover_thrust_to_weight": q_rel(
            tw_total,
            rss([prp.PROP_UNCERTAINTY, mr]),
            "",
            "Hover thrust-to-weight",
            "Full-throttle thrust of the four lift motors divided by the "
            "weight. Generic motors are sized to the Settings minimum "
            "(assumed until real parts in Phase 4).",
            "Motor model at full throttle on the loaded pack voltage.",
        ),
        "hover_thrust_to_weight_busier_pair": tw_pair,
        "max_static_thrust_per_motor_n": max_static["thrust_n"],
        "cruise_thrust_available_n": nmax["cruise_thrust_available_n"],
        "cruise_thrust_required_n": nmax["cruise_thrust_required_n"],
        "parts_note": st["parts_note"],
    }
    peak_lv = bat.loaded_voltage(
        pack, (tr_nom or {}).get("peak_power_w", nmax["hover_power_w"]), pack["v_reserve_ocv"]
    )
    t_rise = bat.temperature_rise_k(pack, nmax["hover_current_a"], 90) + (
        bat.temperature_rise_k(pack, peak_lv["current_a"], 2 * (tr_nom or {}).get("duration_s", 15))
    )
    battery = {
        "pack": {k: v for k, v in pack.items()},
        "nominal_voltage": q_exact(
            pack["v_nominal"],
            "V",
            "Pack nominal voltage",
            "Cells in series x nominal cell voltage.",
            bat.SOURCES["nominal"],
        ),
        "internal_resistance": q_rel(
            pack["r_pack_ohm"] * 1000,
            bat.RESISTANCE_UNCERTAINTY,
            "mΩ",
            "Pack internal resistance",
            "Resistance inside the pack that "
            "turns some current into heat and makes the voltage sag.",
            bat.SOURCES["resistance"],
        ),
        "hover_current": _q_oat(
            *hc,
            "A",
            "Hover battery current",
            "Current drawn from the battery while hovering (heaviest camera).",
            "Hover electrical power / loaded pack voltage.",
        ),
        "hover_voltage": q_exact(
            nmax["hover_voltage_v"],
            "V",
            "Pack voltage in hover",
            "Voltage at the battery terminals while hovering (mid-charge).",
            "V = Voc - I R.",
        ),
        "peak_current": _q_oat(
            *peak_i,
            "A",
            "Peak battery current",
            "Highest current, during the transition at the end of the flight "
            "when the pack is lowest.",
            "Transition model, reserve-point voltage.",
        ),
        "peak_voltage_per_cell": q_exact(
            peak_lv["voltage_v"] / pack["cells_series"],
            "V",
            "Cell voltage at peak demand",
            "Loaded cell voltage at the peak current with the reserve "
            "left. Below about 3.3 V (LiPo) or 3.0 V (Li-ion) the "
            "autopilot's low-voltage failsafe may trigger.",
            bat.SOURCES["ocv"],
        ),
        "continuous_rating_a": pack["i_continuous_a"],
        "burst_rating_a": pack["i_burst_a"],
        "rating_source": bat.SOURCES["rating"],
        "peak_c_rate": peak_lv["current_a"] / pack["capacity_ah"],
        "temperature_rise_k": t_rise,
        "temperature_note": (
            f"Internal heating raises the cells by about {t_rise:.0f} °C over the hover and "
            "transition phases (I²R heating, adiabatic). Keep LiPo packs below about 60 °C; in "
            "cold "
            "weather (below 10 °C) warm the pack first because its resistance roughly doubles."
        ),
        "usable_energy": q_rel(
            nmax["usable_wh"],
            0.05,
            "Wh",
            "Usable energy",
            "Energy you can use before landing with the reserve still in the pack.",
            f"Nominal x (1 - {settings['checks']['battery_reserve_fraction']:g} reserve) x 0.95.",
        ),
        "energy": q_rel(
            pack["energy_wh"],
            0.05,
            "Wh",
            "Battery energy",
            "Energy stored in the full pack.",
            "Cells x nominal voltage x capacity.",
        ),
    }

    # ----- Mission and endurance -----
    def mission_block(case: str) -> dict[str, Any]:
        r = nominal[case]
        tr = r["transition"] or {}
        segs = [
            {
                "key": "takeoff_hover",
                "label": "Take-off hover",
                "duration_s": MISSION_PROFILE["takeoff_hover_s"],
                "power_w": r["hover_power_w"],
                "energy_wh": r["hover_wh"] / 2,
            },
            {
                "key": "transition_out",
                "label": "Transition to wing flight",
                "duration_s": r["transition_s"],
                "power_w": tr.get("mean_power_w", r["transition_power_w"]),
                "energy_wh": r["transition_wh"] / 2,
            },
            {
                "key": "cruise",
                "label": "Cruise",
                "duration_s": r["cruise_s"],
                "power_w": r["cruise_power_w"],
                "energy_wh": r["cruise_wh"],
            },
            {
                "key": "transition_in",
                "label": "Transition back to hover",
                "duration_s": r["transition_s"],
                "power_w": tr.get("mean_power_w", r["transition_power_w"]),
                "energy_wh": r["transition_wh"] / 2,
            },
            {
                "key": "landing_hover",
                "label": "Landing hover",
                "duration_s": MISSION_PROFILE["landing_hover_s"],
                "power_w": r["hover_power_w"],
                "energy_wh": r["hover_wh"] / 2,
            },
        ]
        return {
            "segments": segs,
            "usable_wh": r["usable_wh"],
            "vtol_wh": r["vtol_wh"],
            "mass_kg": r["mass_kg"],
        }

    end = oat(lambda r: r["max"]["cruise_s"] / 60, all_f)
    end_min = oat(lambda r: r["min"]["cruise_s"] / 60, all_f)
    tot = oat(lambda r: r["max"]["total_s"] / 60, all_f)
    rng = oat(lambda r: r["max"]["range_m"] / 1000, all_f)
    endurance_src = (
        "Usable energy minus hover and transition energy (pack losses included), / "
        "cruise battery power; range from one-at-a-time factors: profile drag, parasite "
        "drag, induced drag, mass, battery energy, propeller CT and CP, motor losses, "
        "pack resistance (root-sum-square)."
    )
    performance = {
        "endurance_cruise": _q_oat(
            *end,
            "min",
            "Wing-flight endurance",
            "Minutes of cruise on the wing after take-off, transitions and "
            "landing are paid for, keeping the battery reserve.",
            endurance_src,
        ),
        "endurance_cruise_min_payload": _q_oat(
            *end_min,
            "min",
            "Wing-flight endurance (lightest camera)",
            "Wing-flight endurance with the lightest camera.",
            endurance_src,
        ),
        "endurance_total": _q_oat(
            *tot,
            "min",
            "Total flight time",
            "Wing-flight time plus take-off, transitions and landing.",
            "Cruise time + VTOL phase durations.",
        ),
        "range": _q_oat(
            *rng,
            "km",
            "Range (still air)",
            "Distance covered in the wing-flight time at cruise speed with no wind.",
            "Cruise speed x wing-flight endurance.",
        ),
        "vtol_energy": q_rel(
            nmax["vtol_wh"],
            0.15,
            "Wh",
            "Energy for take-off, transitions and landing",
            "Energy for 45 s hover at each end and two modelled transitions.",
            "Hover power x time + transition model energy, pack losses included.",
        ),
        "a3_note": A3_NOTE,
    }

    # ----- Structure -----
    report(0.92, "Structure checks")
    sf = settings["checks"]["structural_safety_factor"]
    n_man = settings["checks"]["manoeuvre_load_factor"]
    spar_detail = ms["spar"]
    wing_check = stc.wing_spar_check(
        g, spar_detail, wing_strips(hi), weight_max, _wing_share(hi), n_man, sf
    )
    boom = stc.boom_check(p, g, max_static["thrust_n"], weight_max, sf)
    boom["min_diameter_mm"] = stc.min_boom_diameter(p, g, max_static["thrust_n"], weight_max, sf)
    structure = {"wing_spar": wing_check, "boom": boom, "spar_sizing": st["spar_sizing"]}
    t = _tick(timings, "structure_s", t)

    # ----- Transition summary -----
    transition = None
    if tr_nom:
        mm = oat(
            lambda r: (r["max"]["transition"] or {}).get("min_thrust_margin", float("nan")),
            ["mass", "ct", "cp", "motor_loss", "pack_r", "parasite"],
        )
        transition = {
            **{k: v for k, v in tr_nom.items()},
            "min_margin": _q_oat(
                *mm,
                "",
                "Minimum transition thrust margin",
                "Available thrust divided by the thrust needed, at the "
                "worst speed of the transition. 1.0 means no reserve.",
                "Quasi-steady transition sweep 0-1.3 x cruise speed.",
            ),
        }

    # ----- CG envelope sweep (five payloads) -----
    envelope = []
    pl_lo, pl_hi = mission["payload_min_g"], mission["payload_max_g"]
    for i in range(5):
        pl = pl_lo + (pl_hi - pl_lo) * i / 4
        x, tot_g = _cg_payload(ms, pl)
        f = (x - cg_min) / (cg_max - cg_min) if abs(cg_max - cg_min) > 1e-9 else 0.0
        xnp_i = x_np_min + f * (x_np - x_np_min)
        share_i = (g["rear_rotor_x_mm"] - x) / (g["rear_rotor_x_mm"] - g["front_rotor_x_mm"])
        envelope.append(
            {
                "payload_g": pl,
                "cg_x_mm": x,
                "mass_kg": tot_g / 1000,
                "static_margin": (xnp_i - x) / mac,
                "front_share": share_i,
            }
        )

    summary = {
        "takeoff_mass": mass["takeoff_max_payload"],
        "empty_mass": mass["empty"],
        "endurance_cruise": performance["endurance_cruise"],
        "endurance_cruise_min_payload": performance["endurance_cruise_min_payload"],
        "endurance_total": performance["endurance_total"],
        "range": performance["range"],
        "cruise_power": propulsion["cruise_power"],
        "hover_power": propulsion["hover_power"],
        "stall_speed": aero["stall_speed"],
        "cruise_to_stall": aero["cruise_to_stall"],
        "lift_to_drag": aero["lift_to_drag"],
        "static_margin_max_payload": balance["static_margin_max_payload"],
        "static_margin_min_payload": balance["static_margin_min_payload"],
        "hover_thrust_to_weight": propulsion["hover_thrust_to_weight"],
        "peak_current": battery["peak_current"],
    }
    if transition:
        summary["transition_min_margin"] = transition["min_margin"]

    # ----- Tier 1 comparison -----
    table_wing = st["table_wing"] or st["polars"]["wing MAC"]
    table_tail = (
        st["store"].table_polar(g["tail"]["airfoil"], st["polars"]["tail"].re)
        or st["polars"]["tail"]
    )
    t1_cd0 = sum(
        i["drag_area_m2"]
        for i in st["drag"]["items"]
        if i["key"] not in ("wing", "tail", "leakage")
    )
    fr = drg.tier1_lifting_friction(g, v, scale)
    t1_cd0 = (t1_cd0 / w["area_m2"] + fr["wing"] + fr["tail"]) * 1.1
    t1 = tier1_reference(g, table_wing, table_tail, st["mass_max_kg"], v, t1_cd0)
    comparison = _tier1_comparison(
        st, t1, aero, balance, propulsion, performance, nmax, x_np, sm_max, sd
    )

    # ----- Checks -----
    report(0.96, "Checks")
    check_state = {
        "p": p,
        "g": g,
        "mission": mission,
        "settings": settings,
        "mass_max_kg": st["mass_max_kg"],
        "mass_high_kg": mass["takeoff_max_payload"]["high"],
        "sm_max": sm_max,
        "sm_min": sm_min,
        "envelope": envelope,
        "front_share_max": ms["front_share_max"],
        "front_share_min": ms["front_share_min"],
        "cruise_to_stall": v / stall["stall_speed_mps"],
        "stall_speed": stall["stall_speed_mps"],
        "tw_total": tw_total,
        "tw_pair": tw_pair,
        "hover_current": nmax["hover_current_a"],
        "peak_current": peak_lv["current_a"],
        "peak_cell_v": peak_lv["voltage_v"] / pack["cells_series"],
        "pack": pack,
        "transition": tr_nom,
        "spar": wing_check,
        "boom": boom,
        "trim_max": cr_max["elevator_deg"],
        "trim_min": cr_min["elevator_deg"],
        "trim_ok": cr_max["trim_converged"] and cr_min["trim_converged"],
        "cruise_thrust_available": nmax["cruise_thrust_available_n"],
        "cruise_thrust_required": nmax["cruise_thrust_required_n"],
        "cruise_throttle": cop["throttle"],
        "endurance": performance["endurance_cruise"],
        "vtol_exceeds_usable": nmax["vtol_exceeds_usable"],
        "cn_beta": sd.get("dCn'/dbeta"),
        "cl_beta": sd.get("dCl'/dbeta"),
        "hover_throttle": max(o["throttle"] for o in ops_h.values()),
        "geometry_statuses": g["statuses"],
        "mass_converged": mass["converged"],
        "spar_sizing": st["spar_sizing"],
    }
    checks = chk.build_checks(check_state, settings_meta)

    notes = list(g["statuses"])
    for name, pol in st["polars"].items():
        if pol.source != "xfoil":
            notes.append(
                status(
                    f"polars.{name.replace(' ', '_')}",
                    f"Airfoil data: {name}",
                    "info",
                    f"{pol.airfoil} at Re {round(pol.re / 1000)}k: {pol.note}.",
                )
            )
    for case, pr_ in st["prof"].items():
        if pr_["wing"]["strips_outside_polar"]:
            notes.append(
                status(
                    f"polars.outside_{case}",
                    "Wing lift beyond the airfoil data",
                    "warn",
                    f"{pr_['wing']['strips_outside_polar']} wing strips work beyond the "
                    "converged XFOIL polar at cruise; their drag is the last converged "
                    "value, so profile drag may be underestimated.",
                )
            )
    if st["mode"] == "fast":
        notes.append(
            status(
                "analysis.fast",
                "Quick analysis",
                "info",
                "Quick analysis: cached or tabulated airfoil polars were used and XFOIL "
                "was not run; numbers can differ slightly from a full analysis.",
            )
        )
    body = st["avl"].get("body", {}).get("Fuselage", {})
    boom_vol = (
        2 * math.pi * (p["booms"]["diameter_mm"] / 2000) ** 2 * p["booms"]["length_mm"] / 1000
    )
    assumptions = [
        "Sea-level standard air (1.225 kg/m³, 15 °C), still air.",
        f"AVL vortex lattice: wing {avl_model.WING_NCHORD} x {avl_model.WING_NSPAN} vortices "
        "per side, tail 6 x 10; fuselage as a slender body; "
        f"booms and motor pods not modelled in AVL (boom volume {boom_vol * 1e6:.0f} cm³ is "
        f"{(boom_vol / body['volume'] * 100) if body.get('volume') else float('nan'):.0f} % of the "
        "fuselage's); no propeller slipstream.",
        f"XFOIL polars at Ncrit {settings['analysis']['ncrit']:g}; profile drag "
        "strip-integrated with "
        "AVL local lift; laminar-flow assumptions in XFOIL suit a smooth, sanded and painted "
        "surface.",
        "Generic propellers fitted to UIUC propeller-database trends (+/-15 % CT, CP) and generic "
        "motors (Kv, R, I0) sized to the hover thrust-to-weight minimum: assumed until Phase 4 "
        "parts.",
        "Battery: per-cell internal resistance and open-circuit voltage by chemistry (estimates), "
        "discharge ratings are placeholders until Phase 4 packs.",
        f"Mission: 45 s take-off hover, modelled transitions ({trn.ACCELERATION:g} m/s² to "
        "cruise), "
        f"cruise, modelled transition back, 45 s landing hover, "
        f"{settings['checks']['battery_reserve_fraction'] * 100:g} % reserve, 95 % of the "
        "remaining "
        "nominal energy usable.",
        "Mass model: the Tier 1 build-up (docs/ENGINE.md) "
        + (
            "with the wing spar tube sized by the structure check."
            if scale == "prototype"
            else "with bending-sized spar caps."
        ),
        f"Structure: manoeuvre load factor {n_man:g} x safety factor {sf:g}; carbon tube and cap "
        "properties are estimates until Phase 4 catalogue parts.",
    ]
    _tick(timings, "assembly_s", t)
    return {
        "schema": RESULT_SCHEMA,
        "engine_version": ENGINE_VERSION,
        "valid": True,
        "mode": st["mode"],
        "layout": p["layout"],
        "summary": summary,
        "geometry": _geometry_summary(g),
        "mass": {
            "components": mass["components"],
            "empty": mass["empty"],
            "battery": mass["battery"],
            "structure": mass["structure"],
            "takeoff_max_payload": mass["takeoff_max_payload"],
            "takeoff_min_payload": mass["takeoff_min_payload"],
            "structure_fraction": mass["structure_fraction"],
            "converged": mass["converged"],
            "iterations": mass["iterations"],
        },
        "balance": {**balance, "cg_envelope": envelope},
        "aero": {
            **aero,
            "stability_derivatives": stability,
            "stall": stall,
            "span_loading": [
                {
                    "y_mm": s["y"] * 1000,
                    "chord_mm": s["chord"] * 1000,
                    "cl": s["cl"],
                    "ccl_m": s["ccl"],
                }
                for s in wing_strips(cr_max)
                if s["y"] >= 0
            ],
            "polars": {k: v.to_dict() for k, v in st["polars"].items()},
            "avl_cases": [_avl_summary(c) for c in st["avl"]["cases"]],
            "claf": st["claf"],
        },
        "drag": {
            "items": st["drag"]["items"],
            "cd_total_cruise": nmax["cd"],
            "cd_induced": st["cdi_max"],
            "cd_profile": st["cd_profile_max"],
            "cd_parasite": st["cd_parasite"],
        },
        "propulsion": propulsion,
        "battery": battery,
        "transition": transition,
        "structure": structure,
        "mission": {
            "payload_max": mission_block("max"),
            "payload_min": mission_block("min"),
            "profile": MISSION_PROFILE,
        },
        "performance": performance,
        "checks": checks,
        "notes": sort_statuses(notes),
        "tier1_comparison": comparison,
        "assumptions": assumptions,
        "polar_log": st["store"].log,
        "a3_note": A3_NOTE,
        "inputs": {"parameters": p, "mission": mission, "settings": settings},
    }


def _cg_payload(ms: dict[str, Any], payload_g: float) -> tuple[float, float]:
    from app.engine.mass import cg_with_payload

    return cg_with_payload(ms, payload_g)


def _op_summary(op: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "rpm",
        "j",
        "thrust_n",
        "torque_nm",
        "shaft_power_w",
        "current_a",
        "motor_voltage_v",
        "motor_power_w",
        "battery_power_w",
        "eta_motor",
        "eta_prop",
        "throttle",
        "bus_voltage_v",
        "feasible",
    )
    return {k: op.get(k) for k in keys}


def _avl_summary(c: dict[str, Any]) -> dict[str, Any]:
    tot = c["totals"]
    return {
        "name": c["case"]["name"],
        "x_cg_mm": c["case"]["xref"] * 1000,
        "cl": tot.get("CL"),
        "cdi": tot.get("CDff"),
        "e": tot.get("e"),
        "cm": tot.get("Cm"),
        "alpha_deg": c.get("alpha_deg"),
        "elevator_deg": c.get("elevator_deg"),
        "trim_converged": c.get("trim_converged"),
        "neutral_point_mm": (c["stab"].get("neutral point") or float("nan")) * 1000,
    }


def _geometry_summary(g: dict[str, Any]) -> dict[str, Any]:
    w, t, f = g["wing"], g["tail"], g["fuselage"]
    return {
        "wing": {
            k: w[k]
            for k in (
                "span_mm",
                "area_m2",
                "aspect_ratio",
                "taper_ratio",
                "mac_mm",
                "mac_x_le_mm",
                "ac_x_mm",
                "thickness_ratio",
                "airfoil",
            )
        },
        "tail": {
            k: t[k]
            for k in (
                "type",
                "planform_area_m2",
                "horizontal_area_m2",
                "vertical_area_m2",
                "quarter_chord_x_mm",
                "horizontal_volume_coefficient",
                "vertical_volume_coefficient",
            )
        },
        "fuselage": {k: f[k] for k in ("length_mm", "wetted_area_m2", "fineness_ratio")},
        "front_rotor_x_mm": g["front_rotor_x_mm"],
        "rear_rotor_x_mm": g["rear_rotor_x_mm"],
    }


def _tier1_comparison(
    st: dict[str, Any],
    t1: dict[str, float],
    aero: dict[str, Any],
    balance: dict[str, Any],
    propulsion: dict[str, Any],
    performance: dict[str, Any],
    nmax: dict[str, Any],
    x_np: float,
    sm_max: float,
    sd: dict[str, Any],
) -> list[dict[str, Any]]:
    """Tier 1 (browser) vs Tier 2 (this analysis) for the key numbers, with the reason."""
    g = st["g"]
    w = g["wing"]
    mac = w["mac_mm"]
    ms = st["ms"]
    v = st["mission"]["cruise_speed_mps"]
    weight = st["mass_max_kg"] * G0
    # Tier 1 powers: fixed efficiencies (docs/ENGINE.md "Performance").
    eta_prop = 0.75 if st["p"]["layout"] == "quad_pusher" else 0.65
    t1_cruise = t1["drag_n"] * v / (eta_prop * 0.85 * 0.95) + st["avionics_w"]
    share = clamp(ms["front_share_max"], 0, 1)
    disc = math.pi * (st["p"]["propulsion"]["prop_diameter_mm"] / 2000) ** 2
    from app.engine.mass import ideal_hover_power

    t = weight * 1.03
    t1_hover = (
        2 * ideal_hover_power(t * share / 2, disc)
        + 2 * ideal_hover_power(t * (1 - share) / 2, disc)
    ) / 0.65 / (0.85 * 0.95) + st["avionics_w"]
    rows = []

    def row(key: str, label: str, a: float, b: float, unit: str, why: str) -> None:
        diff = (
            (b - a) / abs(a) * 100
            if a not in (0, None) and math.isfinite(a) and math.isfinite(b)
            else None
        )
        rows.append(
            {
                "key": key,
                "label": label,
                "tier1": a,
                "tier2": b,
                "unit": unit,
                "difference_pct": diff,
                "why": why,
            }
        )

    row(
        "lift_slope",
        "Lift-curve slope",
        t1["lift_slope"],
        sd.get("dCL/dalpha") or float("nan"),
        "/rad",
        "Tier 1 uses Helmbold's formula for the wing alone; AVL solves the whole aircraft "
        "including "
        "the tail's contribution and the real planform.",
    )
    row(
        "span_efficiency",
        "Span (Oswald) efficiency",
        t1["oswald"],
        st["span_efficiency"],
        "",
        "Tier 1's Raymer fit includes viscous drag-due-to-lift in e; AVL's e is the inviscid "
        "lift-distribution efficiency (the viscous part is in the XFOIL profile drag).",
    )
    row(
        "cd_induced",
        "Induced drag coefficient",
        t1["cd_induced"],
        st["cdi_max"],
        "",
        "AVL computes the actual trimmed lift distribution (taper, twist, tail load) instead of "
        "the empirical Oswald factor.",
    )
    row(
        "cd0",
        "Zero-lift / profile + parasite drag coefficient",
        t1["cd0"],
        st["cd_parasite"] + st["cd_profile_max"],
        "",
        "Wing and tail drag now come from XFOIL at each strip's Reynolds number and local lift "
        "instead of flat-plate friction x form factor; the rest of the build-up is the same.",
    )
    row(
        "cl_max",
        "Maximum lift coefficient",
        t1["cl_max"],
        st["stall"]["cl_max"],
        "",
        "Tier 1 takes 0.9 x the section maximum; Tier 2 finds where the first part of the real "
        "span loading reaches its local section maximum (critical-section method).",
    )
    row(
        "neutral_point",
        "Neutral point",
        t1["neutral_point_x_mm"],
        x_np,
        "mm",
        "Tier 1 adds wing and tail with an empirical downwash gradient (2 CL_alpha / (pi A)) "
        "and Multhopp's "
        "fuselage strips; AVL computes the downwash at the actual tail position (an inverted V "
        "dipping towards the wing wake sees more of it) and the fuselage as a slender body.",
    )
    row(
        "static_margin",
        "Static margin (heaviest camera)",
        (t1["neutral_point_x_mm"] - ms["cg_max_x"]) / mac * 100,
        sm_max * 100,
        "% MAC",
        "Follows the neutral point difference; the balance point is the same in both.",
    )
    row(
        "lift_to_drag",
        "Lift-to-drag ratio",
        t1["lift_to_drag"],
        nmax["lift_to_drag"],
        "",
        "Combination of the drag differences above.",
    )
    row(
        "cruise_power",
        "Cruise power",
        t1_cruise,
        nmax["cruise_power_w"],
        "W",
        f"Tier 1 assumes a fixed propeller efficiency of {eta_prop}; the propeller model gives "
        f"{nmax['cruise_op']['eta_prop']:.2f} at the cruise advance ratio "
        f"J = {nmax['cruise_op']['j']:.2f}"
        + (
            " (hover propellers are lightly loaded and near their "
            "zero-thrust advance ratio in cruise)"
            if nmax["cruise_op"]["eta_prop"] < 0.5
            else ""
        )
        + ".",
    )
    row(
        "hover_power",
        "Hover power",
        t1_hover,
        nmax["hover_power_w"],
        "W",
        "Tier 1 uses a figure of merit 0.65 and fixed motor efficiency 0.85; Tier 2 uses the "
        "propeller's static coefficients (figure of merit "
        f"{st['lift_prop'].figure_of_merit():.2f}), "
        "the motor model and battery sag.",
    )
    if st.get("spar_sizing"):
        sz = st["spar_sizing"]
        from app.engine.mass import default_spar_tube

        d0 = default_spar_tube(g)
        row(
            "spar_tube",
            "Wing spar tube outer diameter",
            d0["outer_mm"],
            sz["outer_mm"],
            "mm",
            "Tier 1 guesses 70 % of the root thickness; Tier 2 sizes the lightest standard tube "
            "that passes the bending check at the manoeuvre load.",
        )
    return rows
