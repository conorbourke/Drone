"""Phase 4 parts selection: constraints, locks, reasoning numbers, and the analysis with parts."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.engine import selection as sel
from app.engine.analysis import apply_parts, run_analysis
from app.engine.geometry import build_geometry
from app.engine.mass import solve_mass
from app.engine.structure import MARGIN_WARN
from app.parts_catalog import validate_spec

SEED = Path(__file__).resolve().parents[2] / "seed" / "parts.json"

#: A 24 kg final-scale front-tilt design: the default prototype scaled with
#: ``run_scale(..., 24.0, mode="fast")`` (snapshot, so this test does not depend on the scaler).
SCALED_24KG_PARAMETERS: dict[str, Any] = {
    "schema_version": 2,
    "layout": "front_tilt",
    "wing": {
        "span_mm": 4760,
        "root_chord_mm": 690,
        "tip_chord_mm": 475,
        "sweep_deg": 0.0,
        "dihedral_deg": 3.0,
        "incidence_deg": 2.0,
        "twist_deg": 0.0,
        "airfoil": "sd7037",
        "x_le_mm": 625,
        "z_mm": 0.0,
    },
    "fuselage": {
        "length_mm": 1870,
        "width_mm": 115,
        "height_mm": 125,
        "cross_section": "rounded_rect",
    },
    "booms": {
        "count": 2,
        "lateral_offset_mm": 795,
        "length_mm": 1415,
        "x_offset_mm": -400,
        "diameter_mm": 25.0,
    },
    "motors": {"front_x_mm": 51, "rear_x_mm": 1366, "height_mm": 48},
    "tilt": {"axis_x_mm": 51, "max_angle_deg": 90.0},
    "pusher": {"prop_diameter_mm": 254.0, "x_mm": 880.0},
    "tail": {
        "type": "inverted_v",
        "span_mm": 1490,
        "chord_mm": 420,
        "arm_mm": 1290,
        "height_mm": 190,
        "v_angle_deg": 40.0,
        "airfoil": "naca0009",
    },
    "nose_bay": {"length_mm": 180.0, "width_mm": 100.0, "height_mm": 100.0},
    "landing_gear": {"type": "skids", "height_mm": 125},
    "propulsion": {"prop_diameter_mm": 635.0, "prop_pitch_mm": 269.4, "prop_blades": 2},
    "battery": {
        "chemistry": "lipo",
        "cells_series": 12,
        "cells_parallel": 2,
        "capacity_mah": 20700,
        "x_mm": 671,
    },
    "allowances": {"avionics_g": 220.0, "wiring_fraction": 0.06},
}
SCALED_24KG_MISSION = {
    "schema_version": 1,
    "scale": "final",
    "target_takeoff_mass_kg": 24.0,
    "target_endurance_min": 45.0,
    "cruise_speed_mps": 16.0,
    "payload_min_g": 150.0,
    "payload_max_g": 400.0,
}


def seed_catalogue() -> list[dict[str, Any]]:
    out = []
    for i, entry in enumerate(json.loads(SEED.read_text(encoding="utf-8")), start=1):
        part = copy.deepcopy(entry)
        part["id"] = i
        part["spec"] = validate_spec(part["category"], part["spec"])
        for li in part["listings"]:
            li["last_checked_at"] = "2026-10-09T00:00:00Z"
        out.append(part)
    return out


def _solver(p: dict[str, Any], m: dict[str, Any], s: dict[str, Any], r: dict[str, Any]) -> Any:
    sizing = (r.get("structure") or {}).get("spar_sizing") or {}
    tube = {"outer_mm": sizing["outer_mm"], "wall_mm": sizing["wall_mm"]} if sizing else None

    def solve(parts: dict[str, Any]) -> dict[str, Any]:
        q, mp, _ = apply_parts(p, parts)
        return solve_mass(q, build_geometry(q), m, s, spar_tube=tube, parts=mp)

    return solve


def make_list(
    p: dict[str, Any],
    m: dict[str, Any],
    s: dict[str, Any],
    cache: str,
    locked: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    r = run_analysis(p, m, s, mode="fast", cache_dir=cache, uncertainty=False)
    assert r["valid"]
    out = sel.build_parts_list(
        p, m, s, r, seed_catalogue(), locked=locked, mass_solver=_solver(p, m, s, r)
    )
    return out, r


def _role(out: dict[str, Any], role: str) -> dict[str, Any]:
    return next(r for r in out["roles"] if r["role"] == role)


def _check_constraints(out: dict[str, Any], s: dict[str, Any], cells: int) -> None:
    """Every filled role satisfies its own rules (from the numbers the list reports)."""
    tw = s["checks"]["hover_thrust_to_weight_min"]
    lm = _role(out, "lift_motor")
    if lm["filled"]:
        assert lm["part"]["spec"]["lipo_cells_min"] <= cells <= lm["part"]["spec"]["lipo_cells_max"]
        assert lm["metrics"]["thrust_to_weight"] >= tw - 1e-6
        assert lm["quantity"] == 4
    esc = _role(out, "esc")
    if esc["filled"]:
        spec = esc["part"]["spec"]
        assert spec["continuous_current_a"] >= 1.25 * esc["metrics"]["peak_current_a"] - 1e-6
        assert spec["lipo_cells_min"] <= cells <= spec["lipo_cells_max"]
    servo = next((r for r in out["roles"] if r["role"] == "tilt_servo"), None)
    if servo and servo["filled"]:
        assert servo["part"]["spec"]["torque_kg_cm"] >= 2 * servo["metrics"]["hinge_moment_kg_cm"]
        assert servo["part"]["spec"]["speed_s_per_60deg"] <= 0.15
    batt = _role(out, "battery")
    if batt["filled"]:
        frac = s["checks"]["battery_current_max_fraction_of_rating"]
        assert batt["metrics"]["continuous_a"] * frac >= batt["metrics"]["peak_a"] - 1e-6
    for role in ("spar_tube", "boom_tube"):
        tube = _role(out, role)
        if tube["filled"]:
            assert tube["metrics"]["margin"] >= MARGIN_WARN
    for role in ("radio", "telemetry"):
        radio = _role(out, role)
        if radio["filled"]:
            legal, _ = sel.radio_legality(radio["part"]["spec"]["frequency_mhz"])
            assert legal == "legal"


def test_default_prototype_full_list(
    polar_cache: str, params: dict, mission: dict, engine_settings: dict
) -> None:
    out, _r = make_list(params, mission, engine_settings, polar_cache)
    roles = {x["role"] for x in out["roles"]}
    assert roles == {
        "lift_motor",
        "lift_prop",
        "esc",
        "tilt_servo",
        "battery",
        "autopilot",
        "gps",
        "radio",
        "telemetry",
        "spar_tube",
        "boom_tube",
    }
    assert all(x["filled"] for x in out["roles"]), out["unfilled"]
    _check_constraints(out, engine_settings, params["battery"]["cells_series"])
    # Every choice explains itself with the numbers it used.
    lm = _role(out, "lift_motor")
    text = " ".join(lm["reasoning"])
    assert "thrust-to-weight" in text and " g/W" in text and " A " in text
    hover_g = round(lm["metrics"]["hover_current_a"], 1)
    assert f"{hover_g:.1f} A" in text
    for row in out["roles"]:
        assert row["reasoning"] and any(re.search(r"\d", s) for s in row["reasoning"]), row["role"]
        assert row["part"]["verified"] is False
        assert any(f["code"] == "unverified" for f in row["flags"])
        assert row["listings"], row["role"]
    # Totals against the default EUR 5,000 budget.
    t = out["totals"]
    assert t["budget_eur"] == 5000.0
    priced = sum(x["line_price_eur"] or 0 for x in out["roles"]) + out["consumables"]["cost_eur"]
    assert t["cost_eur"] == pytest.approx(priced, abs=0.05)
    assert t["budget_status"] == ("under" if t["cost_eur"] <= 4500 else t["budget_status"])
    assert t["mass_g"] == pytest.approx(
        sum(x["line_mass_g"] for x in out["roles"]) + out["consumables"]["mass_g"], abs=0.5
    )
    assert out["uk_import_note"] and "VAT" in out["uk_import_note"]
    for u in out["upgrades"]:
        assert u["extra_cost_eur"] > 0
        if u["eur_per_min"] is not None:
            assert u["endurance_gain_min"] > 0


def test_budget_setting_drives_status(
    polar_cache: str, params: dict, mission: dict, engine_settings: dict
) -> None:
    s = copy.deepcopy(engine_settings)
    s["budget"] = {"prototype_eur": 500.0}
    out, _ = make_list(params, mission, s, polar_cache)
    assert out["totals"]["budget_eur"] == 500.0
    assert out["totals"]["budget_status"] == "over"
    assert "over the EUR 500 budget" in out["totals"]["budget_message"]


def test_locked_parts_are_respected(
    polar_cache: str, params: dict, mission: dict, engine_settings: dict
) -> None:
    out, _ = make_list(params, mission, engine_settings, polar_cache)
    gps = _role(out, "gps")
    other = next(a for a in gps["alternatives"] if a["part"]["id"] != gps["part"]["id"])
    esc = _role(out, "esc")
    weak = next(a for a in esc["alternatives"] if not a["feasible"])
    locked = {
        "gps": {"part_id": other["part"]["id"], "quantity": 1},
        "esc": {"part_id": weak["part"]["id"], "quantity": 4},
    }
    out2, _ = make_list(params, mission, engine_settings, polar_cache, locked=locked)
    assert _role(out2, "gps")["part"]["id"] == other["part"]["id"]
    assert _role(out2, "gps")["locked"] is True
    esc2 = _role(out2, "esc")
    assert esc2["part"]["id"] == weak["part"]["id"] and esc2["locked"]
    # The engine keeps the owner's choice but says what it breaks.
    assert any(f["code"] == "constraint" for f in esc2["flags"])
    assert _role(out2, "autopilot")["locked"] is False


def test_selected_parts_change_the_analysis_masses(
    polar_cache: str, params: dict, mission: dict, engine_settings: dict
) -> None:
    out, generic = make_list(params, mission, engine_settings, polar_cache)
    sel_parts = out["analysis_parts"]
    r = run_analysis(
        params, mission, engine_settings, mode="fast", cache_dir=polar_cache, parts=sel_parts
    )
    assert r["valid"]
    comps = {c["key"]: c for c in r["mass"]["components"]}
    gen = {c["key"]: c for c in generic["mass"]["components"]}
    motor = sel_parts["lift_motor"]
    assert comps["motors_front"]["mass_g"] == pytest.approx(2 * motor["mass_g"])
    assert motor["label"] in comps["motors_front"]["label"]
    assert comps["motors_front"]["mass_g"] != pytest.approx(gen["motors_front"]["mass_g"])
    assert comps["battery"]["mass_g"] == pytest.approx(sel_parts["battery"]["mass_g"])
    assert comps["avionics"]["mass_g"] == pytest.approx(sel_parts["avionics"]["mass_g"])
    assert "allowance" not in comps["avionics"]["label"]
    assert r["propulsion"]["lift_motor"]["assumed"] is False
    assert r["parts"] and "lift_motor" in r["parts"]
    assert r["summary"]["takeoff_mass"]["value"] == pytest.approx(
        out["totals"]["takeoff_mass_kg"], rel=0.02
    )
    # The selected battery's own discharge rating replaces the chemistry placeholder.
    assert r["battery"]["pack"]["c_continuous"] == pytest.approx(
        sel_parts["battery"]["discharge_c_continuous"]
    )


def test_24_kg_design(polar_cache: str, engine_settings: dict) -> None:
    p, m = SCALED_24KG_PARAMETERS, SCALED_24KG_MISSION
    out, _r = make_list(p, m, engine_settings, polar_cache)
    _check_constraints(out, engine_settings, p["battery"]["cells_series"])
    lm = _role(out, "lift_motor")
    assert lm["filled"], lm["unfilled_reason"]
    assert lm["part"]["spec"]["lipo_cells_max"] >= 12
    # Every role is either filled or says plainly why not.
    for row in out["roles"]:
        assert row["filled"] or (row["unfilled_reason"] and len(row["unfilled_reason"]) > 20)
    assert out["unfilled"] == [
        {"role": x["role"], "label": x["label"], "reason": x["unfilled_reason"]}
        for x in out["roles"]
        if not x["filled"]
    ]


def test_radio_legality() -> None:
    assert sel.radio_legality(868)[0] == "legal"
    assert sel.radio_legality(2440)[0] == "legal"
    assert sel.radio_legality(433)[0] == "restricted"
    assert sel.radio_legality(915)[0] == "illegal"


def test_trade_size_matching() -> None:
    from app.engine.propulsion import matching_thrust_points, trade_size

    assert trade_size("APC 13x6.5") == (13.0, 6.5)
    assert trade_size("G28x9.2") == (28.0, 9.2)
    motor = {
        "thrust_data": [
            {"prop": "15x5", "rpm": 1},
            {"prop": "14x4.8", "rpm": 1},
        ]
    }
    prop = {"trade_size": "15x5", "diameter_mm": 381, "pitch_mm": 127}
    assert [p["prop"] for p in matching_thrust_points(motor, prop)] == ["15x5"]
    assert len(matching_thrust_points(motor, None)) == 2
