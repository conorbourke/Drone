"""Parasite drag build-up with an itemised table (Raymer ch. 12.5 component method).

Wing and tail profile drag come from XFOIL polars strip-integrated with AVL's local lift
coefficients (``polars.strip_profile_drag``); everything else uses the Tier 1 relations so the two
engines can be compared item by item:

* skin friction: Blasius laminar 1.328/sqrt(Re) and Raymer's turbulent 0.455/((log10 Re)^2.58
  (1 + 0.144 M^2)^0.65) mixed by a laminar fraction, with the roughness cutoff Reynolds number
  38.21 (l/k)^1.053 (Raymer eqs. 12.25-12.28; Prandtl-Schlichting form);
* body form factor 0.9 + 5/f^1.5 + f/400 (Raymer eq. 12.31), interference factors Q (Raymer
  12.5.4);
* stopped propellers as edge-on flat plates (Cd 1.2, Hoerner), averaged over the stop position
  (2/pi); motor cans (Cd 0.8 side-on stopped, 0.3 face-on running; Hoerner); landing-gear struts
  (Cd 1.0, Hoerner); tilt hinge and servo fairings (new in Tier 2, estimate below);
* 10 % for seams, hatches and protuberances (Raymer 12.5.5: 2-10 %).

The nose bay is part of the fuselage loft (geometry module) and so of the fuselage row.
"""

from __future__ import annotations

import math
from typing import Any

from app.engine.quantity import MU_SL, RHO_SL, mach

LAMINAR_FRACTION = {
    "prototype": {"wing": 0.15, "tail": 0.15, "body": 0.05},
    "final": {"wing": 0.35, "tail": 0.35, "body": 0.10},
}
SURFACE_ROUGHNESS_M = {"prototype": 4.05e-5, "final": 5.2e-7}
FORM_FACTOR_MACH_FLOOR = 0.2
INTERFERENCE = {
    "wing": 1.0,
    "fuselage": 1.0,
    "tail_conventional": 1.05,
    "tail_v": 1.03,
    "tail_h": 1.08,
    "boom": 1.1,
}
LEAKAGE_PROTUBERANCE_FRACTION = 0.10
CD_FLAT_PLATE = 1.2
PROP_MEAN_CHORD_FRACTION = 0.08
PROP_BLADE_THICKNESS_RATIO = 0.12
PROP_BLADE_RADIUS_FRACTION = 0.85
PROP_AZIMUTH_AVERAGE = 2 / math.pi
CD_MOTOR_CAN_SIDE = 0.8
CD_MOTOR_CAN_FRONT = 0.3
MOTOR_CAN_HEIGHT_RATIO = 0.6
MOTOR_CAN_DENSITY = 3500.0
CD_ROUND_STRUT = 1.0
GEAR_STRUT_DIAMETER_FRACTION = 0.06
#: Tilt hinge block and servo per tilting side: frontal area 1.5 x boom diameter x motor-can
#: diameter, Cd 0.5 (Hoerner ch. 3, blunt three-dimensional bodies 0.4-0.8). ESTIMATE, +/-50 %.
TILT_FAIRING_AREA_FACTOR = 1.5
CD_TILT_FAIRING = 0.5
#: Profile-drag uncertainty of XFOIL strip integration: low -10 %, high +30 % for printed
#: surfaces (layer lines and seams can trip the boundary layer early; XFOIL assumes a smooth
#: section), +/-10 % for moulded carbon (Selig et al. wind-tunnel vs XFOIL agreement).
PROFILE_UNCERTAINTY = {"prototype": (0.10, 0.30), "final": (0.10, 0.10)}
#: Non-lifting build-up uncertainty, +/-20 % (Raymer: component build-ups within 10-20 %; stopped
#: propellers and fairings carry +/-50 % but are a minority of the total).
PARASITE_UNCERTAINTY = 0.20


def reynolds(speed: float, length_m: float) -> float:
    return RHO_SL * speed * length_m / MU_SL


def cf_laminar(re: float) -> float:
    return 1.328 / math.sqrt(re)


def cf_turbulent(re: float, m: float = 0.0) -> float:
    return 0.455 / (math.log10(re) ** 2.58 * (1 + 0.144 * m * m) ** 0.65)


def cutoff_reynolds(length_m: float, k: float) -> float:
    return 38.21 * (length_m / k) ** 1.053


def skin_friction(re: float, m: float, lam: float, length_m: float, k: float) -> float:
    re_t = min(re, cutoff_reynolds(length_m, k))
    return lam * cf_laminar(re) + (1 - lam) * cf_turbulent(re_t, m)


def form_factor_lifting(tc: float, xt: float, m: float, sweep_rad: float) -> float:
    mm = max(m, FORM_FACTOR_MACH_FLOOR)
    return (1 + 0.6 / xt * tc + 100 * tc**4) * (1.34 * mm**0.18 * math.cos(sweep_rad) ** 0.28)


def form_factor_body(f: float) -> float:
    return 0.9 + 5 / f**1.5 + f / 400


def stopped_prop_drag_area(diameter_mm: float, pitch_mm: float, blades: int) -> float:
    d = diameter_mm / 1000
    c = PROP_MEAN_CHORD_FRACTION * d
    theta = math.atan(pitch_mm / 1000 / (0.75 * math.pi * d))
    h = c * math.sin(theta) + PROP_BLADE_THICKNESS_RATIO * c * math.cos(theta)
    return (
        CD_FLAT_PLATE
        * max(1, blades)
        * PROP_BLADE_RADIUS_FRACTION
        * (d / 2)
        * h
        * PROP_AZIMUTH_AVERAGE
    )


def motor_can_diameter(mass_g: float) -> float:
    v = mass_g / 1000 / MOTOR_CAN_DENSITY
    return (v / (math.pi / 4 * MOTOR_CAN_HEIGHT_RATIO)) ** (1 / 3) if v > 0 else 0.0


def tail_interference(tail_type: str) -> float:
    if tail_type in ("v_tail", "inverted_v"):
        return INTERFERENCE["tail_v"]
    if tail_type == "twin_boom_h":
        return INTERFERENCE["tail_h"]
    return INTERFERENCE["tail_conventional"]


def tier1_lifting_friction(g: dict[str, Any], speed: float, scale: str) -> dict[str, float]:
    """Tier 1 flat-plate wing and tail profile drag (CD on S_ref), for the comparison table."""
    m = mach(speed)
    lam = LAMINAR_FRACTION[scale]
    k = SURFACE_ROUGHNESS_M[scale]
    w, t = g["wing"], g["tail"]
    s = w["area_m2"]
    lw = w["mac_mm"] / 1000
    cw = skin_friction(reynolds(speed, lw), m, lam["wing"], lw, k)
    ffw = form_factor_lifting(
        w["thickness_ratio"], w["x_max_thickness"], m, math.radians(w["sweep_max_thickness_deg"])
    )
    lt = t["chord_mm"] / 1000
    ct = skin_friction(reynolds(speed, lt), m, lam["tail"], lt, k)
    fft = form_factor_lifting(t["thickness_ratio"], t["x_max_thickness"], m, 0.0)
    return {
        "wing": cw * ffw * INTERFERENCE["wing"] * w["wetted_area_m2"] / s,
        "tail": ct * fft * tail_interference(t["type"]) * t["wetted_area_m2"] / s,
    }


def drag_buildup(
    p: dict[str, Any],
    g: dict[str, Any],
    speed: float,
    scale: str,
    lift_motor_mass_g: float,
    wing_profile_cd: float,
    tail_profile_cd: float,
    profile_note: str = "",
) -> dict[str, Any]:
    """Itemised zero-lift and profile drag at ``speed``. CD values are on the wing area."""
    s_ref = g["wing"]["area_m2"]
    m = mach(speed)
    lam = LAMINAR_FRACTION[scale]
    k = SURFACE_ROUGHNESS_M[scale]
    items: list[dict[str, Any]] = []

    def area_item(
        key: str, label: str, area: float, source: str, explain: str, **extra: Any
    ) -> None:
        if area > 0:
            items.append(
                {
                    "key": key,
                    "label": label,
                    "drag_area_m2": area,
                    "cd": area / s_ref,
                    "source": source,
                    "explain": explain,
                    **extra,
                }
            )

    def friction(
        key: str,
        label: str,
        length_m: float,
        wet: float,
        lam_f: float,
        ff: float,
        q: float,
        explain: str,
    ) -> None:
        if wet <= 0 or length_m <= 0:
            return
        re = reynolds(speed, length_m)
        cf = skin_friction(re, m, lam_f, length_m, k)
        area_item(
            key,
            label,
            cf * ff * q * wet,
            "Raymer ch. 12.5 build-up: flat-plate skin friction (laminar fraction "
            f"{lam_f:.0%}) x form factor {ff:.2f} x interference {q:.2f} x wetted area "
            f"{wet:.3f} m².",
            explain,
            reynolds=re,
            cf=cf,
            form_factor=ff,
            interference=q,
            wetted_area_m2=wet,
        )

    area_item(
        "wing",
        "Wing (profile drag)",
        wing_profile_cd * s_ref,
        "XFOIL polars at the local Reynolds number of each AVL strip, integrated across the "
        f"span with AVL's local lift coefficients. {profile_note}".strip(),
        "Drag of the wing's surface and shape, which grows a little as the wing works "
        "harder. Smooth, accurate wing surfaces keep it low.",
    )
    q_tail = tail_interference(g["tail"]["type"])
    area_item(
        "tail",
        "Tail (profile drag)",
        tail_profile_cd * s_ref * q_tail,
        f"XFOIL tail polar strip-integrated with AVL's tail lift, x interference {q_tail:.2f} "
        "(Raymer 12.5.4).",
        "Drag of the tail surfaces.",
    )
    f = g["fuselage"]
    friction(
        "fuselage",
        "Fuselage (with nose bay)",
        f["length_mm"] / 1000,
        f["wetted_area_m2"],
        lam["body"],
        form_factor_body(f["fineness_ratio"]),
        INTERFERENCE["fuselage"],
        "Skin friction and shape drag of the fuselage, including the camera nose bay.",
    )
    n_booms = max(1, round(p["booms"]["count"]))
    d = p["booms"]["diameter_mm"]
    friction(
        "booms",
        "Motor booms",
        p["booms"]["length_mm"] / 1000,
        n_booms * math.pi * d * p["booms"]["length_mm"] / 1e6,
        lam["body"],
        form_factor_body(p["booms"]["length_mm"] / d),
        INTERFERENCE["boom"],
        "Skin friction of the two carbon booms.",
    )
    t = g["tail"]
    if t["support_length_mm"] > 0:
        n = n_booms if t["support_kind"] == "booms" else 1
        friction(
            "tail_support",
            "Boom extensions" if t["support_kind"] == "booms" else "Tail boom",
            t["support_length_mm"] / 1000,
            n * math.pi * d * t["support_length_mm"] / 1e6,
            lam["body"],
            form_factor_body(t["support_length_mm"] / d),
            INTERFERENCE["boom"],
            "The tube that carries the tail behind the fuselage or booms.",
        )
    rotors = [r for r in g["rotors"] if r["id"] != "pusher"]
    stopped = sum(1 for r in rotors if r["stopped_in_cruise"])
    running = len(rotors) - stopped
    pr = p["propulsion"]
    if stopped:
        area_item(
            "stopped_props",
            f"Stopped lift propellers ({stopped})",
            stopped
            * stopped_prop_drag_area(
                pr["prop_diameter_mm"], pr["prop_pitch_mm"], pr["prop_blades"]
            ),
            "Blades as flat plates edge-on (Cd 1.2, Hoerner), projected height at 0.75 R, "
            "averaged over the stop position (2/pi). Engine method, +/-50 %.",
            "Propellers that stop in wing flight still catch the air. Aligning them with "
            "the flow (ArduPilot can park them) or folding blades reduces this.",
        )
    dm = motor_can_diameter(lift_motor_mass_g)
    if dm > 0:
        side = stopped * CD_MOTOR_CAN_SIDE * dm * MOTOR_CAN_HEIGHT_RATIO * dm
        front = running * CD_MOTOR_CAN_FRONT * math.pi / 4 * dm * dm
        area_item(
            "motor_pods",
            "Lift motors and mounts",
            side + front,
            "Stopped motor cans as short cylinders side-on (Cd 0.8) and running tilted "
            "motors face-on behind the spinner (Cd 0.3), Hoerner; can size from motor mass.",
            "The motor cans sticking into the airflow.",
        )
    if p["layout"] != "quad_pusher" and dm > 0:
        a = 2 * CD_TILT_FAIRING * TILT_FAIRING_AREA_FACTOR * (d / 1000) * dm
        area_item(
            "tilt_hardware",
            "Tilt hinges and servos (2)",
            a,
            "Per side a blunt fairing of frontal area 1.5 x boom diameter x motor-can "
            "diameter, Cd 0.5 (Hoerner, blunt 3-D bodies 0.4-0.8). Estimate, +/-50 %.",
            "The hinge blocks and servos that tilt the motors.",
        )
    gear = p["landing_gear"]
    if gear["type"] != "none" and gear["height_mm"] > 0:
        strut = max(4.0, GEAR_STRUT_DIAMETER_FRACTION * gear["height_mm"]) / 1000
        area_item(
            "landing_gear",
            f"Landing gear ({gear['type']})",
            CD_ROUND_STRUT * 4 * strut * gear["height_mm"] / 1000,
            "Four round struts of 6 % of the gear height (min 4 mm), Cd 1.0 on frontal area "
            "(Hoerner, subcritical cylinder).",
            "The skids or legs hanging in the airflow.",
        )
    subtotal = sum(i["drag_area_m2"] for i in items)
    area_item(
        "leakage",
        "Seams, hatches and protuberances",
        LEAKAGE_PROTUBERANCE_FRACTION * subtotal,
        "Leakage and protuberance allowance, 10 % of the build-up (Raymer 12.5.5: 2-10 %).",
        "Gaps, screw heads, hatches and antennas.",
    )
    total = sum(i["drag_area_m2"] for i in items)
    for i in items:
        i["share"] = i["drag_area_m2"] / total if total > 0 else 0.0
    profile = sum(i["drag_area_m2"] for i in items if i["key"] in ("wing", "tail"))
    return {
        "items": items,
        "cd_zero_lift_and_profile": total / s_ref,
        "cd_profile": profile / s_ref,
        "cd_parasite_non_lifting": (total - profile) / s_ref,
        "drag_area_m2": total,
    }
