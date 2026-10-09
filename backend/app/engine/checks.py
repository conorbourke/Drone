"""Tier 2 checks with pass/warn/fail ("ok" / "warn" / "fail") and a plain message each.

Every check names its threshold and where the threshold comes from (the settings ``meta`` source
when the threshold is a setting; the engine rule and its basis otherwise). Status shape::

    {key, label, level, message, value, threshold, unit, threshold_source}

The brief's checks (docs/BRIEF.md "Checks with clear pass/warn/fail"; docs/phases/PHASE3.md
section 2): hover thrust-to-weight, static margin at both payloads, CG envelope over the payload
range (five steps), stall margin, battery current against rating, take-off mass against the
design and legal limits, transition thrust margin, spar and boom margins. Added because a
hobbyist has nobody else to catch them: trim deflection, cruise thrust available, endurance
against the target, directional and roll stability signs.
"""

from __future__ import annotations

import math
from typing import Any

from app.engine import transition as trn
from app.engine.avl_model import TRIM_LIMIT_DEG
from app.engine.quantity import fmt, sort_statuses, status

#: Meta for the Phase 3 settings until the settings document (schema 2) carries it.
PHASE3_META = {
    "checks.manoeuvre_load_factor": {
        "label": "Manoeuvre load factor",
        "source": "Limit load factor for the structure checks: 3.0 g, a common small-UAV design "
        "value (CS-23 normal category uses 3.8 g for light aircraft; Gundlach 2014 ch. 9 gives "
        "3-4 g "
        "for small UAVs); proposed in Phase 3.",
    },
    "checks.structural_safety_factor": {
        "label": "Structural safety factor",
        "source": "Ultimate = 1.5 x limit load (CS-23.303 / FAR 23.303 factor of safety); "
        "proposed in Phase 3.",
    },
    "checks.transition_thrust_margin_min": {
        "label": "Minimum transition thrust margin",
        "source": "Available / required thrust of at least 1.3 through the transition, leaving "
        "control authority for gusts and attitude control (engine rule; proposed in Phase 3).",
    },
    "analysis.ncrit": {
        "label": "XFOIL transition parameter Ncrit",
        "source": "Drela, XFOIL user guide: 9 for an average wind tunnel / clean air.",
    },
}
#: Hover balance: the busier motor pair should carry at most 65 % (Tier 1 rule, engine
#: decision: keeps at least ~35 % of control authority on that pair at the T/W minimum).
HOVER_SHARE_WARN = 0.65


def _src(meta: dict[str, Any], path: str) -> str:
    m = meta.get(path) or PHASE3_META.get(path) or {}
    label = m.get("label", path)
    return f"Settings: {label} ({path}). {m.get('source', '')}".strip()


def _top_speed_message(top: dict[str, Any]) -> str:
    fm = top["margin_min"]
    end = top["sweep_end_speed_mps"]
    if fm is None:
        return (
            "The wing never carries the full weight within the sweep, so there is no "
            "wing-borne flight to check."
        )
    msg = (
        f"In wing-borne flight the {top['propeller']} give at least {fm:.2f} x the forward "
        f"force needed (lowest at {top['margin_min_speed_mps']:.1f} m/s; "
        f"{top['margin_at_sweep_end']:.2f} x at {end:.1f} m/s, {trn.SWEEP_END_FACTOR:g} x cruise)."
    )
    v_top = top["top_speed_mps"]
    if v_top is not None:
        msg += f" Estimated top speed {v_top:.1f} m/s at full throttle."
    else:
        msg += f" Top speed above {2 * end:.0f} m/s (not limited by thrust in this model)."
    j, j0 = top.get("advance_ratio_at_sweep_end"), top.get("zero_thrust_advance_ratio")
    if top["level"] != "ok":
        msg += (
            " What limits it: propeller unloading. At full throttle at "
            f"{end:.1f} m/s the propellers run at advance ratio J = V / (n D) = "
            + (f"{j:.2f}" if j is not None else "n/a")
            + (
                f", close to J = {j0:.2f} where a propeller of this pitch gives no thrust"
                if j0
                else ""
            )
            + "; low-pitch hover propellers cannot push much faster than this. Use a "
            "higher-pitch cruise propeller (or a pusher), a higher-Kv or higher-voltage "
            "motor, or less drag."
        )
    return msg


def build_checks(
    s: dict[str, Any], settings_meta: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    from app.defaults import SETTINGS_META

    meta = {**SETTINGS_META, **(settings_meta or {})}
    c = s["settings"]["checks"]
    lim = s["settings"]["limits"]
    out: list[dict[str, Any]] = []

    def add(
        key: str,
        label: str,
        level: str,
        message: str,
        value: Any,
        threshold: Any,
        unit: str,
        source: str,
    ) -> None:
        out.append(
            status(
                key,
                label,
                level,
                message,
                value=_clean(value),
                threshold=threshold,
                unit=unit,
                threshold_source=source,
            )
        )

    # 1. Hover thrust-to-weight
    tw_min = c["hover_thrust_to_weight_min"]
    tw = s["tw_total"]
    lvl = "ok" if tw >= tw_min * 0.995 else "fail"
    add(
        "hover_thrust_to_weight",
        "Hover thrust-to-weight",
        lvl,
        (
            f"The four lift motors give {tw:.2f} x the weight at full throttle (minimum "
            f"{tw_min:g}). "
            + (
                "The generic motors are sized to this minimum, so this confirms the sizing; "
                "real motors "
                "are checked in Phase 4."
                if lvl == "ok"
                else "Below the minimum: choose bigger propellers or motors, or reduce weight."
            )
        ),
        tw,
        tw_min,
        "",
        _src(meta, "checks.hover_thrust_to_weight_min"),
    )

    # 2. Static margin at both payloads
    sm_lo, sm_hi = c["static_margin_min"], c["static_margin_max"]
    for case, sm in (("max_payload", s["sm_max"]), ("min_payload", s["sm_min"])):
        word = "heaviest" if case == "max_payload" else "lightest"
        if sm < 0:
            lvl, msg = (
                "fail",
                (
                    f"Unstable in pitch with the {word} camera: the balance point is "
                    f"{-sm * 100:.1f} % of the chord behind the neutral point. Move the "
                    "battery forward or enlarge the tail."
                ),
            )
        elif sm < sm_lo or sm > sm_hi:
            lvl = "warn"
            msg = (
                f"Static margin {sm * 100:.1f} % with the {word} camera is outside "
                f"{sm_lo * 100:g}-{sm_hi * 100:g} %. "
                + (
                    "Move the battery forward or enlarge the tail for more stability."
                    if sm < sm_lo
                    else "Move the battery back: too much margin costs trim drag "
                    "and makes the aircraft sluggish."
                )
            )
        else:
            lvl, msg = (
                "ok",
                (
                    f"Static margin {sm * 100:.1f} % with the {word} camera, inside "
                    f"{sm_lo * 100:g}-{sm_hi * 100:g} %."
                ),
            )
        add(
            f"static_margin_{case}",
            f"Static margin ({word} camera)",
            lvl,
            msg,
            sm * 100,
            [sm_lo * 100, sm_hi * 100],
            "% MAC",
            _src(meta, "checks.static_margin_min") + " " + _src(meta, "checks.static_margin_max"),
        )

    # 3. CG envelope over the payload range
    worst = "ok"
    bad = []
    for e in s["envelope"]:
        share = e["front_share"]
        lv = "ok"
        if e["static_margin"] < 0 or share < 0 or share > 1:
            lv = "fail"
        elif not (sm_lo <= e["static_margin"] <= sm_hi) or max(share, 1 - share) > HOVER_SHARE_WARN:
            lv = "warn"
        if lv != "ok":
            bad.append(
                f"{e['payload_g']:.0f} g ({e['static_margin'] * 100:.1f} % MAC, front pair "
                f"{share * 100:.0f} %)"
            )
        worst = {"ok": lv, "warn": "fail" if lv == "fail" else "warn", "fail": "fail"}[worst]
    sweep_txt = ", ".join(f"{e['payload_g']:.0f} g -> {e['cg_x_mm']:.0f} mm" for e in s["envelope"])
    add(
        "cg_envelope",
        "Balance for every camera",
        worst,
        (
            f"Balance point across the payload range: {sweep_txt}. "
            + (
                "Stable in pitch and balanced between the motors for every payload."
                if worst == "ok"
                else f"Out of the envelope at: {'; '.join(bad)}. Move the battery or "
                "the wing so the balance point stays between the limits for all cameras."
            )
        ),
        [e["cg_x_mm"] for e in s["envelope"]],
        {"static_margin": [sm_lo * 100, sm_hi * 100], "max_pair_share": HOVER_SHARE_WARN},
        "mm",
        _src(meta, "checks.static_margin_min") + " Hover balance: busier pair at most 65 % "
        "(engine rule, Tier 1).",
    )

    # 4. Stall margin
    ratio_min = c["cruise_to_stall_speed_ratio_min"]
    r = s["cruise_to_stall"]
    lvl = "fail" if r < 1 else ("warn" if r < ratio_min else "ok")
    add(
        "stall_margin",
        "Cruise speed above stall",
        lvl,
        f"Cruise is {r:.2f} x the stall speed ({s['stall_speed']:.1f} m/s; minimum ratio "
        f"{ratio_min:g})."
        + (
            ""
            if lvl == "ok"
            else " Increase the wing area, choose an airfoil "
            "with more lift, reduce weight or cruise faster."
        ),
        r,
        ratio_min,
        "",
        _src(meta, "checks.cruise_to_stall_speed_ratio_min"),
    )

    # 5. Battery current against rating
    pack = s["pack"]
    frac = c["battery_current_max_fraction_of_rating"]
    i_cont, i_burst = pack["i_continuous_a"], pack["i_burst_a"]
    hover_i, peak_i = s["hover_current"], s["peak_current"]
    if hover_i > i_cont or peak_i > i_burst:
        lvl = "fail"
    elif peak_i > frac * i_cont or s["peak_cell_v"] < (3.3 if pack["chemistry"] == "lipo" else 3.0):
        lvl = "warn"
    else:
        lvl = "ok"
    add(
        "battery_current",
        "Battery current within rating",
        lvl,
        f"Hover draws {hover_i:.0f} A and the transition peaks at {peak_i:.0f} A "
        f"({peak_i / pack['capacity_ah']:.1f} C) "
        f"against a continuous rating of {i_cont:.0f} A ({pack['c_continuous']:g} C, placeholder "
        f"until Phase 4) and {i_burst:.0f} A burst; cells sag to {s['peak_cell_v']:.2f} V at the "
        "peak."
        + (
            ""
            if lvl == "ok"
            else " Use a pack with a higher C rating, add a parallel "
            "group, or reduce hover power (bigger propellers, less weight)."
        ),
        peak_i,
        frac * i_cont,
        "A",
        _src(meta, "checks.battery_current_max_fraction_of_rating") + " Ratings: LiPo 25 C / 50 C, "
        "Li-ion 3 C / 6 C placeholders.",
    )

    # 6. Take-off mass: design and legal limits
    m = s["mass_max_kg"]
    m_hi = s["mass_high_kg"]
    if m > lim["design_mtow_kg"]:
        lvl = "fail"
    elif m >= lim["warn_mtow_kg"] or m_hi > lim["design_mtow_kg"]:
        lvl = "warn"
    else:
        lvl = "ok"
    add(
        "mtow_design",
        "Take-off mass (design limit)",
        lvl,
        f"Take-off mass {m:.2f} kg (up to {m_hi:.2f} kg within the estimate's range); warning from "
        f"{lim['warn_mtow_kg']:g} kg, design limit {lim['design_mtow_kg']:g} kg.",
        m,
        lim["design_mtow_kg"],
        "kg",
        _src(meta, "limits.design_mtow_kg"),
    )
    lvl = "fail" if m >= lim["legal_mtow_kg"] else "ok"
    add(
        "mtow_legal",
        "Take-off mass (legal limit)",
        lvl,
        f"Take-off mass {m:.2f} kg against the {lim['legal_mtow_kg']:g} kg legal limit for the "
        "Open category."
        + (
            " Above the legal limit: this aircraft could not be flown in the Open category."
            if lvl == "fail"
            else ""
        ),
        m,
        lim["legal_mtow_kg"],
        "kg",
        _src(meta, "limits.legal_mtow_kg"),
    )

    # 7. Transition thrust margin (hover to the end of the transition range) and the separate
    # top-speed thrust margin (wing-borne flight up to 1.3 x cruise speed).
    tr = s["transition"]
    if tr:
        mm = tr["min_thrust_margin"]
        tr_end = tr.get("transition_end_speed_mps")
        incomplete = tr.get("transition_complete_within_sweep") is False
        add(
            "transition_margin",
            "Transition thrust margin",
            tr["level"],
            f"Lowest thrust margin {mm:.2f} at {tr['min_margin_speed_mps']:.1f} m/s "
            f"({tr['min_margin_group']}) between hover and "
            + (f"{tr_end:.1f} m/s, the end of the transition" if tr_end is not None else "")
            + f"; the wing carries 80 % of the weight from "
            f"{tr['speed_wing_80pct_mps']:.1f} m/s and all of it from "
            f"{tr['speed_wing_100pct_mps']:.1f} m/s; peak power {tr['peak_power_w']:.0f} W."
            + (
                f" The wing does not carry the full weight with a "
                f"{(trn.TRANSITION_END_BUFFER - 1) * 100:.0f} % speed margin within "
                f"{trn.SWEEP_END_FACTOR:g} x cruise speed: the transition never completes. "
                "Enlarge the wing or raise the cruise speed."
                if incomplete
                else ""
            )
            + (
                ""
                if tr["level"] == "ok"
                else " Increase motor or propeller size, reduce drag, or transition more gently."
            ),
            mm,
            c["transition_thrust_margin_min"],
            "",
            _src(meta, "checks.transition_thrust_margin_min")
            + f" Transition range: hover to {trn.TRANSITION_END_BUFFER:g} x the speed at "
            "which the wing alone carries the weight (engine rule).",
        )
        top = tr.get("top_speed")
        if top:
            add(
                "top_speed_margin",
                "Top-speed thrust margin",
                top["level"],
                _top_speed_message(top),
                top["margin_min"],
                top["required_margin"],
                "",
                f"Engine rule: in wing-borne flight up to {trn.SWEEP_END_FACTOR:g} x cruise "
                "speed the cruise "
                f"propellers should give at least {top['required_margin']:.2f} x the force "
                "needed (drag, plus the acceleration force below cruise speed). The "
                f"{(top['required_margin'] - 1) * 100:.0f} % reserve is the stated "
                "thrust-coefficient uncertainty of the generic propeller model. Warn only: "
                "the cruise-speed check covers flight at cruise.",
            )

    # 8. Spar and boom
    sp = s["spar"]
    add(
        "spar_strength",
        "Wing spar strength",
        sp["level"],
        f"{sp['spar']}: {sp['stress_mpa']:.0f} MPa at {sp['load_factor']:g} g x "
        f"{sp['safety_factor']:g} against {sp['allowable_mpa']:.0f} MPa allowable (margin "
        f"{sp['margin'] * 100:.0f} %); tip deflection {sp['tip_deflection_limit_mm']:.0f} mm at "
        f"{sp['load_factor']:g} g." + _spar_hint(s, sp),
        sp["margin"],
        0.0,
        "margin",
        _src(meta, "checks.manoeuvre_load_factor")
        + " "
        + _src(meta, "checks.structural_safety_factor")
        + " Fail below 0, warn below 0.25.",
    )
    if sp["level"] == "ok" and sp["tip_deflection_fraction"] > 0.10:
        add(
            "spar_stiffness",
            "Wing stiffness",
            "warn",
            f"The wing tip bends {sp['tip_deflection_limit_mm']:.0f} mm at {sp['load_factor']:g} g "
            f"({sp['tip_deflection_fraction'] * 100:.0f} % of the half span); a stiffer (larger) "
            "spar tube avoids flutter and aileron reversal.",
            sp["tip_deflection_fraction"],
            0.10,
            "fraction of half span",
            "Engine rule of thumb: tip deflection at limit load below 10 % of the half span.",
        )
    bm = s["boom"]
    add(
        "boom_strength",
        "Boom strength",
        bm["level"],
        f"{bm['tube']}: {bm['stress_mpa']:.0f} MPa ({bm['critical_case']}) against "
        f"{bm['allowable_mpa']:.0f} MPa allowable (margin {bm['margin'] * 100:.0f} %)."
        + (
            ""
            if bm["level"] == "ok"
            else f" Use at least a {bm['min_diameter_mm']:g} mm boom (standard wall)."
        ),
        bm["margin"],
        0.0,
        "margin",
        _src(meta, "checks.structural_safety_factor")
        + " Landing load 3 g on the gear (estimate). Fail below 0, warn below 0.25.",
    )

    # 9. Trim
    trims = [t for t in (s["trim_max"], s["trim_min"]) if t is not None]
    worst_trim = max((abs(t) for t in trims), default=float("nan"))
    if not s["trim_ok"] or not math.isfinite(worst_trim):
        lvl, msg = (
            "fail",
            (
                "AVL could not trim the aircraft at cruise with the elevator. Check the "
                "balance point and the tail size."
            ),
        )
    elif worst_trim > TRIM_LIMIT_DEG:
        lvl, msg = (
            "warn",
            (
                f"Cruise needs {worst_trim:.1f}° of elevator to trim, more than "
                f"±{TRIM_LIMIT_DEG:g}°: little control left. Adjust the wing incidence "
                "or balance point."
            ),
        )
    else:
        lvl, msg = (
            "ok",
            (
                f"Cruise trims with {fmt(s['trim_max'], 1)}° (heaviest camera) and "
                f"{fmt(s['trim_min'], 1)}° (lightest) of elevator, within ±{TRIM_LIMIT_DEG:g}°."
            ),
        )
    add(
        "trim",
        "Elevator trim at cruise",
        lvl,
        msg,
        worst_trim,
        TRIM_LIMIT_DEG,
        "°",
        "Engine rule: keep trim within ±15° of a typical ±20-25° servo throw.",
    )

    # 10. Cruise thrust
    avail, req = s["cruise_thrust_available"], s["cruise_thrust_required"]
    ratio = avail / req if req > 0 else float("inf")
    lvl = "fail" if ratio < 1.0 else ("warn" if ratio < 1.3 else "ok")
    add(
        "cruise_thrust",
        "Thrust available in cruise",
        lvl,
        f"At cruise speed the cruise propellers give up to {avail:.1f} N against {req:.1f} N of "
        f"drag ({ratio:.2f} x; cruise throttle {s['cruise_throttle'] * 100:.0f} %)."
        + (
            ""
            if lvl == "ok"
            else " The propellers run out of pitch at this speed: use a higher "
            "pitch or larger propeller, or cruise slower."
        ),
        ratio,
        1.3,
        "",
        "Engine rule: at least 1.3 x the cruise drag for climb and gusts (fail below 1.0).",
    )

    # 11. Endurance
    end = s["endurance"]
    target = s["mission"]["target_endurance_min"]
    if s["vtol_exceeds_usable"]:
        lvl = "fail"
    elif end["value"] is None or end["value"] < target:
        lvl = "warn"
    else:
        lvl = "ok"
    add(
        "endurance",
        "Endurance against target",
        lvl,
        f"Wing-flight endurance {fmt(end['value'])} min ({fmt(end['low'])}-{fmt(end['high'])}) "
        "against "
        f"the {target:g} min target."
        + (
            " The battery cannot even cover take-off, transitions and landing."
            if lvl == "fail"
            else ""
        ),
        end["value"],
        target,
        "min",
        "Mission target (mission.target_endurance_min).",
    )

    # 12. Lateral-directional stability signs
    cnb, clb = s.get("cn_beta"), s.get("cl_beta")
    if cnb is not None and clb is not None:
        lvl = "ok" if (cnb > 0 and clb < 0) else "warn"
        add(
            "lateral_stability",
            "Weathercock and roll stability",
            lvl,
            f"AVL gives Cn_beta {cnb:.3f}/rad (needs > 0: the nose turns into the wind) and "
            f"Cl_beta {clb:.3f}/rad (needs < 0: dihedral effect)."
            + ("" if lvl == "ok" else " Enlarge the fin or V-tail angle, or add dihedral."),
            {"Cn_beta": cnb, "Cl_beta": clb},
            {"Cn_beta": "> 0", "Cl_beta": "< 0"},
            "/rad",
            "Static lateral-directional stability signs (Nelson ch. 2 and 5).",
        )
    for gs in s["geometry_statuses"]:
        if gs["level"] == "fail":
            out.append({**gs, "threshold_source": "Geometry rule (Tier 1 geometry checks)."})
    return sort_statuses(out)


def _spar_hint(s: dict[str, Any], sp: dict[str, Any]) -> str:
    sz = s.get("spar_sizing")
    if sz and not sz.get("fits"):
        return (
            f" No standard tube that fits inside the {sz['max_outer_mm']:.0f} mm root depth is "
            "strong enough: use a thicker airfoil, a larger root chord, or carbon spar caps."
        )
    if sz:
        return " The tube was sized by the engine (lightest standard tube with margin)."
    return ""


def _clean(v: Any) -> Any:
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    return v
