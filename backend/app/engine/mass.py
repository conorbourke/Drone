"""Mass and balance: the Python port of the Tier 1 mass model (frontend/src/engine/mass.ts).

Used by the server analysis until Phase 4 supplies real parts. Every component carries its mass,
x position, relative uncertainty, source and a plain explanation; motors are sized for the hover
thrust-to-weight minimum, which depends on the total mass, so the build-up is iterated to a fixed
point (at most 20 passes). Constants, sources and rules are those of docs/ENGINE.md "Mass and
balance"; the Tier 1 values are repeated here with their sources so the two engines agree.

One addition for the server: ``spar_tube`` lets the structure module replace the Tier 1 guessed
prototype spar tube by the tube it sized for the bending check (docs/phases/PHASE3.md section 2,
"a default tube sized by the engine until Phase 4").

Phase 4: ``parts`` (see :func:`solve_mass`) replaces the statistical motor, propeller, ESC, tilt
servo, avionics and battery masses and the tube masses by the selected catalogue parts' masses.
Each replaced component says so in its label and source and carries a 3 % uncertainty
(catalogue masses, unverified). Mounts (20 % of motor mass), hinges, control-surface servos,
landing gear and the wiring fraction stay allowances.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

from app.engine.quantity import G0, RHO_SL, q_range, q_rel

# ----- Constants (frontend/src/engine/constants.ts; sources in docs/ENGINE.md) -----

#: Structure areal densities, kg/m^2 (printed values are PLACEHOLDERS for Phase 6 calibration).
AREAL_DENSITY = {
    "prototype": {"wing": 0.75, "fuselage": 0.8, "tail": 0.5},
    "final": {"wing": 1.0, "fuselage": 1.0, "tail": 0.7},
}
AREAL_DENSITY_UNCERTAINTY = {
    "prototype": {"wing": 0.3, "fuselage": 0.35, "tail": 0.35},
    "final": {"wing": 0.35, "fuselage": 0.35, "tail": 0.35},
}
#: Carbon tube: density 1550 kg/m^3, wall max(1 mm, 5 % of D), +/-15 %.
CARBON_TUBE_DENSITY = 1550.0
CARBON_TUBE_MIN_WALL_M = 0.001
CARBON_TUBE_WALL_FRACTION = 0.05
CARBON_TUBE_UNCERTAINTY = 0.15
SPAR_TUBE_THICKNESS_FRACTION = 0.7
#: Final-scale bending-sized spar caps (Tier 1): n_ult 6, 600 MPa, depth 0.85 t, x1.3.
SPAR_ULTIMATE_LOAD_FACTOR = 6.0
SPAR_ALLOWABLE_STRESS_PA = 600e6
SPAR_DEPTH_FRACTION = 0.85
SPAR_EXTRA_FACTOR = 1.3
ELLIPTIC_CENTROID = 4 / (3 * math.pi)
#: Statistical motor, ESC and propeller masses (engine author's fits; docs/ENGINE.md).
MOTOR_MASS_COEFF = 1.2
MOTOR_MASS_EXP = 0.73
MOTOR_MASS_UNCERTAINTY = 0.3
ESC_MASS_PER_A = 1.0
ESC_MASS_FIXED_G = 5.0
ESC_MASS_UNCERTAINTY = 0.4
PROP_MASS_REF_G = 20.0
PROP_MASS_REF_DIAMETER_M = 0.3048
PROP_MASS_EXP = 2.5
PROP_MASS_UNCERTAINTY = 0.4
MOTOR_MOUNT_FRACTION = 0.2
TILT_MECH_FIXED_G = 25.0
TILT_MECH_FRACTION = 0.25
CONTROL_SERVO_COUNT = 4
CONTROL_SERVO_FIXED_G = 6.0
CONTROL_SERVO_PER_TAKEOFF_G = 0.0025
LANDING_GEAR_FRACTION = {"skids": 0.03, "legs": 0.04, "none": 0.0}
ALLOWANCE_UNCERTAINTY = 0.2
#: Motor sizing (Tier 1): figure of merit at full power 0.55, motor efficiency 0.80, download 3 %.
FIGURE_OF_MERIT_MAX = 0.55
FIGURE_OF_MERIT_PUSHER_STATIC = 0.6
ETA_MOTOR_MAX = 0.8
HOVER_DOWNLOAD_FRACTION = 0.03
PUSHER_THRUST_TO_WEIGHT = 0.5
ESC_CURRENT_MARGIN = 1.2
#: Batteries: nominal cell voltage and pack-level specific energy (datasheets; Gundlach ch. 7).
CELL_NOMINAL_V = {"lipo": 3.7, "li-ion": 3.6}
PACK_SPECIFIC_ENERGY_WH_PER_KG = {"lipo": 145.0, "li-ion": 200.0}
PACK_SPECIFIC_ENERGY_UNCERTAINTY = 0.1
#: Catalogue part masses: manufacturer figures, unverified (Phase 4).
PART_MASS_UNCERTAINTY = 0.03
#: Tilt hinge, bearing and linkage hardware per side when a catalogue servo is selected
#: (estimate: printed or aluminium hinge, two bearings, a ball link; 15-30 g).
TILT_HINGE_HARDWARE_G = 20.0
MASS_MAX_ITERATIONS = 20
MASS_TOLERANCE_G = 0.05


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def carbon_tube_wall_m(diameter_mm: float) -> float:
    return max(CARBON_TUBE_MIN_WALL_M, CARBON_TUBE_WALL_FRACTION * diameter_mm / 1000)


def carbon_tube_mass_per_m(diameter_mm: float, wall_mm: float | None = None) -> float:
    """Carbon tube mass per metre (g/m): rho pi (D - t) t; solid rod when the wall fills it."""
    d = diameter_mm / 1000
    t = carbon_tube_wall_m(diameter_mm) if wall_mm is None else wall_mm / 1000
    if d <= 2 * t:
        return CARBON_TUBE_DENSITY * math.pi * (d / 2) ** 2 * 1000
    return CARBON_TUBE_DENSITY * math.pi * (d - t) * t * 1000


def motor_mass_g(max_power_w: float) -> float:
    return MOTOR_MASS_COEFF * max_power_w**MOTOR_MASS_EXP if max_power_w > 0 else 0.0


def esc_mass_g(rated_current_a: float) -> float:
    return ESC_MASS_PER_A * rated_current_a + ESC_MASS_FIXED_G if rated_current_a > 0 else 0.0


def prop_mass_g(diameter_mm: float, blades: int) -> float:
    if diameter_mm <= 0:
        return 0.0
    ratio = diameter_mm / 1000 / PROP_MASS_REF_DIAMETER_M
    return PROP_MASS_REF_G * ratio**PROP_MASS_EXP * (max(1, blades) / 2)


def ideal_hover_power(thrust_n: float, disc_area_m2: float, rho: float = RHO_SL) -> float:
    """Momentum-theory ideal power T^1.5 / sqrt(2 rho A) (Leishman ch. 2)."""
    if thrust_n <= 0 or disc_area_m2 <= 0:
        return 0.0
    return thrust_n**1.5 / math.sqrt(2 * rho * disc_area_m2)


def pack_electrics(p: dict[str, Any]) -> dict[str, float]:
    b = p["battery"]
    voltage = b["cells_series"] * CELL_NOMINAL_V[b["chemistry"]]
    capacity_ah = b["capacity_mah"] / 1000 * b["cells_parallel"]
    energy = voltage * capacity_ah
    return {
        "voltage": voltage,
        "capacity_ah": capacity_ah,
        "energy_wh": energy,
        "mass_g": energy / PACK_SPECIFIC_ENERGY_WH_PER_KG[b["chemistry"]] * 1000,
    }


def centre_of_gravity(components: list[dict[str, Any]]) -> dict[str, float]:
    """Mass-weighted x and its uncertainty (structure correlated, the rest independent)."""
    m = sum(c["mass_g"] for c in components)
    x = sum(c["mass_g"] * c["x_mm"] for c in components) / m if m > 0 else float("nan")
    lin = 0.0
    ind = 0.0
    for c in components:
        d = (c["x_mm"] - x) * c["uncertainty"] * c["mass_g"]
        if c["group"] == "structure":
            lin += d
        else:
            ind += d * d
    return {"x": x, "sigma": math.sqrt(lin * lin + ind) / m if m > 0 else float("nan"), "total": m}


def mass_sigma(components: list[dict[str, Any]]) -> float:
    lin = sum(c["uncertainty"] * c["mass_g"] for c in components if c["group"] == "structure")
    ind = sum(
        (c["uncertainty"] * c["mass_g"]) ** 2 for c in components if c["group"] != "structure"
    )
    return math.sqrt(lin * lin + ind)


def front_thrust_share(cg_x: float, front_x: float, rear_x: float) -> float:
    """Front-pair share of hover thrust from the moment balance about the CG (statics)."""
    span = rear_x - front_x
    if abs(span) <= 1e-9:
        return 0.5
    return (rear_x - cg_x) / span


def _fuselage_wetted_centroid_x(g: dict[str, Any]) -> float:
    st = g["fuselage"]["stations"]
    a = ax = 0.0
    for s0, s1 in itertools.pairwise(st):
        seg = (s0["perimeter_mm"] + s1["perimeter_mm"]) / 2 * (s1["x_mm"] - s0["x_mm"])
        a += seg
        ax += seg * (s0["x_mm"] + s1["x_mm"]) / 2
    return ax / a if a > 0 else g["fuselage"]["length_mm"] * 0.45


def default_spar_tube(g: dict[str, Any]) -> dict[str, float]:
    """Tier 1 prototype spar tube: 70 % of the root thickness, whole mm, 8-30 mm."""
    w = g["wing"]
    d = clamp(
        math.floor(SPAR_TUBE_THICKNESS_FRACTION * w["thickness_ratio"] * w["root_chord_mm"]), 8, 30
    )
    return {"outer_mm": float(d), "wall_mm": carbon_tube_wall_m(d) * 1000}


def _build(
    p: dict[str, Any],
    g: dict[str, Any],
    mission: dict[str, Any],
    settings: dict[str, Any],
    mtow_kg: float,
    cg_guess: float,
    spar_tube: dict[str, float] | None,
    parts: dict[str, Any] | None = None,
    structure_factor: float = 1.0,
) -> dict[str, Any]:
    scale = "final" if mission.get("scale") == "final" else "prototype"
    dens = AREAL_DENSITY[scale]
    dens_u = AREAL_DENSITY_UNCERTAINTY[scale]
    pack = pack_electrics(p)
    weight = mtow_kg * G0
    comps: list[dict[str, Any]] = []
    parts = parts or {}

    def part(key: str) -> dict[str, Any] | None:
        """A selected catalogue part's mass entry ``{mass_g, label, ...}``, or None."""
        entry = parts.get(key)
        return entry if isinstance(entry, dict) and entry.get("mass_g") else None

    def part_src(entry: dict[str, Any], what: str) -> str:
        return f"Selected catalogue part {entry['label']}: {what} (manufacturer figure)."

    def add(**c: Any) -> None:
        if c["group"] == "structure" and structure_factor != 1.0:
            # Phase 6: the owner's built weights calibrate the structure densities.
            c["mass_g"] *= structure_factor
            c["source"] = (
                f"{c.get('source', '')} Calibrated with built weights (x {structure_factor:.3f})."
            ).strip()
        if math.isfinite(c["mass_g"]) and c["mass_g"] > 0:
            comps.append(c)

    scale_word = (
        "carbon-fibre composite" if scale == "final" else "3D-printed lightweight (foaming) PLA"
    )
    wing = g["wing"]
    add(
        key="wing_structure",
        label="Wing structure (skins, ribs)",
        group="structure",
        mass_g=wing["area_m2"] * dens["wing"] * 1000,
        uncertainty=dens_u["wing"],
        x_mm=wing["mac_x_le_mm"] + 0.5 * wing["mac_mm"],
        source=f"Areal density {dens['wing']} kg/m² of wing area for {scale_word} (estimate; "
        + (
            "placeholder to calibrate with Phase 6 built weights"
            if scale == "prototype"
            else "composite lay-up estimate"
        )
        + ").",
        explain="The wing shell and ribs, estimated from the wing area times a typical weight "
        "per square metre for this kind of construction.",
    )
    t_root_mm = wing["thickness_ratio"] * wing["root_chord_mm"]
    spar_x = wing["mac_x_le_mm"] + wing["x_max_thickness"] * wing["mac_mm"]
    spar_info: dict[str, Any]
    spar_part = part("spar_tube")
    if scale == "prototype" and spar_part is not None:
        tube = {"outer_mm": spar_part["outer_mm"], "wall_mm": spar_part["wall_mm"]}
        add(
            key="wing_spar",
            label=f"Wing spar ({spar_part['label']}, full span)",
            group="structure",
            mass_g=spar_part["mass_per_m_g"] * wing["span_mm"] / 1000,
            uncertainty=PART_MASS_UNCERTAINTY,
            x_mm=spar_x,
            source=part_src(spar_part, f"{spar_part['mass_per_m_g']:g} g/m x the span"),
            explain="The carbon tube running through the wing that carries the bending load.",
        )
        spar_info = {"kind": "tube", **tube, "sized_by_structure": False, "catalogue": True}
    elif scale == "prototype":
        tube = spar_tube or default_spar_tube(g)
        sized = spar_tube is not None
        add(
            key="wing_spar",
            label=f"Wing spar (carbon tube {tube['outer_mm']:g} x {tube['wall_mm']:.1f} mm "
            "wall, full span)",
            group="structure",
            mass_g=carbon_tube_mass_per_m(tube["outer_mm"], tube["wall_mm"])
            * wing["span_mm"]
            / 1000,
            uncertainty=CARBON_TUBE_UNCERTAINTY,
            x_mm=spar_x,
            source="Carbon tube mass per metre from tube geometry (1550 kg/m³); "
            + (
                "tube sized by the engine's spar bending check (Phase 3 structure)."
                if sized
                else "diameter 70 % of the root thickness (Tier 1 starting guess)."
            ),
            explain="The carbon tube running through the wing that carries the bending load.",
        )
        spar_info = {"kind": "tube", **tube, "sized_by_structure": sized}
    else:
        semi_m = wing["span_mm"] / 2000
        moment = SPAR_ULTIMATE_LOAD_FACTOR * (weight / 2) * ELLIPTIC_CENTROID * semi_m
        depth_m = SPAR_DEPTH_FRACTION * t_root_mm / 1000
        cap_area = moment / (SPAR_ALLOWABLE_STRESS_PA * depth_m) if depth_m > 0 else 0.0
        mass_kg = 2 * 2 * CARBON_TUBE_DENSITY * cap_area * (semi_m / 3) * SPAR_EXTRA_FACTOR
        add(
            key="wing_spar",
            label="Wing spar caps (carbon, bending-sized)",
            group="structure",
            mass_g=mass_kg * 1000,
            uncertainty=0.35,
            x_mm=spar_x,
            source="Spar caps sized for root bending at an ultimate load factor of 6 (4 x 1.5) "
            "with an elliptic lift distribution, 600 MPa allowable, x1.3 for webs and joints "
            "(estimate).",
            explain="The carbon spar caps that carry the wing bending load, sized from the weight "
            "and span.",
        )
        spar_info = {"kind": "caps", "cap_area_m2": cap_area, "depth_m": depth_m}
    add(
        key="fuselage_structure",
        label="Fuselage shell and frames",
        group="structure",
        mass_g=g["fuselage"]["wetted_area_m2"] * dens["fuselage"] * 1000,
        uncertainty=dens_u["fuselage"],
        x_mm=_fuselage_wetted_centroid_x(g),
        source=f"Areal density {dens['fuselage']} kg/m² of fuselage surface for {scale_word} "
        "(estimate).",
        explain="The fuselage skin, internal frames and mounts, estimated from its surface area.",
    )
    tail = g["tail"]
    add(
        key="tail_structure",
        label="Tail surfaces",
        group="structure",
        mass_g=tail["planform_area_m2"] * dens["tail"] * 1000,
        uncertainty=dens_u["tail"],
        x_mm=tail["quarter_chord_x_mm"] + 0.25 * tail["chord_mm"],
        source=f"Areal density {dens['tail']} kg/m² of tail panel area for {scale_word} "
        "(estimate).",
        explain="The tail panels that keep the aircraft pointing straight and level.",
    )
    boom_part = part("boom_tube")
    boom_per_m = (
        boom_part["mass_per_m_g"]
        if boom_part is not None
        else carbon_tube_mass_per_m(p["booms"]["diameter_mm"])
    )
    boom_count = max(1, round(p["booms"]["count"]))
    add(
        key="booms",
        label=(
            f"Motor booms ({boom_count} x {boom_part['label']})"
            if boom_part is not None
            else f"Motor booms ({boom_count} carbon tubes, {p['booms']['diameter_mm']:g} mm)"
        ),
        group="structure",
        mass_g=boom_count * boom_per_m * p["booms"]["length_mm"] / 1000,
        uncertainty=PART_MASS_UNCERTAINTY if boom_part is not None else CARBON_TUBE_UNCERTAINTY,
        x_mm=g["booms"][0]["start"][0] + p["booms"]["length_mm"] / 2,
        source=(
            part_src(boom_part, f"{boom_per_m:g} g/m x the boom length")
            if boom_part is not None
            else "Carbon tube mass per metre from diameter (tube geometry, 1550 kg/m³, wall "
            "max(1 mm, 5 % of D))."
        ),
        explain="The carbon tubes that hold the four lift motors.",
    )
    if tail["support_length_mm"] > 0:
        n = boom_count if tail["support_kind"] == "booms" else 1
        start = (
            g["booms"][0]["end"][0]
            if tail["support_kind"] == "booms"
            else g["fuselage"]["length_mm"]
        )
        add(
            key="tail_support",
            label="Boom extensions to the tail" if tail["support_kind"] == "booms" else "Tail boom",
            group="structure",
            mass_g=n * boom_per_m * tail["support_length_mm"] / 1000,
            uncertainty=CARBON_TUBE_UNCERTAINTY,
            x_mm=start + tail["support_length_mm"] / 2,
            source="Carbon tube of the boom diameter; mass per metre from tube geometry.",
            explain="Tube needed to carry the tail behind the fuselage or booms.",
        )

    total_g = mtow_kg * 1000
    d_payload = mission["payload_max_g"] - mission["payload_min_g"]
    nose_x = p["nose_bay"]["length_mm"] / 2
    cg_min_guess = (
        (total_g * cg_guess - d_payload * nose_x) / (total_g - d_payload)
        if total_g - d_payload > 0
        else cg_guess
    )
    xf, xr = g["front_rotor_x_mm"], g["rear_rotor_x_mm"]
    share_max = clamp(front_thrust_share(cg_guess, xf, xr), 0, 1)
    share_min = clamp(front_thrust_share(cg_min_guess, xf, xr), 0, 1)
    worst = max(share_max, 1 - share_max, share_min, 1 - share_min)
    tw = settings["checks"]["hover_thrust_to_weight_min"]
    dia = p["propulsion"]["prop_diameter_mm"]
    disc = math.pi * (dia / 2000) ** 2
    motor_max_thrust = tw * weight * (1 + HOVER_DOWNLOAD_FRACTION) * worst / 2
    motor_max_power = (
        ideal_hover_power(motor_max_thrust, disc) / FIGURE_OF_MERIT_MAX / ETA_MOTOR_MAX
    )
    motor_g = motor_mass_g(motor_max_power)
    esc_rating = motor_max_power / pack["voltage"] * ESC_CURRENT_MARGIN
    esc_g = esc_mass_g(esc_rating)
    prop_g = prop_mass_g(dia, p["propulsion"]["prop_blades"])
    m_part, e_part, pr_part = part("lift_motor"), part("esc"), part("lift_prop")
    if m_part is not None:
        motor_g = m_part["mass_g"]
    if e_part is not None:
        esc_g = e_part["mass_g"]
    if pr_part is not None:
        prop_g = pr_part["mass_g"]
    for pos, x in (("front", xf), ("rear", xr)):
        add(
            key=f"motors_{pos}",
            label=f"Lift motors, {pos} pair"
            + (f" ({m_part['label']})" if m_part is not None else ""),
            group="propulsion",
            mass_g=2 * motor_g,
            uncertainty=PART_MASS_UNCERTAINTY if m_part is not None else MOTOR_MASS_UNCERTAINTY,
            x_mm=x,
            source=part_src(m_part, f"{motor_g:g} g each")
            if m_part is not None
            else f"Statistical motor mass 1.2 x P^0.73 g for {round(motor_max_power)} W each, "
            f"sized so the four motors give {tw:g} x the weight (momentum theory, figure of "
            "merit 0.55 at full power).",
            explain="Two of the four lift motors, sized so that together they lift the aircraft "
            "with the thrust-to-weight margin set in Settings.",
        )
        add(
            key=f"escs_{pos}",
            label=f"ESCs, {pos} pair" + (f" ({e_part['label']})" if e_part is not None else ""),
            group="propulsion",
            mass_g=2 * esc_g,
            uncertainty=PART_MASS_UNCERTAINTY if e_part is not None else ESC_MASS_UNCERTAINTY,
            x_mm=x,
            source=part_src(e_part, f"{esc_g:g} g each")
            if e_part is not None
            else "Statistical ESC mass 1.0 g per amp of rating + 5 g; rating 1.2 x the "
            "full-power current.",
            explain="The speed controllers that drive the motors.",
        )
        add(
            key=f"props_{pos}",
            label=f"Lift propellers, {pos} pair"
            + (f" ({pr_part['label']})" if pr_part is not None else ""),
            group="propulsion",
            mass_g=2 * prop_g,
            uncertainty=PART_MASS_UNCERTAINTY if pr_part is not None else PROP_MASS_UNCERTAINTY,
            x_mm=x,
            source=part_src(pr_part, f"{prop_g:g} g each")
            if pr_part is not None
            else "Statistical propeller mass 20 g x (D / 305 mm)^2.5 per two blades.",
            explain="Two lift propellers, estimated from their diameter.",
        )
        add(
            key=f"mounts_{pos}",
            label=f"Motor mounts, {pos} pair",
            group="structure",
            mass_g=2 * MOTOR_MOUNT_FRACTION * motor_g,
            uncertainty=0.5,
            x_mm=x,
            source="Mount mass 20 % of motor mass (estimate).",
            explain="Brackets that fix the motors to the booms.",
        )
    if p["layout"] != "quad_pusher":
        per_side = TILT_MECH_FIXED_G + TILT_MECH_FRACTION * (motor_g + prop_g)
        s_part = part("tilt_servo")
        source = "Per side 25 g + 25 % of the tilted motor and propeller mass (estimate)."
        if s_part is not None:
            per_side = s_part["mass_g"] + TILT_HINGE_HARDWARE_G
            source = part_src(s_part, f"{s_part['mass_g']:g} g per servo") + (
                f" Plus {TILT_HINGE_HARDWARE_G:g} g of hinge, bearing and linkage hardware per "
                "side (estimate)."
            )
        hinge_x = g["tilt_hinge_x_mm"]
        add(
            key="tilt_mechanism",
            label="Tilt mechanism (2 servos and hinges)"
            + (f" ({s_part['label']})" if s_part is not None else ""),
            group="systems",
            mass_g=2 * per_side,
            uncertainty=0.15 if s_part is not None else 0.5,
            x_mm=hinge_x if hinge_x is not None else (xf if p["layout"] == "front_tilt" else xr),
            source=source,
            explain="The servos, hinges and bearings that tilt the motors for wing flight.",
        )
    pusher_power = 0.0
    pusher_motor = 0.0
    if p["layout"] == "quad_pusher":
        dp = p["pusher"]["prop_diameter_mm"]
        area = math.pi * (dp / 2000) ** 2
        pusher_power = (
            ideal_hover_power(PUSHER_THRUST_TO_WEIGHT * weight, area)
            / FIGURE_OF_MERIT_PUSHER_STATIC
            / ETA_MOTOR_MAX
        )
        pusher_motor = motor_mass_g(pusher_power)
        pm_part, pp_part = part("cruise_motor"), part("pusher_prop")
        if pm_part is not None:
            pusher_motor = pm_part["mass_g"]
        px = p["pusher"]["x_mm"]
        add(
            key="pusher_motor",
            label="Pusher motor" + (f" ({pm_part['label']})" if pm_part is not None else ""),
            group="propulsion",
            mass_g=pusher_motor,
            uncertainty=PART_MASS_UNCERTAINTY if pm_part is not None else MOTOR_MASS_UNCERTAINTY,
            x_mm=px,
            source=part_src(pm_part, f"{pusher_motor:g} g")
            if pm_part is not None
            else f"Statistical motor mass for {round(pusher_power)} W, sized for a static "
            f"thrust of {PUSHER_THRUST_TO_WEIGHT} x the weight (momentum theory, FM 0.6).",
            explain="The separate motor that pushes the aircraft in wing flight.",
        )
        add(
            key="pusher_esc",
            label="Pusher ESC" + (f" ({e_part['label']})" if e_part is not None else ""),
            group="propulsion",
            mass_g=esc_g
            if e_part is not None
            else esc_mass_g(pusher_power / pack["voltage"] * ESC_CURRENT_MARGIN),
            uncertainty=PART_MASS_UNCERTAINTY if e_part is not None else ESC_MASS_UNCERTAINTY,
            x_mm=px,
            source=part_src(e_part, f"{esc_g:g} g")
            if e_part is not None
            else "Statistical ESC mass 1.0 g per amp + 5 g.",
            explain="The speed controller for the pusher motor.",
        )
        add(
            key="pusher_prop",
            label="Pusher propeller" + (f" ({pp_part['label']})" if pp_part is not None else ""),
            group="propulsion",
            mass_g=pp_part["mass_g"] if pp_part is not None else prop_mass_g(dp, 2),
            uncertainty=PART_MASS_UNCERTAINTY if pp_part is not None else PROP_MASS_UNCERTAINTY,
            x_mm=px,
            source=part_src(pp_part, f"{pp_part['mass_g']:g} g")
            if pp_part is not None
            else "Statistical propeller mass 20 g x (D / 305 mm)^2.5.",
            explain="The pusher propeller.",
        )
        add(
            key="pusher_mount",
            label="Pusher motor mount",
            group="structure",
            mass_g=MOTOR_MOUNT_FRACTION * pusher_motor,
            uncertainty=0.5,
            x_mm=px,
            source="Mount mass 20 % of motor mass (estimate).",
            explain="The bracket that holds the pusher motor.",
        )

    servo_each = CONTROL_SERVO_FIXED_G + CONTROL_SERVO_PER_TAKEOFF_G * mtow_kg * 1000
    add(
        key="servos_wing",
        label="Aileron servos (2)",
        group="systems",
        mass_g=CONTROL_SERVO_COUNT / 2 * servo_each,
        uncertainty=0.4,
        x_mm=wing["mac_x_le_mm"] + 0.7 * wing["mac_mm"],
        source="Each 6 g + 0.25 % of take-off mass (estimate).",
        explain="Servos that move the ailerons.",
    )
    add(
        key="servos_tail",
        label="Tail servos (2)",
        group="systems",
        mass_g=CONTROL_SERVO_COUNT / 2 * servo_each,
        uncertainty=0.4,
        x_mm=tail["le_x_mm"],
        source="Each 6 g + 0.25 % of take-off mass (estimate).",
        explain="Servos that move the tail control surfaces.",
    )
    av_part = part("avionics")
    add(
        key="avionics",
        label="Avionics" + (f" ({av_part['label']})" if av_part is not None else " allowance"),
        group="systems",
        mass_g=av_part["mass_g"] if av_part is not None else p["allowances"]["avionics_g"],
        uncertainty=0.1 if av_part is not None else ALLOWANCE_UNCERTAINTY,
        x_mm=p["wing"]["x_le_mm"],
        source=(
            f"Selected catalogue parts: {av_part['detail']} (manufacturer figures), under the "
            "wing leading edge."
            if av_part is not None
            else "Owner allowance (allowances.avionics_g), under the wing leading edge; "
            "replaced by the selected parts when a parts list exists (Phase 4)."
        ),
        explain="Autopilot, GPS, receiver, telemetry radio and power module.",
    )
    gear = p["landing_gear"]["type"]
    add(
        key="landing_gear",
        label=f"Landing gear ({gear})",
        group="structure",
        mass_g=LANDING_GEAR_FRACTION[gear] * mtow_kg * 1000,
        uncertainty=0.5,
        x_mm=(xf + xr) / 2,
        source=f"{LANDING_GEAR_FRACTION[gear] * 100:g} % of take-off mass for {gear} (estimate).",
        explain="The skids or legs the aircraft stands and lands on.",
    )
    chem = "LiPo" if p["battery"]["chemistry"] == "lipo" else "Li-ion"
    b_part = part("battery")
    add(
        key="battery",
        label=f"Battery {p['battery']['cells_series']}S{p['battery']['cells_parallel']}P {chem}"
        + (f" ({b_part['label']})" if b_part is not None else ""),
        group="energy",
        mass_g=b_part["mass_g"] if b_part is not None else pack["mass_g"],
        uncertainty=(
            PART_MASS_UNCERTAINTY if b_part is not None else PACK_SPECIFIC_ENERGY_UNCERTAINTY
        ),
        x_mm=p["battery"]["x_mm"],
        source=part_src(b_part, f"{b_part['mass_g']:g} g")
        if b_part is not None
        else f"Pack energy {pack['energy_wh']:.0f} Wh at "
        f"{PACK_SPECIFIC_ENERGY_WH_PER_KG[p['battery']['chemistry']]:g} Wh/kg pack-level "
        "specific energy (typical datasheet value).",
        explain="The flight battery, from its energy and a typical energy per kilogram.",
    )
    empty_parts = [c for c in comps if c["group"] != "energy"]
    empty_no_wiring = sum(c["mass_g"] for c in empty_parts)
    empty_cg = centre_of_gravity(empty_parts)["x"]
    f = clamp(p["allowances"]["wiring_fraction"], 0, 0.5)
    add(
        key="wiring",
        label="Wiring, connectors and fasteners",
        group="systems",
        mass_g=f / (1 - f) * empty_no_wiring,
        uncertainty=ALLOWANCE_UNCERTAINTY,
        x_mm=empty_cg,
        source=f"Owner allowance: {f * 100:.1f} % of the empty mass (allowances.wiring_fraction).",
        explain="Wires, plugs, screws and glue, as a fraction of the empty aircraft.",
    )
    add(
        key="payload",
        label="Nose-bay payload (camera)",
        group="payload",
        mass_g=mission["payload_max_g"],
        uncertainty=0.0,
        x_mm=nose_x,
        source="Mission payload range, placed at the centre of the nose bay.",
        explain="The camera in the nose bay (heaviest payload; balance is also checked with "
        "the lightest).",
    )
    return {
        "components": comps,
        "lift_motor_max_power_w": motor_max_power,
        "lift_motor_mass_g": motor_g,
        "lift_motor_max_thrust_n": motor_max_thrust,
        "lift_esc_rating_a": esc_rating,
        "pusher_max_power_w": pusher_power,
        "pusher_motor_mass_g": pusher_motor,
        "spar": spar_info,
    }


def solve_mass(
    p: dict[str, Any],
    g: dict[str, Any],
    mission: dict[str, Any],
    settings: dict[str, Any],
    spar_tube: dict[str, float] | None = None,
    start_kg: float | None = None,
    parts: dict[str, Any] | None = None,
    structure_factor: float = 1.0,
) -> dict[str, Any]:
    """Iterate the build-up to a fixed point at the maximum payload (as Tier 1).

    ``parts`` (Phase 4, optional): selected catalogue masses by key, each ``{mass_g, label}``:
    ``lift_motor``, ``lift_prop``, ``esc`` (each), ``cruise_motor``, ``pusher_prop``,
    ``tilt_servo`` (each), ``avionics`` (total, plus ``detail``), ``battery`` (pack), and the
    tubes ``spar_tube`` / ``boom_tube`` with ``outer_mm``, ``wall_mm`` and ``mass_per_m_g``.

    ``structure_factor`` (Phase 6): the applied structural-mass calibration from built weights;
    every structure component is multiplied by it.
    """
    target = mission.get("target_takeoff_mass_kg") or 2.5
    m = start_kg if start_kg and start_kg > 0 else (target if target > 0 else 2.5)
    cg = (g["front_rotor_x_mm"] + g["rear_rotor_x_mm"]) / 2
    build = _build(p, g, mission, settings, m, cg, spar_tube, parts, structure_factor)
    converged = False
    iterations = 0
    for i in range(MASS_MAX_ITERATIONS):
        iterations = i + 1
        c = centre_of_gravity(build["components"])
        m_new = c["total"] / 1000
        d_g = abs(m_new - m) * 1000
        d_cg = abs(c["x"] - cg)
        m, cg = m_new, c["x"]
        if not math.isfinite(m) or m > 1e4:
            break
        build = _build(p, g, mission, settings, m, cg, spar_tube, parts, structure_factor)
        if d_g < MASS_TOLERANCE_G and d_cg < 0.05:
            converged = True
            break
    comps = build["components"]
    comps_min = [
        {**c, "mass_g": mission["payload_min_g"]} if c["key"] == "payload" else c for c in comps
    ]
    comps_min = [c for c in comps_min if c["mass_g"] > 0]
    c_max = centre_of_gravity(comps)
    c_min = centre_of_gravity(comps_min)
    sigma = mass_sigma(comps)
    total_max = c_max["total"]
    total_min = c_min["total"]

    def total(pred: Any) -> float:
        return sum(c["mass_g"] for c in comps if pred(c))

    battery = total(lambda c: c["group"] == "energy")
    empty = total(lambda c: c["group"] not in ("energy", "payload"))
    structure = total(lambda c: c["group"] == "structure")
    struct_sigma = sum(c["uncertainty"] * c["mass_g"] for c in comps if c["group"] == "structure")
    empty_sigma = mass_sigma([c for c in comps if c["group"] not in ("energy", "payload")])
    pack = pack_electrics(p)

    def mk(value_g: float, sig_g: float, label: str, explain: str, source: str) -> dict[str, Any]:
        return q_range(
            value_g / 1000,
            (value_g - sig_g) / 1000,
            (value_g + sig_g) / 1000,
            "kg",
            label,
            explain,
            source,
        )

    result = {
        "components": comps,
        "empty": mk(
            empty,
            empty_sigma,
            "Empty mass",
            "Everything except the battery and the camera. Every gram here costs endurance.",
            "Component build-up (Tier 1 mass model, docs/ENGINE.md); range from "
            "per-component uncertainties.",
        ),
        "battery": q_rel(
            battery / 1000,
            PACK_SPECIFIC_ENERGY_UNCERTAINTY,
            "kg",
            "Battery mass",
            f"The {pack['energy_wh']:.0f} Wh battery's weight.",
            "Pack energy / pack-level specific energy (+/-10 %).",
        ),
        "structure": mk(
            structure,
            struct_sigma,
            "Structure mass",
            "Wing, fuselage, tail, booms, mounts and landing gear. First estimates "
            "until your built weights correct them.",
            "Areal and linear densities by scale; uncertainties added linearly.",
        ),
        "takeoff_max_payload": mk(
            total_max,
            sigma,
            "Take-off mass (heaviest camera)",
            "Total weight ready to fly with the heaviest camera. It sets the "
            "stall speed, the hover power and the legal limit check.",
            f"Component build-up iterated to convergence ({iterations} "
            "passes); structure uncertainties linear, others root-sum-square.",
        ),
        "takeoff_min_payload": mk(
            total_min,
            sigma,
            "Take-off mass (lightest camera)",
            "Total weight with the lightest camera fitted.",
            "Same build-up with the minimum payload.",
        ),
        "structure_fraction": q_rel(
            structure / total_max,
            struct_sigma / max(structure, 1e-9),
            "",
            "Structure fraction",
            "Share of the take-off weight that is airframe; small drones are typically 25-35 %.",
            "Structure mass / take-off mass (Gundlach 2014 ch. 8).",
        ),
        "converged": converged,
        "iterations": iterations,
    }
    xf, xr = g["front_rotor_x_mm"], g["rear_rotor_x_mm"]
    return {
        "result": result,
        "components_min": comps_min,
        "front_share_max": front_thrust_share(c_max["x"], xf, xr),
        "front_share_min": front_thrust_share(c_min["x"], xf, xr),
        "cg_max_x": c_max["x"],
        "cg_min_x": c_min["x"],
        "cg_max_sigma": c_max["sigma"],
        "cg_min_sigma": c_min["sigma"],
        "mass_rel": sigma / total_max if total_max > 0 else float("nan"),
        "total_max_g": total_max,
        "total_min_g": total_min,
        "pack": pack,
        "lift_motor_mass_g": build["lift_motor_mass_g"],
        "lift_motor_max_power_w": build["lift_motor_max_power_w"],
        "lift_motor_max_thrust_n": build["lift_motor_max_thrust_n"],
        "lift_esc_rating_a": build["lift_esc_rating_a"],
        "pusher_max_power_w": build["pusher_max_power_w"],
        "pusher_motor_mass_g": build["pusher_motor_mass_g"],
        "spar": build["spar"],
    }


def cg_with_payload(sol: dict[str, Any], payload_g: float) -> tuple[float, float]:
    """CG x (mm) and total mass (g) for any payload, from the converged components."""
    comps = [
        {**c, "mass_g": payload_g} if c["key"] == "payload" else c
        for c in sol["result"]["components"]
    ]
    comps = [c for c in comps if c["mass_g"] > 0]
    c = centre_of_gravity(comps)
    return c["x"], c["total"]
