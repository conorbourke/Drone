"""Battery pack model: voltage under load, usable energy, current against rating, heating.

Per-cell values by chemistry (placeholders until Phase 4 supplies real packs, each with its
source and uncertainty):

* Nominal voltage: LiPo 3.7 V, Li-ion 3.6 V (manufacturer datasheets; IEC 61960 defines nominal
  voltage; e.g. Molicel INR-21700-P45B datasheet 3.6 V nominal; hobby LiPo packs 3.7 V/cell).
* Open-circuit voltage at the reserve point (end of the planned flight, about 20 % charge left):
  LiPo 3.70 V, Li-ion 3.45 V per cell (typical open-circuit-voltage curves of LiCoO2 polymer and
  NMC/NCA cylindrical cells; Plett, *Battery Management Systems* vol. 1, 2015, ch. 2). ESTIMATE
  +/-0.05 V. The transition back to hover happens here, so it is where the pack is weakest.
* DC internal resistance per cell, scaled with capacity as R = k / C[Ah]:
  LiPo k = 15 mOhm Ah (3 mOhm for a 5 Ah cell: hobby 25-50 C packs measure 2-5 mOhm per cell);
  Li-ion k = 60 mOhm Ah (about 13 mOhm for a 4.5 Ah 21700 high-drain cell: datasheet AC
  impedance ~8-10 mOhm, DC resistance ~1.3-1.6 x higher). ESTIMATE +/-30 %, at 25 C and mid
  charge. Plus 2 mOhm for the pack's wiring and connector (estimate).
* Continuous and burst discharge ratings: LiPo 25 C continuous / 50 C for 10 s; Li-ion 3 C /
  6 C (the Tier 1 placeholders, deliberately conservative for Li-ion; real packs in Phase 4).
* Specific heat of cells 1000 J/(kg K) (Li-ion and LiPo cells 0.9-1.1 kJ/(kg K); Bernardi-type
  heating I^2 R only, entropic heat neglected), used only for the temperature note.

Usable energy = nominal energy x (1 - reserve fraction) x 0.95 (the Tier 1 usable fraction), and
the energy each flight phase takes from the pack is the load energy x (V_open-circuit /
V_loaded): what is lost as heat inside the pack is not available to the motors.
"""

from __future__ import annotations

import math
from typing import Any

from app.engine.mass import PACK_SPECIFIC_ENERGY_WH_PER_KG

CELL_NOMINAL_V = {"lipo": 3.7, "li-ion": 3.6}
CELL_RESERVE_OCV_V = {"lipo": 3.70, "li-ion": 3.45}
CELL_FULL_V = {"lipo": 4.20, "li-ion": 4.20}
CELL_MIN_LOADED_V = {"lipo": 3.30, "li-ion": 3.00}
CELL_RESISTANCE_K_OHM_AH = {"lipo": 0.015, "li-ion": 0.060}
RESISTANCE_UNCERTAINTY = 0.30
PACK_WIRING_OHM = 0.002
C_RATING = {
    "lipo": {"continuous": 25.0, "burst": 50.0},
    "li-ion": {"continuous": 3.0, "burst": 6.0},
}
CELL_SPECIFIC_HEAT = 1000.0
USABLE_ENERGY_FACTOR = 0.95
CHEMISTRY_LABEL = {"lipo": "LiPo", "li-ion": "Li-ion"}

SOURCES = {
    "nominal": "Cell nominal voltage LiPo 3.7 V, Li-ion 3.6 V (manufacturer datasheets, e.g. "
    "Molicel INR-21700-P45B; IEC 61960 nominal-voltage definition).",
    "resistance": "DC internal resistance per cell R = k / capacity, LiPo k = 15 mOhm Ah, "
    "Li-ion k = 60 mOhm Ah (hobby LiPo packs measure 2-5 mOhm per 5 Ah cell; 21700 high-drain "
    "datasheet AC impedance 8-10 mOhm, DC ~1.3-1.6 x); estimate +/-30 %, plus 2 mOhm wiring.",
    "ocv": "Open-circuit voltage at the reserve point LiPo 3.70 V, Li-ion 3.45 V per cell "
    "(typical discharge curves; Plett 2015 ch. 2); estimate +/-0.05 V.",
    "rating": "Discharge rating placeholders until Phase 4 packs: LiPo 25 C continuous / 50 C "
    "burst (10 s), Li-ion 3 C / 6 C (Tier 1 placeholders).",
}


def pack_model(battery: dict[str, Any], resistance_factor: float = 1.0) -> dict[str, Any]:
    """Electrical model of the pack from the design's battery block."""
    chem = battery["chemistry"]
    s = int(battery["cells_series"])
    par = int(battery["cells_parallel"])
    unit_ah = battery["capacity_mah"] / 1000
    cap_ah = unit_ah * par
    r_unit = CELL_RESISTANCE_K_OHM_AH[chem] / unit_ah
    r_pack = (s * r_unit / par + PACK_WIRING_OHM) * resistance_factor
    v_nom = s * CELL_NOMINAL_V[chem]
    energy = v_nom * cap_ah
    mass_kg = energy / PACK_SPECIFIC_ENERGY_WH_PER_KG[chem]
    c_cont = C_RATING[chem]["continuous"]
    c_burst = C_RATING[chem]["burst"]
    # Phase 4: a catalogue pack (or a custom pack built from catalogue cells) brings its own
    # mass and discharge ratings; the electrical model above stays the chemistry model.
    if battery.get("mass_g"):
        mass_kg = float(battery["mass_g"]) / 1000
    if battery.get("discharge_c_continuous"):
        c_cont = float(battery["discharge_c_continuous"])
        c_burst = float(battery.get("discharge_c_burst") or c_cont)
    label = f"{s}S{par}P {CHEMISTRY_LABEL[chem]} {battery['capacity_mah']:g} mAh per group"
    if battery.get("part_label"):
        label = f"{battery['part_label']} ({label})"
    return {
        "chemistry": chem,
        "label": label,
        "cells_series": s,
        "cells_parallel": par,
        "capacity_ah": cap_ah,
        "v_nominal": v_nom,
        "v_full": s * CELL_FULL_V[chem],
        "v_reserve_ocv": s * CELL_RESERVE_OCV_V[chem],
        "v_min_loaded": s * CELL_MIN_LOADED_V[chem],
        "r_pack_ohm": r_pack,
        "energy_wh": energy,
        "mass_kg": mass_kg,
        "i_continuous_a": c_cont * cap_ah,
        "i_burst_a": c_burst * cap_ah,
        "c_continuous": c_cont,
        "c_burst": c_burst,
    }


def loaded_voltage(
    pack: dict[str, Any], power_w: float, v_ocv: float | None = None
) -> dict[str, float]:
    """Terminal voltage and current delivering ``power_w``: V = Voc - I R with P = V I.

    Solved exactly: I = (Voc - sqrt(Voc^2 - 4 R P)) / (2 R). When the pack cannot deliver the
    power at all (maximum-power-transfer limit Voc^2 / 4R), ``feasible`` is False."""
    voc = pack["v_nominal"] if v_ocv is None else v_ocv
    r = pack["r_pack_ohm"]
    if power_w <= 0:
        return {"current_a": 0.0, "voltage_v": voc, "loss_w": 0.0, "feasible": True, "voc": voc}
    disc = voc * voc - 4 * r * power_w
    if disc < 0 or r <= 0:
        i = voc / (2 * r) if r > 0 else power_w / voc
        return {
            "current_a": i,
            "voltage_v": voc / 2,
            "loss_w": i * i * r,
            "feasible": r <= 0,
            "voc": voc,
        }
    i = (voc - math.sqrt(disc)) / (2 * r)
    return {
        "current_a": i,
        "voltage_v": voc - i * r,
        "loss_w": i * i * r,
        "feasible": True,
        "voc": voc,
    }


def usable_energy_wh(
    pack: dict[str, Any], reserve_fraction: float, energy_factor: float = 1.0
) -> float:
    return pack["energy_wh"] * energy_factor * (1 - reserve_fraction) * USABLE_ENERGY_FACTOR


def phase_energy_wh(
    pack: dict[str, Any], load_power_w: float, duration_s: float
) -> dict[str, float]:
    """Energy taken from the pack for a phase at constant load power (at nominal voltage)."""
    lv = loaded_voltage(pack, load_power_w)
    factor = lv["voc"] / lv["voltage_v"] if lv["voltage_v"] > 0 else 1.0
    return {
        "energy_wh": load_power_w * duration_s / 3600 * factor,
        "current_a": lv["current_a"],
        "voltage_v": lv["voltage_v"],
        "loss_w": lv["loss_w"],
    }


def temperature_rise_k(pack: dict[str, Any], current_a: float, duration_s: float) -> float:
    """Adiabatic cell temperature rise from I^2 R heating over ``duration_s``."""
    heat_j = current_a * current_a * (pack["r_pack_ohm"] - PACK_WIRING_OHM) * duration_s
    return heat_j / (pack["mass_kg"] * CELL_SPECIFIC_HEAT) if pack["mass_kg"] > 0 else float("nan")
