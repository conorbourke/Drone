"""Recommendations: a sensitivity sweep ranked by endurance gained, respecting every check.

Public entry point::

    run_recommendations(parameters, mission, settings, *, baseline=None, cache_dir=None,
                        progress=None, settings_meta=None, max_results=8) -> dict

Each key parameter is nudged by a small step in both directions (docs/phases/PHASE3.md
section 2): span +/-5 %, root and tip chord +/-5 %, aspect ratio at constant area +/-5 %, the
other library wing airfoils, fuselage width and height +/-10 %, battery capacity +/-20 % and
parallel groups +/-1, lift propeller diameter +/-1 inch (25.4 mm). The propeller pitch +/-1 inch
is added because the propeller model shows it matters a lot in cruise (an addition to the contract
list). Every variant re-runs the fast analysis path (AVL, drag, propulsion, battery, transition,
structure, mass; cached or tabulated polars, no new XFOIL runs).

A variant is discarded when it makes any check worse than it was (ok -> warn, ok -> fail,
warn -> fail), or when it does not gain endurance. The rest are ranked by the nominal wing-flight
endurance gained with the heaviest camera. Each recommendation has a plain sentence, the
parameter change, before/after key numbers and a ``patch`` (dotted path -> new value) plus the
full new ``parameters`` document for "Try as new version".

Separately, ``fixes`` lists balance corrections when a stability check fails: the battery move
that puts the static margin in the middle of the settings range at both payloads, verified with
the same fast analysis.
"""

from __future__ import annotations

import copy
import math
import time
from collections.abc import Callable
from typing import Any

from app.engine import airfoils as airfoil_lib
from app.engine.analysis import json_safe, resolve_settings, run_analysis, validate_inputs
from app.engine.polars import PolarStore
from app.engine.quantity import LEVEL_ORDER

ProgressFn = Callable[[float, str], None]
SEVERITY = {"ok": 0, "info": 0, "warn": 1, "fail": 2}
INCH_MM = 25.4


def _set(doc: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = doc
    for k in parts[:-1]:
        node = node[k]
    node[parts[-1]] = value


def _get(doc: dict[str, Any], path: str) -> Any:
    node: Any = doc
    for k in path.split("."):
        node = node[k]
    return node


def candidate_changes(p: dict[str, Any]) -> list[dict[str, Any]]:
    """The parameter nudges, each ``{key, label, patch}`` (patch: dotted path -> value)."""
    w = p["wing"]
    out: list[dict[str, Any]] = []

    def add(key: str, label: str, patch: dict[str, Any]) -> None:
        out.append({"key": key, "label": label, "patch": {k: _round(v) for k, v in patch.items()}})

    for sgn, word in ((1, "Increase"), (-1, "Decrease")):
        f = 1 + 0.05 * sgn
        add(
            f"span{'+' if sgn > 0 else '-'}",
            f"{word} the wingspan by {abs(w['span_mm'] * 0.05):.0f} mm (5 %)",
            {"wing.span_mm": w["span_mm"] * f},
        )
        root = w["root_chord_mm"] * f
        if root >= w["tip_chord_mm"]:
            add(
                f"root_chord{'+' if sgn > 0 else '-'}",
                f"{word} the root chord by {abs(w['root_chord_mm'] * 0.05):.0f} mm (5 %)",
                {"wing.root_chord_mm": root},
            )
        tip = w["tip_chord_mm"] * f
        if tip <= w["root_chord_mm"]:
            add(
                f"tip_chord{'+' if sgn > 0 else '-'}",
                f"{word} the tip chord by {abs(w['tip_chord_mm'] * 0.05):.0f} mm (5 %)",
                {"wing.tip_chord_mm": tip},
            )
        k = math.sqrt(f)
        add(
            f"aspect_ratio{'+' if sgn > 0 else '-'}",
            f"{word} the aspect ratio by 5 % at the same wing area (span {w['span_mm'] * k:.0f} "
            "mm, "
            f"chords {w['root_chord_mm'] / k:.0f}/{w['tip_chord_mm'] / k:.0f} mm)",
            {
                "wing.span_mm": w["span_mm"] * k,
                "wing.root_chord_mm": w["root_chord_mm"] / k,
                "wing.tip_chord_mm": w["tip_chord_mm"] / k,
            },
        )
        fw = 1 + 0.10 * sgn
        add(
            f"fuselage_width{'+' if sgn > 0 else '-'}",
            f"{word} the fuselage width by {abs(p['fuselage']['width_mm'] * 0.1):.0f} mm (10 %)",
            {"fuselage.width_mm": p["fuselage"]["width_mm"] * fw},
        )
        add(
            f"fuselage_height{'+' if sgn > 0 else '-'}",
            f"{word} the fuselage height by {abs(p['fuselage']['height_mm'] * 0.1):.0f} mm (10 %)",
            {"fuselage.height_mm": p["fuselage"]["height_mm"] * fw},
        )
        fb = 1 + 0.20 * sgn
        add(
            f"battery_capacity{'+' if sgn > 0 else '-'}",
            f"{word} the battery capacity by 20 % ({p['battery']['capacity_mah'] * fb:.0f} mAh "
            "per group)",
            {"battery.capacity_mah": p["battery"]["capacity_mah"] * fb},
        )
        par = p["battery"]["cells_parallel"] + sgn
        if 1 <= par <= 10:
            add(
                f"cells_parallel{'+' if sgn > 0 else '-'}",
                f"{'Add' if sgn > 0 else 'Remove'} one parallel battery group "
                f"({p['battery']['cells_series']}S{par}P)",
                {"battery.cells_parallel": par},
            )
        d = p["propulsion"]["prop_diameter_mm"] + sgn * INCH_MM
        if d > 50:
            add(
                f"prop_diameter{'+' if sgn > 0 else '-'}",
                f"Use {d / INCH_MM:.0f}-inch lift propellers ({d:.0f} mm, 1 inch "
                f"{'larger' if sgn > 0 else 'smaller'})",
                {"propulsion.prop_diameter_mm": d},
            )
        pitch = p["propulsion"]["prop_pitch_mm"] + sgn * INCH_MM
        if pitch > 20:
            add(
                f"prop_pitch{'+' if sgn > 0 else '-'}",
                f"Use 1 inch {'more' if sgn > 0 else 'less'} propeller pitch ({pitch:.0f} mm, "
                f"{pitch / INCH_MM:.1f} in)",
                {"propulsion.prop_pitch_mm": pitch},
            )
    for entry in airfoil_lib.LIBRARY:
        if entry.use == "wing" and entry.id != w["airfoil"]:
            add(
                f"airfoil_{entry.id}",
                f"Change the wing airfoil to {entry.name}",
                {"wing.airfoil": entry.id},
            )
    return out


def _round(v: Any) -> Any:
    if isinstance(v, float):
        return round(v, 1)
    return v


def _levels(result: dict[str, Any]) -> dict[str, str]:
    return {c["key"]: c["level"] for c in result.get("checks", [])}


def _worse(base: dict[str, str], new: dict[str, str]) -> list[str]:
    bad = []
    for k, lv in new.items():
        b = base.get(k, "ok")
        if SEVERITY.get(lv, 0) > SEVERITY.get(b, 0):
            bad.append(k)
    return bad


def _key_numbers(r: dict[str, Any]) -> dict[str, Any]:
    s = r["summary"]
    return {
        "endurance_cruise": s["endurance_cruise"],
        "takeoff_mass": s["takeoff_mass"],
        "cruise_power": s["cruise_power"],
        "hover_power": s["hover_power"],
        "stall_speed": s["stall_speed"],
        "static_margin_min_payload": s["static_margin_min_payload"],
        "lift_to_drag": s["lift_to_drag"],
    }


def _reason(base: dict[str, Any], new: dict[str, Any]) -> str:
    """The main physical reason for the endurance change, in plain words."""
    b, n = base, new
    v = b["inputs"]["mission"]["cruise_speed_mps"]
    q_s = 0.5 * 1.225 * v * v * b["aero"]["wing_area"]["value"]
    q_s2 = 0.5 * 1.225 * v * v * n["aero"]["wing_area"]["value"]
    parts = {
        "induced drag": (n["drag"]["cd_induced"] * q_s2 - b["drag"]["cd_induced"] * q_s),
        "wing and tail profile drag": (
            n["drag"]["cd_profile"] * q_s2 - b["drag"]["cd_profile"] * q_s
        ),
        "fuselage, boom and rotor drag": (
            n["drag"]["cd_parasite"] * q_s2 - b["drag"]["cd_parasite"] * q_s
        ),
    }
    words = []
    d_energy = n["battery"]["usable_energy"]["value"] - b["battery"]["usable_energy"]["value"]
    eta_b = b["propulsion"]["cruise_propeller_efficiency"]["value"]
    eta_n = n["propulsion"]["cruise_propeller_efficiency"]["value"]
    if abs(d_energy) > 0.02 * b["battery"]["usable_energy"]["value"]:
        words.append(
            f"usable energy {'rises' if d_energy > 0 else 'falls'} by {abs(d_energy):.0f} Wh"
        )
    if abs(eta_n - eta_b) > 0.02:
        words.append(
            f"the cruise propeller efficiency {'rises' if eta_n > eta_b else 'falls'} from "
            f"{eta_b:.2f} to {eta_n:.2f}"
        )
    key, dv = max(parts.items(), key=lambda kv: abs(kv[1]))
    if abs(dv) > 0.01:
        words.append(f"{key} {'falls' if dv < 0 else 'rises'} by {abs(dv):.2f} N")
    dm = (n["summary"]["takeoff_mass"]["value"] - b["summary"]["takeoff_mass"]["value"]) * 1000
    if not words:
        words.append("the cruise power changes slightly")
    return " and ".join(words) + f"; mass {'+' if dm >= 0 else '-'}{abs(dm):.0f} g"


def _sentence(label: str, base: dict[str, Any], new: dict[str, Any]) -> str:
    eb, en = base["summary"]["endurance_cruise"], new["summary"]["endurance_cruise"]
    d = en["value"] - eb["value"]
    return (
        f"{label}: endurance {'+' if d >= 0 else '-'}{abs(d):.1f} min "
        f"({eb['low']:.0f}-{eb['high']:.0f} -> {en['low']:.0f}-{en['high']:.0f} min) because "
        f"{_reason(base, new)}."
    )


def balance_fix(
    p: dict[str, Any],
    mission: dict[str, Any],
    settings: dict[str, Any],
    base: dict[str, Any],
    **kw: Any,
) -> dict[str, Any] | None:
    """Battery move that centres the static margin range at both payloads, verified."""
    c = settings["checks"]
    sm_max = base["summary"]["static_margin_max_payload"]["value"] / 100
    sm_min = base["summary"]["static_margin_min_payload"]["value"] / 100
    lo, hi = c["static_margin_min"], c["static_margin_max"]
    if lo <= sm_min <= hi and lo <= sm_max <= hi:
        return None
    target_mid = (lo + hi) / 2
    current_mid = (sm_max + sm_min) / 2
    mac = base["geometry"]["wing"]["mac_mm"]
    d_cg = -(target_mid - current_mid) * mac
    m_tot = base["summary"]["takeoff_mass"]["value"]
    m_bat = base["mass"]["battery"]["value"]
    if m_bat <= 0:
        return None
    dx = d_cg * m_tot / m_bat
    x0 = p["battery"]["x_mm"]
    lo_x = p["nose_bay"]["length_mm"] + 20
    hi_x = p["fuselage"]["length_mm"] * 0.75
    x1 = min(hi_x, max(lo_x, x0 + dx))
    if abs(x1 - x0) < 2:
        return None
    q = copy.deepcopy(p)
    q["battery"]["x_mm"] = round(x1, 1)
    r = run_analysis(q, mission, settings, mode="fast", **kw)
    if not r.get("valid"):
        return None
    lv = _levels(r)
    return {
        "key": "battery_position",
        "sentence": (
            f"Move the battery {'forward' if x1 < x0 else 'back'} by {abs(x1 - x0):.0f} mm "
            f"(centre at {x1:.0f} mm from the nose): static margin "
            f"{sm_max * 100:.1f}/{sm_min * 100:.1f} % -> "
            f"{r['summary']['static_margin_max_payload']['value']:.1f}/"
            f"{r['summary']['static_margin_min_payload']['value']:.1f} % MAC (heaviest/"
            "lightest camera)"
            + (" (limited by the space in the fuselage)" if x1 != x0 + dx else "")
            + "."
        ),
        "patch": {"battery.x_mm": round(x1, 1)},
        "parameters": q,
        "before": _key_numbers(base),
        "after": _key_numbers(r),
        "checks_after": lv,
    }


def run_recommendations(
    parameters: dict[str, Any],
    mission: dict[str, Any],
    settings: dict[str, Any] | None = None,
    *,
    baseline: dict[str, Any] | None = None,
    cache_dir: str | None = None,
    progress: ProgressFn | None = None,
    settings_meta: dict[str, Any] | None = None,
    max_results: int = 8,
) -> dict[str, Any]:
    """Sensitivity sweep. ``baseline`` may be a fast-mode result for the same inputs."""
    t0 = time.time()
    p, m, problems = validate_inputs(parameters, mission)
    if p is None or m is None:
        return {
            "valid": False,
            "recommendations": [],
            "fixes": [],
            "rejected": [],
            "checks": problems,
        }
    s = resolve_settings(settings)
    kw = {"cache_dir": cache_dir, "settings_meta": settings_meta}
    if baseline is None or baseline.get("mode") != "fast":
        baseline = run_analysis(p, m, s, mode="fast", **kw)
    if not baseline.get("valid"):
        return {
            "valid": False,
            "recommendations": [],
            "fixes": [],
            "rejected": [],
            "checks": baseline.get("checks", []),
        }
    base_levels = _levels(baseline)
    cands = candidate_changes(p)
    accepted, rejected = [], []
    ncrit = float(s.get("analysis", {}).get("ncrit", 9.0))
    table_baseline: dict[str, Any] | None = None
    for i, cand in enumerate(cands):
        if progress:
            progress(i / max(1, len(cands)), f"Trying: {cand['label']}")
        q = copy.deepcopy(p)
        for path, val in cand["patch"].items():
            _set(q, path, val)
        ref = baseline
        if cand["key"].startswith("airfoil_"):
            # Compare airfoils on the same footing: both from the Phase 2 XFOIL table.
            if table_baseline is None:
                table_baseline = run_analysis(
                    p,
                    m,
                    s,
                    mode="fast",
                    settings_meta=settings_meta,
                    polar_store=PolarStore(cache_dir, ncrit, allow_xfoil=False, use_cache=False),
                )
            ref = table_baseline
            r = run_analysis(
                q,
                m,
                s,
                mode="fast",
                settings_meta=settings_meta,
                polar_store=PolarStore(cache_dir, ncrit, allow_xfoil=False, use_cache=False),
            )
        else:
            r = run_analysis(q, m, s, mode="fast", **kw)
        if not r.get("valid") or not ref.get("valid"):
            rejected.append(
                {
                    "key": cand["key"],
                    "label": cand["label"],
                    "reason": "invalid design",
                    "detail": [c["message"] for c in r.get("checks", [])][:2],
                }
            )
            continue
        worse = _worse(base_levels, _levels(r))
        gain = (
            r["summary"]["endurance_cruise"]["value"] - ref["summary"]["endurance_cruise"]["value"]
        )
        if worse:
            rejected.append(
                {
                    "key": cand["key"],
                    "label": cand["label"],
                    "endurance_gain_min": gain,
                    "reason": "makes a check worse",
                    "checks": worse,
                }
            )
            continue
        if gain < max(0.2, 0.005 * ref["summary"]["endurance_cruise"]["value"]):
            rejected.append(
                {
                    "key": cand["key"],
                    "label": cand["label"],
                    "endurance_gain_min": gain,
                    "reason": "no worthwhile endurance gain (under 0.2 min or 0.5 %)",
                }
            )
            continue
        accepted.append(
            {
                "key": cand["key"],
                "label": cand["label"],
                "sentence": _sentence(cand["label"], ref, r)
                + (
                    " (both airfoils compared with the Phase 2 XFOIL table polars)"
                    if ref is not baseline
                    else ""
                ),
                "endurance_gain_min": gain,
                "change": {
                    path: {"from": _get(p, path), "to": val} for path, val in cand["patch"].items()
                },
                "patch": cand["patch"],
                "parameters": q,
                "before": _key_numbers(ref),
                "after": _key_numbers(r),
                "checks_after": _levels(r),
            }
        )
    accepted.sort(key=lambda a: -a["endurance_gain_min"])
    for rank, a in enumerate(accepted, start=1):
        a["rank"] = rank
    fixes = []
    fx = balance_fix(p, m, s, baseline, **kw)
    if fx:
        fixes.append(fx)
    return json_safe(
        {
            "valid": True,
            "baseline": _key_numbers(baseline),
            "baseline_checks": base_levels,
            "recommendations": accepted[:max_results],
            "all_accepted": len(accepted),
            "fixes": fixes,
            "rejected": rejected,
            "variants_tried": len(cands),
            "duration_s": round(time.time() - t0, 2),
            "method": "Each change re-runs the fast analysis (AVL, XFOIL polars from the cache "
            "or the "
            "Phase 2 table, drag, propulsion, battery, transition, structure, mass); changes "
            "that make "
            "any check worse are discarded; the rest are ranked by wing-flight endurance gained "
            "with "
            "the heaviest camera.",
            "level_order": LEVEL_ORDER,
        }
    )
