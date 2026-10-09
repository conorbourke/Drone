"""Engine-driven parts selection (docs/phases/PHASE4.md section 2).

Pure functions: a design (parameters, mission, settings), the generic-parts analysis of that
design (its fast path is enough) and the parts catalogue go in; a full parts set comes out, one
entry per role, each with plain reasoning sentences that quote the numbers, the runner-ups and
why they lost, flags, and a list of "upgrades worth paying for".

How each role is chosen (constants and their sources are below):

* Lift motor and propeller, chosen together: motors whose cell range includes the pack and
  propellers within 10 % of the design's diameter (or the nearest diameter available). Each pair
  is run through the Phase 3 motor model (Kv, R, I0 as published) and propeller model (static
  coefficients fitted to the motor's catalogue thrust tests for that propeller when they exist,
  else the generic UIUC-trend propeller). Constraints: at full throttle on the loaded pack the
  motor gives at least the hover thrust-to-weight minimum x the busier pair's hover thrust; the
  current at that thrust is within the motor's rating; an ESC exists for 1.25 x the peak
  current; propeller rpm limit; for tilt layouts the tilting pair also holds cruise thrust at
  85 % throttle or less. Ranked by hover efficiency (g/W, in 0.25 g/W steps), then mass, then
  price.
* Pusher motor and propeller (quad + pusher): thrust = cruise drag at cruise speed with throttle
  in the efficient band (at most 80 %) and 1.3 x drag available at full throttle; ranked by
  overall cruise efficiency, then mass, then price.
* ESC: continuous rating >= 1.25 x peak motor current, cell range, telemetry preferred, then
  mass and price. One per lift motor (and one for the pusher).
* Tilt servos: torque >= 2 x the hinge moment (full motor thrust x its offset from the hinge,
  plus the gyroscopic moment of the spinning rotor at a 100 deg/s body rate, plus 10 % for
  inertia and linkage friction) and speed <= 0.15 s/60 deg; lightest, then cheapest.
* Battery: packs with the design's series cell count, and custom packs of catalogue cells
  (S x 1..8 P, 8 % mass overhead for strip, wrap and leads); continuous current >= peak current /
  the settings fraction; the lightest that meets the endurance target, else the longest
  endurance. The pack-vs-custom Li-ion trade-off is reported.
* Autopilot, GPS, radio, telemetry: ArduPilot boards with two or more IMUs and enough outputs;
  GPS with a 10 Hz update; radios legal in Ireland (863-870 MHz SRD, 2.4 GHz at 100 mW EIRP; 433
  MHz only at 10 mW, flagged; 902-928 MHz excluded). Default = cheapest that meets the rules,
  alternatives labelled cheaper / premium.
* Carbon tubes: spar (prototype) at the analysis's ultimate root moment, outer diameter inside
  the root depth; booms at full motor thrust or the hard-landing case; margin of safety >= 0.25
  against min(500 MPa, half the published tensile strength); lightest per metre first.

Endurance effects (for the upgrade list and the totals) use the generic analysis split: hover
and transition energy scale with mass^1.5 and the hover efficiency, cruise power with
(1 - f_i) + f_i (m / m0)^2 (f_i the induced share of cruise drag). These are estimates for
ranking; the full analysis with the selected parts gives the real numbers.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.engine import battery as bat
from app.engine import propulsion as prp
from app.engine.analysis import AVIONICS_POWER_W
from app.engine.mass import HOVER_DOWNLOAD_FRACTION
from app.engine.quantity import G0
from app.engine.structure import MARGIN_WARN, TUBE_ALLOWABLE_PA, tube_section

SELECTION_VERSION = "selection-1"

ROLE_ORDER = (
    "lift_motor",
    "lift_prop",
    "cruise_motor",
    "pusher_prop",
    "esc",
    "tilt_servo",
    "battery",
    "autopilot",
    "gps",
    "radio",
    "telemetry",
    "spar_tube",
    "boom_tube",
)
ROLE_LABELS = {
    "lift_motor": "Lift motors",
    "lift_prop": "Lift propellers",
    "cruise_motor": "Pusher motor",
    "pusher_prop": "Pusher propeller",
    "esc": "ESCs",
    "tilt_servo": "Tilt servos",
    "battery": "Battery",
    "autopilot": "Autopilot",
    "gps": "GPS",
    "radio": "RC receiver",
    "telemetry": "Telemetry radio",
    "spar_tube": "Wing spar tube",
    "boom_tube": "Boom tubes",
}
ROLE_SYSTEM = {
    "lift_motor": "propulsion",
    "lift_prop": "propulsion",
    "cruise_motor": "propulsion",
    "pusher_prop": "propulsion",
    "esc": "propulsion",
    "tilt_servo": "flight_control",
    "battery": "energy",
    "autopilot": "flight_control",
    "gps": "flight_control",
    "radio": "flight_control",
    "telemetry": "flight_control",
    "spar_tube": "structure",
    "boom_tube": "structure",
}
SYSTEM_LABELS = {
    "propulsion": "Propulsion",
    "energy": "Energy",
    "flight_control": "Flight control",
    "structure": "Structure",
    "consumables": "Consumables",
}
#: Catalogue category allowed per role (a cell-based custom pack fills the battery role too).
ROLE_CATEGORIES = {
    "lift_motor": ("motor",),
    "lift_prop": ("propeller",),
    "cruise_motor": ("motor",),
    "pusher_prop": ("propeller",),
    "esc": ("esc",),
    "tilt_servo": ("servo",),
    "battery": ("battery", "cell"),
    "autopilot": ("autopilot",),
    "gps": ("gps",),
    "radio": ("radio",),
    "telemetry": ("telemetry",),
    "spar_tube": ("carbon_tube",),
    "boom_tube": ("carbon_tube",),
}

ESC_CURRENT_FACTOR = 1.25
SERVO_TORQUE_FACTOR = 2.0
SERVO_SPEED_MAX_S = 0.15
#: Body roll/pitch rate for the gyroscopic hinge load: 100 deg/s, a brisk VTOL attitude
#: correction (ArduPilot QuadPlane default rate limits are in this range); estimate.
GYRO_BODY_RATE_RAD_S = math.radians(100)
SERVO_INERTIA_ALLOWANCE = 0.10
TILT_CRUISE_THROTTLE_MAX = 0.85
PUSHER_THROTTLE_MAX = 0.80
PUSHER_THRUST_MARGIN = 1.3
PROP_DIAMETER_WINDOW = 0.10
#: Custom Li-ion pack: nickel strip, insulation, wrap and leads add about 8 % to the cell mass
#: (typical DIY 21700 packs; estimate).
CELL_PACK_OVERHEAD = 0.08
CELL_PACK_MAX_PARALLEL = 8
POWER_MODULE_G = 20.0
LISTING_STALE_DAYS = 30
UPGRADES_PER_ROLE = 3
#: Roles whose choice and the take-off mass drive each other; held in the final pass when the
#: mass iteration does not settle (the other roles are chosen again at the final mass).
COUPLED_ROLES = ("lift_motor", "lift_prop", "cruise_motor", "pusher_prop", "battery")
#: Selection and mass are iterated (parts change the mass, the mass changes the parts).
MASS_PASSES = 6
EFFICIENCY_STEP_G_PER_W = 0.25
#: Avionics outputs needed: four lift motors, four control surfaces, two tilt servos or one
#: pusher.
CONTROL_SURFACE_SERVOS = 4
TUBE_KNOCKDOWN = 0.5
#: Boom stiffness rule: tip deflection under full motor thrust at most 1 % of the arm (engine
#: rule for small VTOL booms: a soft boom couples motor vibration and attitude control;
#: estimate), with E = 100 GPa unless the tube publishes its modulus.
BOOM_DEFLECTION_MAX = 0.01
TUBE_E_GPA = 100.0
GPS_RATE_MIN_HZ = 10.0
TELEMETRY_RATE_MIN_KBPS = 32.0
CONSUMABLES = {
    "prototype": [
        ("Power module (current and voltage sensing for the autopilot)", 35.0),
        ("Main battery connectors and 10-12 AWG silicone wire", 35.0),
        ("Motor bullet connectors, heat-shrink and cable ties", 15.0),
        ("Servo extension leads", 20.0),
        ("Servo BEC (7.4-8.4 V supply for HV servos)", 20.0),
        ("Fasteners, threaded inserts and nylon hardware", 25.0),
    ],
}
CONSUMABLES_FINAL_FACTOR = 2.5
UK_IMPORT_NOTE = (
    "Most listings are UK shops. Since Brexit, a UK shop selling to Ireland should charge no UK "
    "VAT; Irish VAT (23 %) is then due on import, plus customs duty on goods not of UK origin, "
    "and the courier usually adds a handling fee. The listed UK prices include UK VAT (20 %), so "
    "the real landed cost can differ by a few percent either way. LiPo and Li-ion batteries "
    "often ship from the UK by road or sea only; check before ordering."
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def listing_is_stale(listing: dict[str, Any], now: datetime | None = None) -> bool:
    checked = _parse_dt(listing.get("last_checked_at"))
    if checked is None:
        return True
    return (now or _now()) - checked > timedelta(days=LISTING_STALE_DAYS)


def best_listing(part: dict[str, Any], now: datetime | None = None) -> dict[str, Any] | None:
    """Cheapest priced listing that is not known broken, preferring fresh and in-stock ones."""
    priced = [
        li
        for li in part.get("listings") or []
        if li.get("price_eur") is not None and li.get("url_ok") is not False
    ]
    if not priced:
        return None

    def key(li: dict[str, Any]) -> tuple[int, int, float]:
        return (
            1 if listing_is_stale(li, now) else 0,
            1 if li.get("in_stock") is False else 0,
            float(li["price_eur"]),
        )

    return min(priced, key=key)


def unit_price(part: dict[str, Any]) -> tuple[float | None, str | None]:
    """(price in euro, 'listing' | 'estimate' | None)."""
    li = best_listing(part)
    if li is not None:
        return float(li["price_eur"]), "listing"
    if part.get("price_eur_estimate") is not None:
        return float(part["price_eur_estimate"]), "estimate"
    return None, None


def part_name(part: dict[str, Any]) -> str:
    return f"{part['manufacturer']} {part['model']}"


def sold_as_pair(part: dict[str, Any]) -> bool:
    return "pair" in part["model"].lower()


def _fmt_g(grams: float) -> str:
    if grams >= 10000:
        return f"{grams / 1000:.1f} kg"
    return f"{grams:,.0f} g"


def _fmt_eur(eur: float | None) -> str:
    return "no price" if eur is None else f"EUR {eur:,.2f}"


def _pct(x: float) -> str:
    return f"{x * 100:.0f} %"


# ---------------------------------------------------------------------------
# Design context from the generic-parts analysis
# ---------------------------------------------------------------------------


def _q(result: dict[str, Any], *path: str) -> Any:
    node: Any = result
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if isinstance(node, dict) and "value" in node:
        return node["value"]
    return node


def design_context(
    parameters: dict[str, Any],
    mission: dict[str, Any],
    settings: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    """The numbers the selection needs, read from a (generic-parts) analysis result."""
    if not analysis.get("valid"):
        raise ValueError("The design could not be analysed, so no parts can be chosen.")
    p = parameters
    checks = settings["checks"]
    comps = {c["key"]: c["mass_g"] for c in analysis["mass"]["components"]}
    segments = {s["key"]: s for s in analysis["mission"]["payload_max"]["segments"]}
    drag = analysis.get("drag") or {}
    cd_total = drag.get("cd_total_cruise") or 0.0
    induced = drag.get("cd_induced") or 0.0
    envelope = (analysis.get("balance") or {}).get("cg_envelope") or []
    shares = [e["front_share"] for e in envelope] or [_q(analysis, "balance", "hover_front_share")]
    worst_share = max(max(s, 1 - s) for s in shares if s is not None)
    structure = analysis.get("structure") or {}
    spar = structure.get("wing_spar") or {}
    boom = structure.get("boom") or {}
    from app.engine.geometry import build_geometry

    geo = build_geometry(p)
    wing = p["wing"]
    t_ratio = geo["wing"]["thickness_ratio"]
    tail = geo["tail"]
    hover_power = _q(analysis, "propulsion", "hover_power") or 0.0
    vtol_wh = analysis["mission"]["payload_max"]["vtol_wh"]
    return {
        "layout": p["layout"],
        "scale": "final" if mission.get("scale") == "final" else "prototype",
        "mass_kg": _q(analysis, "summary", "takeoff_mass"),
        "worst_share": worst_share,
        "tw_min": float(checks["hover_thrust_to_weight_min"]),
        "reserve": float(checks["battery_reserve_fraction"]),
        "current_fraction": float(checks["battery_current_max_fraction_of_rating"]),
        "safety_factor": float(checks["structural_safety_factor"]),
        "cruise_speed_mps": float(mission["cruise_speed_mps"]),
        "endurance_target_min": float(mission["target_endurance_min"]),
        "cruise_drag_n": _q(analysis, "aero", "drag_cruise"),
        "induced_fraction": (induced / cd_total) if cd_total > 0 else 0.3,
        "cruise_power_w": segments["cruise"]["power_w"],
        "hover_power_w": hover_power,
        "vtol_wh": vtol_wh,
        "usable_wh": analysis["mission"]["payload_max"]["usable_wh"],
        "endurance_min": _q(analysis, "summary", "endurance_cruise"),
        "peak_current_a": _q(analysis, "battery", "peak_current"),
        "pack_energy_wh": (analysis.get("battery") or {}).get("pack", {}).get("energy_wh"),
        "avionics_w": None,
        "generic_masses": comps,
        "generic_total_g": analysis["mass"]["takeoff_max_payload"]["value"] * 1000,
        "spar_moment_ult_nm": spar.get("root_moment_ultimate_nm"),
        "spar_max_od_mm": 0.85 * t_ratio * wing["root_chord_mm"],
        "boom_arm_mm": boom.get("arm_mm"),
        "boom_landing_moment_ult_nm": boom.get("moment_landing_ultimate_nm") or 0.0,
        "tail_support_mm": tail.get("support_length_mm"),
        "tail_support_kind": tail.get("support_kind"),
        "endurance_source": "the generic-parts analysis",
    }


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class Choice:
    """The outcome for one role."""

    role: str
    part: dict[str, Any] | None
    quantity: int = 0
    line_mass_g: float = 0.0
    reasoning: list[str] = field(default_factory=list)
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    flags: list[dict[str, str]] = field(default_factory=list)
    locked: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)
    unfilled_reason: str | None = None
    # For upgrades: every feasible candidate with its estimated effect.
    feasible: list[dict[str, Any]] = field(default_factory=list)
    custom_pack: dict[str, Any] | None = None

    def flag(self, code: str, message: str) -> None:
        if not any(f["code"] == code and f["message"] == message for f in self.flags):
            self.flags.append({"code": code, "message": message})


def _part_flags(choice: Choice, part: dict[str, Any]) -> None:
    if not part.get("verified"):
        choice.flag(
            "unverified",
            "Specification not yet verified against the manufacturer page; check it before buying.",
        )
    listings = part.get("listings") or []
    if not listings:
        choice.flag("no_listing", "No Irish or UK listing is known for this part.")
    else:
        if best_listing(part) is None:
            choice.flag(
                "no_price",
                "No listing shows a price; "
                + (
                    "the total uses the catalogue estimate."
                    if part.get("price_eur_estimate") is not None
                    else "this line is not counted in the total."
                ),
            )
        if all(li.get("url_ok") is False for li in listings):
            choice.flag("links_broken", "None of the shop links answered at the last check.")
        if all(listing_is_stale(li) for li in listings):
            choice.flag(
                "stale",
                f"Prices and stock were last checked more than {LISTING_STALE_DAYS} days ago; "
                "refresh the listings.",
            )


# ---------------------------------------------------------------------------
# Propulsion evaluation
# ---------------------------------------------------------------------------


def _pack(design_battery: dict[str, Any]) -> dict[str, Any]:
    return bat.pack_model(design_battery)


def _bus(pack: dict[str, Any], power_w: float) -> float:
    return bat.loaded_voltage(pack, power_w)["voltage_v"]


@dataclass
class _Rotor:
    motor: dict[str, Any]
    prop: dict[str, Any]
    model_motor: prp.Motor
    model_prop: prp.Propeller
    fitted: bool


def _rotor(motor: dict[str, Any], prop: dict[str, Any]) -> _Rotor:
    spec_m = {**motor["spec"], "label": part_name(motor)}
    spec_p = {**prop["spec"], "label": part_name(prop)}
    mm = prp.catalogue_motor(spec_m)
    mp, fit = prp.catalogue_propeller(
        spec_p, spec_m, mm, prop["spec"]["diameter_mm"], prop["spec"]["pitch_mm"], 2
    )
    return _Rotor(motor, prop, mm, mp, fit is not None)


def _props_near(props: list[dict[str, Any]], diameter_mm: float) -> list[dict[str, Any]]:
    near = [
        pp
        for pp in props
        if abs(pp["spec"]["diameter_mm"] - diameter_mm) <= PROP_DIAMETER_WINDOW * diameter_mm
    ]
    if near:
        return near
    if not props:
        return []
    best = min(abs(pp["spec"]["diameter_mm"] - diameter_mm) for pp in props)
    return [pp for pp in props if abs(abs(pp["spec"]["diameter_mm"] - diameter_mm) - best) < 1.0]


def _evaluate_lift(
    rotor: _Rotor,
    ctx: dict[str, Any],
    pack: dict[str, Any],
    mass_kg: float,
    escs: list[dict[str, Any]],
    cells: int,
) -> dict[str, Any]:
    """Hover, full-throttle and (tilt) cruise operating points and the constraint checks."""
    w = mass_kg * G0
    t_hover = w * (1 + HOVER_DOWNLOAD_FRACTION) * ctx["worst_share"] / 2
    t_req = ctx["tw_min"] * t_hover
    spec = rotor.motor["spec"]
    out: dict[str, Any] = {"t_hover_n": t_hover, "t_req_n": t_req, "problems": []}
    v_hover = _bus(pack, ctx["hover_power_w"] * (mass_kg / ctx["mass_kg"]) ** 1.5)
    try:
        hov = prp.operating_point(rotor.model_prop, rotor.model_motor, t_hover, 0.0, v_hover)
        # Full throttle on the loaded pack: sag from the four motors' full-throttle current
        # (as the analysis does for catalogue motors).
        v_full = pack["v_nominal"]
        for _ in range(4):
            full = prp.max_thrust(rotor.model_prop, rotor.model_motor, 0.0, v_full)
            i_full = min(full["current_a"], spec["max_current_a"])
            v_full = _bus(pack, 4 * i_full * v_full / prp.ETA_ESC)
        full = prp.max_thrust(rotor.model_prop, rotor.model_motor, 0.0, v_full)
    except (ValueError, ZeroDivisionError, OverflowError):
        out["problems"].append("the motor model did not converge with this propeller")
        return out
    out["hover"] = hov
    out["full"] = full
    out["g_per_w"] = (t_hover / G0 * 1000) / hov["motor_power_w"] if hov["motor_power_w"] else 0
    out["max_thrust_g"] = full["thrust_n"] / G0 * 1000
    out["hover_fraction"] = t_hover / full["thrust_n"] if full["thrust_n"] > 0 else math.inf
    if not hov["feasible"]:
        out["problems"].append(
            f"cannot even hover: it needs {_pct(hov['throttle'])} throttle "
            f"for {_fmt_g(t_hover / G0 * 1000)}"
        )
    if full["thrust_n"] < t_req:
        out["problems"].append(
            f"gives only {_fmt_g(out['max_thrust_g'])} at full throttle; "
            f"{_fmt_g(t_req / G0 * 1000)} is needed for {ctx['tw_min']:g} thrust-to-weight"
        )
        out["i_req_a"] = full["current_a"]
    else:
        req = prp.operating_point(rotor.model_prop, rotor.model_motor, t_req, 0.0, v_full)
        out["i_req_a"] = req["current_a"]
        if req["current_a"] > spec["max_current_a"] * 1.0001:
            out["problems"].append(
                f"draws {req['current_a']:.1f} A for the required thrust, over its "
                f"{spec['max_current_a']:g} A rating"
            )
    # Thrust this pair can give within the motor's current rating (for the mass bound).
    cap = full["thrust_n"]
    if full["current_a"] > spec["max_current_a"]:
        lo, hi = 0.0, full["thrust_n"]
        for _ in range(30):
            mid = (lo + hi) / 2
            op = prp.operating_point(rotor.model_prop, rotor.model_motor, mid, 0.0, v_full)
            if op["current_a"] > spec["max_current_a"]:
                hi = mid
            else:
                lo = mid
        cap = lo
    out["t_cap_n"] = cap
    sizing = len(out["problems"])
    i_peak = min(full["current_a"], spec["max_current_a"])
    out["i_peak_a"] = i_peak
    out["throttle_limited"] = full["current_a"] > spec["max_current_a"]
    if not any(_esc_fits(e, cells, i_peak) for e in escs):
        out["problems"].append(
            f"no ESC in the catalogue is rated for {ESC_CURRENT_FACTOR:g} x its "
            f"{i_peak:.1f} A peak current on {cells}S"
        )
    max_rpm = rotor.prop["spec"].get("max_rpm")
    if max_rpm and full["rpm"] > max_rpm:
        out["problems"].append(
            f"would spin the propeller to {full['rpm']:,.0f} rpm, over its {max_rpm:,.0f} rpm limit"
        )
    if ctx["layout"] != "quad_pusher" and ctx.get("cruise_drag_n"):
        drag = ctx["cruise_drag_n"] * _drag_factor(ctx, mass_kg)
        try:
            cr = prp.operating_point(
                rotor.model_prop, rotor.model_motor, drag / 2, ctx["cruise_speed_mps"], v_hover
            )
        except (ValueError, ZeroDivisionError, OverflowError):
            cr = None
        out["cruise"] = cr
        if cr is None or cr["throttle"] > TILT_CRUISE_THROTTLE_MAX:
            out["problems"].append(
                "cannot hold cruise thrust "
                f"({drag / 2:.1f} N per tilted motor at {ctx['cruise_speed_mps']:g} m/s) below "
                f"{_pct(TILT_CRUISE_THROTTLE_MAX)} throttle"
                + (f" (needs {_pct(cr['throttle'])})" if cr else "")
            )
    # Problems other than not enough thrust within the rating at this mass.
    out["hard"] = len(out["problems"]) > sizing
    return out


def _drag_factor(ctx: dict[str, Any], mass_kg: float) -> float:
    fi = ctx["induced_fraction"]
    return (1 - fi) + fi * (mass_kg / ctx["mass_kg"]) ** 2


def _esc_fits(esc: dict[str, Any], cells: int, i_peak: float) -> bool:
    s = esc["spec"]
    return (
        s["lipo_cells_min"] <= cells <= s["lipo_cells_max"]
        and s["continuous_current_a"] >= ESC_CURRENT_FACTOR * i_peak
    )


# ---------------------------------------------------------------------------
# The selector
# ---------------------------------------------------------------------------


class Selector:
    def __init__(
        self,
        parameters: dict[str, Any],
        mission: dict[str, Any],
        settings: dict[str, Any],
        ctx: dict[str, Any],
        catalogue: list[dict[str, Any]],
        locked: dict[str, dict[str, Any]] | None = None,
        mass_solver: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.p = parameters
        self.mission = mission
        self.settings = settings
        self.ctx = ctx
        self.catalogue = catalogue
        self.by_id = {c["id"]: c for c in catalogue}
        self.locked = locked or {}
        # Parts held for the final consistency pass (not the owner's locks).
        self.pins: dict[str, dict[str, Any]] = {}
        self.mass_solver = mass_solver
        self.cells = int(parameters["battery"]["cells_series"])
        self.pack = _pack(parameters["battery"])
        self.mass_kg = float(ctx["mass_kg"])
        # The battery mass inside ``mass_kg`` (generic first, then the last chosen pack).
        self.battery_in_mass_g = float(ctx["generic_masses"].get("battery", 0.0))
        # Hover power of the chosen lift rotors relative to the generic ones (energy model).
        self.hover_factor = 1.0
        self.cruise_factor = 1.0
        self.pusher_eval: dict[str, Any] | None = None
        self.lift_capacity_n: float | None = None
        self.prev_battery: Choice | None = None

    # -- catalogue access --------------------------------------------------------------

    def cat(self, category: str) -> list[dict[str, Any]]:
        return [c for c in self.catalogue if c["category"] == category]

    def locked_part(self, role: str) -> dict[str, Any] | None:
        entry = self.pins.get(role) or self.locked.get(role)
        if not entry:
            return None
        part = self.by_id.get(entry.get("part_id"))
        if part is None or part["category"] not in ROLE_CATEGORIES[role]:
            return None
        return part

    def _held(self, role: str) -> str:
        return "Locked choice" if role in self.locked else "At the final take-off mass"

    def roles(self) -> list[str]:
        layout = self.p["layout"]
        out = []
        for role in ROLE_ORDER:
            if role in ("cruise_motor", "pusher_prop") and layout != "quad_pusher":
                continue
            if role == "tilt_servo" and layout == "quad_pusher":
                continue
            out.append(role)
        return out

    # -- main --------------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Choose, solve the mass with the choices, choose again at that mass, until the mass
        moves less than 1 %. Should the passes not settle, the pass that fills the most roles
        (the latest of those) is kept, so a list is never left without lift motors because of
        a last oscillation."""
        mass_kg = self.mass_kg
        history: list[tuple[dict[str, Choice], dict[str, Any] | None, float]] = []
        converged = False
        for _ in range(MASS_PASSES):
            choices = self._choose_all(mass_kg)
            if self.mass_solver is None:
                history.append((choices, None, mass_kg))
                converged = True
                break
            solved = self.mass_solver(self.analysis_parts(choices))
            new_kg = solved["total_max_g"] / 1000
            history.append((choices, solved, new_kg))
            if abs(new_kg - mass_kg) / mass_kg < 0.01:
                converged = True
                break
            mass_kg = new_kg
        last = history[-1]
        if any(c.part is None for c in last[0].values()):
            best = max(
                enumerate(history),
                key=lambda ih: (sum(c.part is not None for c in ih[1][0].values()), ih[0]),
            )[1]
            last = best
        choices, solved, mass_kg = last
        if not converged and self.mass_solver is not None:
            # Hold these parts and evaluate them once more at the mass they really give, so
            # every reasoning sentence and check quotes the final take-off mass.
            self.pins = {
                role: {"part_id": c.part["id"], "quantity": c.quantity}
                for role, c in choices.items()
                if c.part is not None and role in COUPLED_ROLES
            }
            self.prev_battery = choices.get("battery")
            try:
                choices = self._choose_all(mass_kg)
                solved = self.mass_solver(self.analysis_parts(choices))
                mass_kg = solved["total_max_g"] / 1000
            finally:
                self.pins = {}
        return {
            "choices": choices,
            "mass_kg": mass_kg,
            "selected_mass": solved,
            "converged": converged,
            "passes": len(history),
        }

    def _choose_all(self, mass_kg: float) -> dict[str, Choice]:
        out: dict[str, Choice] = {}
        roles = self.roles()
        self.lift_capacity_n = None
        if self.prev_battery is not None and self.prev_battery.metrics:
            # Evaluate the motors on the pack chosen in the previous pass.
            b = self.prev_battery.metrics
            self.pack = bat.pack_model(
                {
                    "chemistry": b["chemistry"],
                    "cells_series": self.cells,
                    "cells_parallel": b["cells_parallel"],
                    "capacity_mah": b["capacity_mah"],
                    "discharge_c_continuous": b["c_cont"],
                    "discharge_c_burst": b["c_burst"],
                }
            )
        lift_motor, lift_prop, lift_eval = self._lift(mass_kg)
        out["lift_motor"], out["lift_prop"] = lift_motor, lift_prop
        pusher_eval = None
        if "cruise_motor" in roles:
            out["cruise_motor"], out["pusher_prop"], pusher_eval = self._pusher(mass_kg)
            self.pusher_eval = pusher_eval
        out["esc"] = self._esc(lift_eval, pusher_eval)
        if "tilt_servo" in roles:
            out["tilt_servo"] = self._tilt_servo(lift_eval)
        for role in ("autopilot", "gps", "radio", "telemetry"):
            out[role] = self._avionics(role)
        out["spar_tube"] = self._spar(mass_kg)
        out["boom_tube"] = self._boom(mass_kg, lift_eval)
        # The battery last, at the mass this pass's other parts give (with the previous
        # pack), so the pack choice sees the motors, ESCs and tubes it will fly with.
        battery_mass_kg = mass_kg
        if self.mass_solver is not None:
            partial = dict(out)
            if self.prev_battery is not None:
                partial["battery"] = self.prev_battery
            solved = self.mass_solver(self.analysis_parts(partial))
            comps = {c["key"]: c["mass_g"] for c in solved["result"]["components"]}
            self.battery_in_mass_g = comps.get("battery", self.battery_in_mass_g)
            battery_mass_kg = solved["total_max_g"] / 1000
        out["battery"] = self._battery(battery_mass_kg, lift_eval)
        if out["battery"].part is not None:
            self.prev_battery = out["battery"]
        for role, choice in out.items():
            if choice.part is not None:
                _part_flags(choice, choice.part)
            choice.locked = (
                role in self.locked
                and choice.part is not None
                and (choice.part["id"] == self.locked[role].get("part_id"))
            )
        return {r: out[r] for r in roles if r in out}

    # -- lift ----------------------------------------------------------------------------

    def _lift(self, mass_kg: float) -> tuple[Choice, Choice, dict[str, Any] | None]:
        ctx = self.ctx
        motors = [
            m
            for m in self.cat("motor")
            if m["spec"]["lipo_cells_min"] <= self.cells <= m["spec"]["lipo_cells_max"]
        ]
        all_props = self.cat("propeller")
        props = _props_near(all_props, self.p["propulsion"]["prop_diameter_mm"])
        lm, lp = self.locked_part("lift_motor"), self.locked_part("lift_prop")
        # Every pair is evaluated (the alternatives list needs them); a held part is only
        # where the choice is made from.
        if lm is not None and lm not in motors:
            motors = [*motors, lm]
        if lp is not None and lp not in props:
            props = [*props, lp]
        escs = self.cat("esc")
        cands = []
        for m in motors:
            for pp in props:
                r = _rotor(m, pp)
                ev = _evaluate_lift(r, ctx, self.pack, mass_kg, escs, self.cells)
                cands.append((r, ev))
        motor_choice = Choice("lift_motor", None)
        prop_choice = Choice("lift_prop", None)
        rejected_motors = [
            m
            for m in self.cat("motor")
            if not m["spec"]["lipo_cells_min"] <= self.cells <= m["spec"]["lipo_cells_max"]
        ]
        feasible = [(r, ev) for r, ev in cands if not ev["problems"]]
        caps = [ev["t_cap_n"] for _r, ev in cands if "t_cap_n" in ev and not ev.get("hard")]
        self.lift_capacity_n = max(caps) if caps else None

        def score(item: tuple[_Rotor, dict[str, Any]]) -> tuple[float, float, float]:
            r, ev = item
            price = (unit_price(r.motor)[0] or 1e6) + (unit_price(r.prop)[0] or 1e6) / (
                2 if sold_as_pair(r.prop) else 1
            )
            mass = r.motor["mass_g"] + r.prop["mass_g"]
            steps = round(ev["g_per_w"] / EFFICIENCY_STEP_G_PER_W)
            return (-steps, mass, price)

        feasible.sort(key=score)
        if not cands:
            why = (
                f"No motor in the catalogue runs on {self.cells}S"
                if not motors
                else "No propeller in the catalogue"
            )
            motor_choice.unfilled_reason = prop_choice.unfilled_reason = why + "."
            return motor_choice, prop_choice, None
        pool = [
            c for c in cands if (lm is None or c[0].motor is lm) and (lp is None or c[0].prop is lp)
        ]
        pool_ok = [c for c in feasible if c in pool]
        if pool_ok:
            best_r, best_ev = pool_ok[0]
        else:
            # Nothing meets every constraint: show the strongest, flagged.
            best_r, best_ev = max(pool, key=lambda c: c[1].get("max_thrust_g", 0))
            if lm is None and lp is None:
                reason = (
                    "No motor and propeller in the catalogue meets every lift requirement at "
                    f"{mass_kg:.2f} kg: the strongest pair, {part_name(best_r.motor)} with "
                    f"{part_name(best_r.prop)}, " + "; ".join(best_ev["problems"]) + "."
                )
                motor_choice.unfilled_reason = prop_choice.unfilled_reason = reason
                motor_choice.alternatives = self._lift_alternatives(cands, None, "motor")
                prop_choice.alternatives = self._lift_alternatives(cands, None, "prop")
                return motor_choice, prop_choice, None
        ev = best_ev
        ev["motor_mass_g"] = best_r.motor["mass_g"]
        ev["prop_mass_g"] = best_r.prop["mass_g"]
        ev["prop_diameter_mm"] = best_r.prop["spec"]["diameter_mm"]
        hov, full = ev.get("hover"), ev.get("full")
        motor_choice.part, prop_choice.part = best_r.motor, best_r.prop
        motor_choice.quantity = 4
        motor_choice.line_mass_g = 4 * best_r.motor["mass_g"]
        prop_choice.quantity = 2 if sold_as_pair(best_r.prop) else 4
        prop_choice.line_mass_g = 4 * best_r.prop["mass_g"]
        for problem in ev["problems"]:
            motor_choice.flag("constraint", f"{self._held('lift_motor')}: it {problem}.")
        share = ctx["worst_share"]
        t_hover_g = ev["t_hover_n"] / G0 * 1000
        t_req_g = ev["t_req_n"] / G0 * 1000
        sent = [
            f"Hover at {mass_kg:.2f} kg needs {_fmt_g(t_hover_g)} from each motor of the busier "
            f"pair (it carries {_pct(share)} of the weight, plus 3 % rotor download); for "
            f"{ctx['tw_min']:g} thrust-to-weight each motor must give {_fmt_g(t_req_g)}.",
        ]
        if hov and full:
            sent.append(
                f"At that thrust the {best_r.motor['model']} with the {best_r.prop['model']} "
                f"draws {hov['current_a']:.1f} A at {_pct(hov['throttle'])} throttle "
                f"({ev['g_per_w']:.1f} g/W) and uses {_pct(ev['hover_fraction'])} of its "
                f"{_fmt_g(ev['max_thrust_g'])} full-throttle thrust, giving "
                f"{ev['max_thrust_g'] / t_hover_g:.1f} thrust-to-weight."
            )
            sent.append(
                f"Full-throttle current {full['current_a']:.1f} A against its "
                f"{best_r.motor['spec']['max_current_a']:g} A rating"
                + (
                    " (limit the maximum throttle in ArduPilot so it stays within the rating)."
                    if ev.get("throttle_limited")
                    else "."
                )
            )
        if ev.get("cruise"):
            cr = ev["cruise"]
            sent.append(
                f"Tilted forward, the front pair holds the {cr['thrust_required_n'] * 2:.1f} N "
                f"cruise drag at {_pct(cr['throttle'])} throttle "
                f"({cr['current_a']:.1f} A each)."
            )
        sent.append(
            "Propeller data: "
            + (
                "coefficients fitted to the manufacturer's thrust tests with this propeller."
                if best_r.fitted
                else "no thrust test with this propeller in the catalogue, so the generic "
                "propeller model was used with the motor's published constants."
            )
        )
        sent.append(
            f"{len(feasible)} of {len(cands)} motor and propeller pairs meet every requirement; "
            "they are ranked by hover efficiency (g/W), then mass, then price."
        )
        motor_choice.reasoning = sent
        prop_choice.reasoning = [
            f"Chosen with the motor: {best_r.prop['model']} "
            f"({best_r.prop['spec']['diameter_mm']:.0f} mm, pitch "
            f"{best_r.prop['spec']['pitch_mm']:.0f} mm) against the design's "
            f"{self.p['propulsion']['prop_diameter_mm']:.0f} mm.",
            *sent[1:2],
        ]
        if not best_r.fitted:
            motor_choice.flag(
                "no_thrust_data",
                "No manufacturer thrust test with this propeller; the motor model and the "
                "generic propeller estimate the performance.",
            )
        if abs(best_r.prop["spec"]["diameter_mm"] - self.p["propulsion"]["prop_diameter_mm"]) > 5:
            prop_choice.flag(
                "design_mismatch",
                f"The design uses {self.p['propulsion']['prop_diameter_mm']:.0f} mm propellers; "
                f"this one is {best_r.prop['spec']['diameter_mm']:.0f} mm. Update "
                "propulsion.prop_diameter_mm and check the clearances.",
            )
        if sold_as_pair(best_r.prop):
            prop_choice.flag("sold_in_pairs", "Sold as a CW/CCW pair: buy two pairs.")
        elif "apc" in best_r.prop["manufacturer"].lower():
            prop_choice.flag(
                "rotation",
                "A quad needs two clockwise and two counter-clockwise propellers: buy two "
                "standard and two 'EP' (reverse) versions.",
            )
        if best_r.prop["spec"]["hub_bore_mm"] < best_r.motor["spec"]["shaft_mm"]:
            prop_choice.flag(
                "fit",
                f"Propeller bore {best_r.prop['spec']['hub_bore_mm']:g} mm is smaller than the "
                f"{best_r.motor['spec']['shaft_mm']:g} mm shaft; check the mounting adapter.",
            )
        motor_choice.metrics = {
            "hover_current_a": hov["current_a"] if hov else None,
            "hover_throttle": hov["throttle"] if hov else None,
            "hover_g_per_w": ev.get("g_per_w"),
            "max_thrust_g": ev.get("max_thrust_g"),
            "thrust_to_weight": (ev.get("max_thrust_g") or 0) / t_hover_g if t_hover_g else None,
            "peak_current_a": ev.get("i_peak_a"),
            "thrust_data_fitted": best_r.fitted,
        }
        motor_choice.alternatives = self._lift_alternatives(cands, best_r, "motor")
        prop_choice.alternatives = self._lift_alternatives(cands, best_r, "prop")
        motor_choice.feasible = [
            {"part": r.motor, "prop": r.prop, "eval": e}
            for r, e in feasible
            if r.prop is best_r.prop
        ]
        prop_choice.feasible = [
            {"part": r.prop, "motor": r.motor, "eval": e}
            for r, e in feasible
            if r.motor is best_r.motor
        ]
        motor_choice.metrics["rejected_cells"] = [part_name(m) for m in rejected_motors]
        return motor_choice, prop_choice, ev

    def _lift_alternatives(
        self, cands: list[tuple[_Rotor, dict[str, Any]]], best: _Rotor | None, which: str
    ) -> list[dict[str, Any]]:
        """Runner-ups for the motor (with the chosen propeller) or the propeller (with the
        chosen motor); the best pairing per alternative part when nothing was chosen."""
        rows = []
        seen = set()
        best_ev = None
        if best is not None:
            best_ev = next(ev for r, ev in cands if r is best)
        ordered = sorted(cands, key=lambda c: (bool(c[1]["problems"]), -c[1].get("g_per_w", 0)))
        for r, ev in ordered:
            part = r.motor if which == "motor" else r.prop
            other = r.prop if which == "motor" else r.motor
            if best is not None:
                if part is (best.motor if which == "motor" else best.prop):
                    continue
                if other is not (best.prop if which == "motor" else best.motor):
                    continue
            if part["id"] in seen:
                continue
            seen.add(part["id"])
            if ev["problems"]:
                reason = "It " + "; ".join(ev["problems"]) + "."
            elif best_ev is not None:
                reason = _lost_reason(
                    ev.get("g_per_w", 0),
                    best_ev.get("g_per_w", 0),
                    part["mass_g"] - (best.motor if which == "motor" else best.prop)["mass_g"],
                    _price_delta(part, best.motor if which == "motor" else best.prop),
                )
            else:
                reason = "Not chosen."
            rows.append(
                {
                    "part": part,
                    "feasible": not ev["problems"],
                    "reason_lost": reason,
                    "deltas": {
                        "hover_g_per_w": ev.get("g_per_w"),
                        "max_thrust_g": ev.get("max_thrust_g"),
                        "mass_g": (
                            part["mass_g"]
                            - (best.motor if which == "motor" else best.prop)["mass_g"]
                        )
                        * 4
                        if best is not None
                        else None,
                        "price_eur": _price_delta(
                            part, best.motor if which == "motor" else best.prop, 4
                        )
                        if best is not None
                        else None,
                    },
                }
            )
        return rows

    # -- pusher --------------------------------------------------------------------------

    def _pusher(self, mass_kg: float) -> tuple[Choice, Choice, dict[str, Any] | None]:
        ctx = self.ctx
        mc, pc = Choice("cruise_motor", None), Choice("pusher_prop", None)
        motors = [
            m
            for m in self.cat("motor")
            if m["spec"]["lipo_cells_min"] <= self.cells <= m["spec"]["lipo_cells_max"]
        ]
        props = _props_near(self.cat("propeller"), self.p["pusher"]["prop_diameter_mm"])
        held_m, held_p = self.locked_part("cruise_motor"), self.locked_part("pusher_prop")
        if held_m is not None and held_m not in motors:
            motors = [*motors, held_m]
        if held_p is not None and held_p not in props:
            props = [*props, held_p]
        drag = (ctx["cruise_drag_n"] or 0) * _drag_factor(ctx, mass_kg)
        v = ctx["cruise_speed_mps"]
        vbus = _bus(self.pack, ctx["cruise_power_w"])
        cands = []
        for m in motors:
            for pp in props:
                r = _rotor(m, pp)
                ev: dict[str, Any] = {"problems": []}
                try:
                    op = prp.operating_point(r.model_prop, r.model_motor, drag, v, vbus)
                    full = prp.max_thrust(r.model_prop, r.model_motor, v, vbus)
                    static = prp.max_thrust(r.model_prop, r.model_motor, 0.0, vbus)
                except (ValueError, ZeroDivisionError, OverflowError):
                    ev["problems"].append("the motor model did not converge with this propeller")
                    cands.append((r, ev))
                    continue
                ev.update(op=op, full=full, static=static)
                ev["eta"] = op["eta_total"]
                ev["i_peak_a"] = min(static["current_a"], m["spec"]["max_current_a"])
                if op["throttle"] > PUSHER_THROTTLE_MAX:
                    ev["problems"].append(
                        f"needs {_pct(op['throttle'])} throttle for the {drag:.1f} N cruise drag "
                        f"(at most {_pct(PUSHER_THROTTLE_MAX)})"
                    )
                if full["thrust_n"] < PUSHER_THRUST_MARGIN * drag:
                    ev["problems"].append(
                        f"gives only {full['thrust_n']:.1f} N at full throttle at cruise speed, "
                        f"under {PUSHER_THRUST_MARGIN:g} x the drag"
                    )
                if op["current_a"] > m["spec"]["max_current_a"]:
                    ev["problems"].append("exceeds its current rating in cruise")
                cands.append((r, ev))
        feasible = [
            (r, ev)
            for r, ev in cands
            if not ev["problems"]
            and (held_m is None or r.motor is held_m)
            and (held_p is None or r.prop is held_p)
        ]
        feasible.sort(
            key=lambda c: (
                -round(c[1]["eta"], 2),
                c[0].motor["mass_g"] + c[0].prop["mass_g"],
                (unit_price(c[0].motor)[0] or 1e6),
            )
        )
        if not feasible:
            reason = "No motor and propeller in the catalogue can push at cruise speed."
            if cands:
                r, ev = cands[0]
                reason += f" For example {part_name(r.motor)}: " + "; ".join(ev["problems"]) + "."
            mc.unfilled_reason = pc.unfilled_reason = reason
            return mc, pc, None
        r, ev = feasible[0]
        op = ev["op"]
        mc.part, pc.part = r.motor, r.prop
        mc.quantity, pc.quantity = 1, 1
        mc.line_mass_g, pc.line_mass_g = r.motor["mass_g"], r.prop["mass_g"]
        mc.reasoning = [
            f"Cruise at {v:g} m/s needs {drag:.1f} N of thrust. The {r.motor['model']} with the "
            f"{r.prop['model']} gives it at {_pct(op['throttle'])} throttle, drawing "
            f"{op['current_a']:.1f} A ({op['battery_power_w']:.0f} W from the battery, "
            f"{_pct(op['eta_total'])} overall efficiency), inside the efficient "
            f"{_pct(PUSHER_THROTTLE_MAX)}-or-less throttle band.",
            f"At full throttle it gives {ev['full']['thrust_n']:.1f} N at cruise speed "
            f"({ev['full']['thrust_n'] / drag:.1f} x the drag) for acceleration.",
            f"{len(feasible)} of {len(cands)} pairs qualify; ranked by cruise efficiency, then "
            "mass, then price.",
        ]
        pc.reasoning = [
            f"Chosen with the pusher motor: {r.prop['model']} "
            f"({r.prop['spec']['diameter_mm']:.0f} mm) against the design's "
            f"{self.p['pusher']['prop_diameter_mm']:.0f} mm."
        ]
        if abs(r.prop["spec"]["diameter_mm"] - self.p["pusher"]["prop_diameter_mm"]) > 5:
            pc.flag(
                "design_mismatch",
                f"The design's pusher propeller is {self.p['pusher']['prop_diameter_mm']:.0f} mm; "
                f"this one is {r.prop['spec']['diameter_mm']:.0f} mm. Update "
                "pusher.prop_diameter_mm and check the clearance.",
            )
        if not r.fitted:
            mc.flag(
                "no_thrust_data",
                "No manufacturer thrust test with this propeller; estimated with the motor "
                "model and the generic propeller.",
            )
        mc.metrics = {
            "cruise_throttle": op["throttle"],
            "cruise_current_a": op["current_a"],
            "peak_current_a": ev["i_peak_a"],
            "efficiency": op["eta_total"],
        }
        for rr, e in cands:
            if rr.motor is r.motor or rr.prop is not r.prop:
                continue
            mc.alternatives.append(
                {
                    "part": rr.motor,
                    "feasible": not e["problems"],
                    "reason_lost": (
                        "It " + "; ".join(e["problems"]) + "."
                        if e["problems"]
                        else f"Lower cruise efficiency ({_pct(e['eta'])} vs "
                        f"{_pct(ev['eta'])}) or heavier."
                    ),
                    "deltas": {
                        "mass_g": rr.motor["mass_g"] - r.motor["mass_g"],
                        "price_eur": _price_delta(rr.motor, r.motor),
                    },
                }
            )
        for rr, e in cands:
            if rr.prop is r.prop or rr.motor is not r.motor:
                continue
            pc.alternatives.append(
                {
                    "part": rr.prop,
                    "feasible": not e["problems"],
                    "reason_lost": "It " + "; ".join(e["problems"]) + "."
                    if e["problems"]
                    else "Lower cruise efficiency with this motor.",
                    "deltas": {
                        "mass_g": rr.prop["mass_g"] - r.prop["mass_g"],
                        "price_eur": _price_delta(rr.prop, r.prop),
                    },
                }
            )
        return mc, pc, ev

    # -- ESC -----------------------------------------------------------------------------

    def _esc(self, lift: dict[str, Any] | None, pusher: dict[str, Any] | None) -> Choice:
        c = Choice("esc", None)
        if lift is None or "i_peak_a" not in lift:
            c.unfilled_reason = "No lift motor was chosen, so the ESC current is unknown."
            return c
        i_peak = lift["i_peak_a"]
        n = 4
        if pusher is not None:
            i_peak = max(i_peak, pusher["i_peak_a"])
            n = 5
        need = ESC_CURRENT_FACTOR * i_peak
        escs = self.cat("esc")
        locked = self.locked_part("esc")
        pool = escs
        rows = []
        for e in pool:
            problems = []
            s = e["spec"]
            if not s["lipo_cells_min"] <= self.cells <= s["lipo_cells_max"]:
                problems.append(
                    f"is rated for {s['lipo_cells_min']}-{s['lipo_cells_max']}S, not {self.cells}S"
                )
            if s["continuous_current_a"] < need:
                problems.append(
                    f"is rated {s['continuous_current_a']:g} A continuous, under the "
                    f"{need:.1f} A needed"
                )
            rows.append((e, problems))
        feasible = [e for e, pr in rows if not pr and (locked is None or e is locked)]
        feasible.sort(
            key=lambda e: (0 if e["spec"]["telemetry"] else 1, e["mass_g"], unit_price(e)[0] or 1e6)
        )
        if not feasible and locked is None:
            c.unfilled_reason = (
                f"No ESC in the catalogue is rated for {need:.1f} A continuous on {self.cells}S "
                f"({ESC_CURRENT_FACTOR:g} x the {i_peak:.1f} A peak motor current)."
            )
            c.alternatives = [
                {
                    "part": e,
                    "feasible": False,
                    "reason_lost": "It " + "; ".join(pr) + ".",
                    "deltas": {},
                }
                for e, pr in rows
            ]
            return c
        best = feasible[0] if feasible else locked
        assert best is not None
        c.part, c.quantity, c.line_mass_g = best, n, n * best["mass_g"]
        for e, pr in rows:
            if pr and e is best:
                c.flag("constraint", self._held(c.role) + ": it " + "; ".join(pr) + ".")
        c.reasoning = [
            f"The peak motor current is {i_peak:.1f} A, so each ESC needs at least "
            f"{need:.1f} A continuous ({ESC_CURRENT_FACTOR:g} x) on {self.cells}S.",
            f"The {best['model']} is rated {best['spec']['continuous_current_a']:g} A continuous "
            f"({best['spec']['continuous_current_a'] / i_peak:.1f} x the peak) for "
            f"{best['spec']['lipo_cells_min']}-{best['spec']['lipo_cells_max']}S"
            + (
                " and reports telemetry (rpm, current, temperature) to ArduPilot."
                if best["spec"]["telemetry"]
                else "; it has no telemetry output."
            ),
            "Telemetry ESCs are preferred, then the lightest and cheapest; "
            f"{n} are needed" + (" (four lift motors and the pusher)." if n == 5 else "."),
        ]
        c.metrics = {"peak_current_a": i_peak, "required_a": need}
        for e, pr in rows:
            if e is best:
                continue
            c.alternatives.append(
                {
                    "part": e,
                    "feasible": not pr,
                    "reason_lost": "It " + "; ".join(pr) + "."
                    if pr
                    else (
                        "No telemetry."
                        if best["spec"]["telemetry"] and not e["spec"]["telemetry"]
                        else "Heavier or more expensive."
                    ),
                    "deltas": {
                        "mass_g": (e["mass_g"] - best["mass_g"]) * n,
                        "price_eur": _price_delta(e, best, n),
                        "current_margin": e["spec"]["continuous_current_a"] / i_peak,
                    },
                }
            )
        c.feasible = [{"part": e} for e in feasible]
        return c

    # -- tilt servo ------------------------------------------------------------------------

    def _tilt_servo(self, lift: dict[str, Any] | None) -> Choice:
        c = Choice("tilt_servo", None)
        if lift is None or "full" not in lift:
            c.unfilled_reason = "No lift motor was chosen, so the hinge load is unknown."
            return c
        p = self.p
        front = p["layout"] == "front_tilt"
        motor_x = p["motors"]["front_x_mm"] if front else p["motors"]["rear_x_mm"]
        dx = abs(motor_x - p["tilt"]["axis_x_mm"]) / 1000
        dz = abs(p["motors"]["height_mm"]) / 1000
        offset = max(dx, dz)
        thrust = lift["full"]["thrust_n"]
        m_thrust = thrust * offset
        motor_mass = lift.get("motor_mass_g") or 150.0
        prop_mass = lift.get("prop_mass_g") or 30.0
        prop_d = (lift.get("prop_diameter_mm") or p["propulsion"]["prop_diameter_mm"]) / 1000
        i_rot = prop_mass / 1000 * prop_d**2 / 12 + 0.5 * (0.4 * motor_mass / 1000) * 0.025**2
        omega = lift["full"]["rpm"] * 2 * math.pi / 60
        m_gyro = i_rot * omega * GYRO_BODY_RATE_RAD_S
        hinge = (m_thrust + m_gyro) * (1 + SERVO_INERTIA_ALLOWANCE)
        hinge_kgcm = hinge / G0 * 100
        need = SERVO_TORQUE_FACTOR * hinge_kgcm
        servos = self.cat("servo")
        locked = self.locked_part("tilt_servo")
        pool = servos
        rows = []
        for s in pool:
            pr = []
            if s["spec"]["torque_kg_cm"] < need:
                pr.append(f"gives {s['spec']['torque_kg_cm']:g} kg·cm, under the {need:.1f} needed")
            if s["spec"]["speed_s_per_60deg"] > SERVO_SPEED_MAX_S:
                pr.append(
                    f"takes {s['spec']['speed_s_per_60deg']:g} s per 60°, slower than "
                    f"{SERVO_SPEED_MAX_S:g} s"
                )
            rows.append((s, pr))
        feasible = sorted(
            [s for s, pr in rows if not pr and (locked is None or s is locked)],
            key=lambda s: (s["mass_g"], unit_price(s)[0] or 1e6),
        )
        if not feasible and locked is None:
            c.unfilled_reason = (
                f"No servo in the catalogue gives {need:.1f} kg·cm at "
                f"{SERVO_SPEED_MAX_S:g} s/60° or faster."
            )
            return c
        best = feasible[0] if feasible else locked
        assert best is not None
        c.part, c.quantity, c.line_mass_g = best, 2, 2 * best["mass_g"]
        c.reasoning = [
            f"The hinge moment is {hinge_kgcm:.1f} kg·cm: full thrust {thrust:.1f} N at "
            f"{offset * 1000:.0f} mm from the hinge ({m_thrust:.2f} N·m), plus "
            f"{m_gyro:.2f} N·m gyroscopic moment of the rotor at {lift['full']['rpm']:,.0f} rpm "
            f"during a 100°/s body rate, plus {_pct(SERVO_INERTIA_ALLOWANCE)} for inertia and "
            "linkage friction.",
            f"With a {SERVO_TORQUE_FACTOR:g} x margin each servo needs {need:.1f} kg·cm and "
            f"{SERVO_SPEED_MAX_S:g} s/60° or faster.",
            f"The {best['model']} gives {best['spec']['torque_kg_cm']:g} kg·cm "
            f"({best['spec']['torque_kg_cm'] / hinge_kgcm:.1f} x the hinge moment) at "
            f"{best['spec']['speed_s_per_60deg']:g} s/60° and weighs {best['mass_g']:g} g; the "
            "lightest that qualifies.",
        ]
        if best["spec"]["voltage_min_v"] > 5.5:
            c.flag(
                "power",
                f"Needs a {best['spec']['voltage_min_v']:g}-{best['spec']['voltage_max_v']:g} V "
                "supply: a separate servo BEC (in the consumables line).",
            )
        for s, pr in rows:
            if pr and s is best:
                c.flag("constraint", self._held(c.role) + ": it " + "; ".join(pr) + ".")
        c.metrics = {
            "hinge_moment_kg_cm": hinge_kgcm,
            "required_torque_kg_cm": need,
            "torque_margin": best["spec"]["torque_kg_cm"] / hinge_kgcm if hinge_kgcm else None,
        }
        for s, pr in rows:
            if s is best:
                continue
            c.alternatives.append(
                {
                    "part": s,
                    "feasible": not pr,
                    "reason_lost": "It " + "; ".join(pr) + "." if pr else "Heavier.",
                    "deltas": {
                        "mass_g": 2 * (s["mass_g"] - best["mass_g"]),
                        "price_eur": _price_delta(s, best, 2),
                        "torque_margin": s["spec"]["torque_kg_cm"] / hinge_kgcm,
                    },
                }
            )
        c.feasible = [{"part": s} for s in feasible]
        return c

    # -- battery -----------------------------------------------------------------------------

    def battery_options(self, mass_kg: float, lift: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Every pack and custom cell pack with its estimated mass, energy and endurance."""
        ctx = self.ctx
        base_g = mass_kg * 1000 - self.battery_in_mass_g
        options = []
        for b in self.cat("battery"):
            s = b["spec"]
            if s["cells_series"] != self.cells:
                continue
            cap_ah = s["capacity_mah"] / 1000 * s["cells_parallel"]
            options.append(
                {
                    "part": b,
                    "kind": "pack",
                    "quantity": 1,
                    "mass_g": b["mass_g"],
                    "energy_wh": s["nominal_voltage_v"] * cap_ah
                    if s["nominal_voltage_v"] > 10
                    else s["cells_series"] * s["nominal_voltage_v"] * cap_ah,
                    "i_cont_a": s["discharge_c_continuous"] * cap_ah,
                    "chemistry": s["chemistry"],
                    "cells_parallel": s["cells_parallel"],
                    "capacity_mah": s["capacity_mah"],
                    "c_cont": s["discharge_c_continuous"],
                    "c_burst": s["discharge_c_burst"],
                    "label": part_name(b),
                }
            )
        for cell in self.cat("cell"):
            s = cell["spec"]
            for par in range(1, CELL_PACK_MAX_PARALLEL + 1):
                n = self.cells * par
                cap_ah = s["capacity_mah"] / 1000 * par
                i_cont = s["max_continuous_discharge_a"] * par
                options.append(
                    {
                        "part": cell,
                        "kind": "custom",
                        "quantity": n,
                        "mass_g": n * cell["mass_g"] * (1 + CELL_PACK_OVERHEAD),
                        "energy_wh": self.cells * s["nominal_voltage_v"] * cap_ah,
                        "i_cont_a": i_cont,
                        "chemistry": "li-ion" if "ion" in s["chemistry"].lower() else "lipo",
                        "cells_parallel": par,
                        "capacity_mah": s["capacity_mah"],
                        "c_cont": i_cont / cap_ah,
                        "c_burst": i_cont / cap_ah,
                        "label": f"custom {self.cells}S{par}P pack of {part_name(cell)}",
                    }
                )
        hover_factor = 1.0
        if lift and lift.get("hover") and ctx.get("hover_power_w"):
            hover_factor = self._hover_power(lift) / (
                ctx["hover_power_w"] * (mass_kg / ctx["mass_kg"]) ** 1.5
            )
        self.hover_factor = hover_factor
        # Cruise power with the chosen cruise rotors relative to the generic ones.
        self.cruise_factor = 1.0
        if self.p["layout"] == "quad_pusher":
            cr_op, n_rot = (self.pusher_eval or {}).get("op"), 1
        else:
            cr_op, n_rot = (lift or {}).get("cruise"), 2
        if cr_op and ctx.get("cruise_power_w"):
            avionics = AVIONICS_POWER_W[ctx["scale"]]
            generic_w = ctx["cruise_power_w"] * _drag_factor(ctx, mass_kg)
            self.cruise_factor = (n_rot * cr_op["battery_power_w"] + avionics) / generic_w
        # Heaviest take-off mass the chosen lift motors still lift with the thrust-to-weight
        # minimum (and never above the design mass limit).
        # The strongest motor and propeller pair in the catalogue (within its current rating)
        # bounds the take-off mass, with a 2 % allowance for the other parts' mass changes, so
        # the next mass pass can always find lift motors.
        max_kg = float(self.settings["limits"]["design_mtow_kg"])
        if self.lift_capacity_n:
            max_kg = min(
                max_kg,
                0.98
                * 2
                * self.lift_capacity_n
                / (ctx["tw_min"] * (1 + HOVER_DOWNLOAD_FRACTION) * ctx["worst_share"] * G0),
            )
        self.battery_max_takeoff_kg = max_kg
        # The heaviest take-off mass the lift motors chosen in this pass carry (alternatives
        # and upgrades above it would also need stronger motors).
        motor_kg = max_kg
        if lift and lift.get("t_cap_n"):
            motor_kg = min(
                max_kg,
                2
                * lift["t_cap_n"]
                / (ctx["tw_min"] * (1 + HOVER_DOWNLOAD_FRACTION) * ctx["worst_share"] * G0),
            )
        self.motor_max_takeoff_kg = motor_kg
        for o in options:
            m = (base_g + o["mass_g"]) / 1000
            o["takeoff_kg"] = m
            o["endurance_min"] = self.endurance_min(m, o["energy_wh"], hover_factor)
            peak = (ctx["peak_current_a"] or 0) * hover_factor * (m / ctx["mass_kg"]) ** 1.5
            o["peak_a"] = peak
            o["i_needed_a"] = peak / ctx["current_fraction"]
            o["current_ok"] = o["i_cont_a"] >= o["i_needed_a"]
            o["thrust_ok"] = m <= max_kg
            o["usable"] = o["current_ok"] and o["thrust_ok"]
            o["fits_motors"] = m <= motor_kg
            o["meets_endurance"] = o["endurance_min"] >= ctx["endurance_target_min"]
            price, _src = unit_price(o["part"])
            o["price_eur"] = price * o["quantity"] if price is not None else None
        return options

    def _hover_power(self, lift: dict[str, Any]) -> float:
        """Battery power to hover with the evaluated rotor (busier pair figures x 4, a slight
        overestimate) plus the avionics share in the generic analysis."""
        hov = lift["hover"]
        return 4 * hov["battery_power_w"]

    def endurance_min(
        self,
        mass_kg: float,
        energy_wh: float,
        hover_factor: float = 1.0,
        cruise_factor: float | None = None,
    ) -> float:
        ctx = self.ctx
        ratio = mass_kg / ctx["mass_kg"]
        usable = energy_wh * (1 - ctx["reserve"]) * bat.USABLE_ENERGY_FACTOR
        vtol = ctx["vtol_wh"] * ratio**1.5 * hover_factor
        if cruise_factor is None:
            cruise_factor = self.cruise_factor
        cruise_w = ctx["cruise_power_w"] * _drag_factor(ctx, mass_kg) * cruise_factor
        if cruise_w <= 0:
            return 0.0
        return max(0.0, (usable - vtol) / cruise_w * 60)

    def _battery(self, mass_kg: float, lift: dict[str, Any] | None) -> Choice:
        ctx = self.ctx
        c = Choice("battery", None)
        options = self.battery_options(mass_kg, lift)
        locked = self.locked_part("battery")
        if locked is not None:
            mine = [o for o in options if o["part"]["id"] == locked["id"]]
            want_q = (self.pins.get("battery") or self.locked.get("battery") or {}).get("quantity")
            if want_q and locked["category"] == "cell":
                mine = [o for o in mine if o["quantity"] == want_q] or mine
            if not mine:
                c.unfilled_reason = (
                    f"The locked battery {part_name(locked)} does not match the design's "
                    f"{self.cells}S pack."
                )
                return c
            chosen = mine[0]
        else:
            ok = [o for o in options if o["usable"]]
            meet = [o for o in ok if o["meets_endurance"]]
            current_only = [o for o in options if o["current_ok"]]
            if meet:
                chosen = min(meet, key=lambda o: (o["mass_g"], o["price_eur"] or 1e6))
            elif ok:
                chosen = max(ok, key=lambda o: o["endurance_min"])
            elif current_only:
                # Even the lightest pack makes the aircraft too heavy for the lift motors;
                # take the lightest and let the next mass pass pick bigger motors.
                chosen = min(current_only, key=lambda o: o["mass_g"])
            else:
                c.unfilled_reason = (
                    f"No {self.cells}S pack or cell pack in the catalogue can supply the "
                    f"{(ctx['peak_current_a'] or 0):.0f} A peak current within "
                    f"{_pct(ctx['current_fraction'])} of its continuous rating."
                )
                return c
        part = chosen["part"]
        c.part, c.quantity, c.line_mass_g = part, chosen["quantity"], chosen["mass_g"]
        if chosen["kind"] == "custom":
            c.custom_pack = {
                "cells_series": self.cells,
                "cells_parallel": chosen["cells_parallel"],
                "cell": part_name(part),
                "overhead_fraction": CELL_PACK_OVERHEAD,
            }
        target = ctx["endurance_target_min"]
        sent = [
            f"The mission asks for {target:g} min of wing flight. The {chosen['label']} stores "
            f"{chosen['energy_wh']:.0f} Wh "
            f"({chosen['energy_wh'] * (1 - ctx['reserve']) * 0.95:.0f} "
            f"Wh usable with the {_pct(ctx['reserve'])} reserve) and weighs "
            f"{_fmt_g(chosen['mass_g'])}; with it the aircraft weighs about "
            f"{chosen['takeoff_kg']:.2f} kg and the estimated wing-flight endurance is "
            f"{chosen['endurance_min']:.0f} min.",
            f"Its continuous rating {chosen['i_cont_a']:.0f} A covers the "
            f"{chosen['peak_a']:.0f} A peak with the {_pct(ctx['current_fraction'])} rule "
            f"(needs {chosen['i_needed_a']:.0f} A).",
        ]
        if not chosen["meets_endurance"] and locked is None:
            sent.append(
                f"No pack in the catalogue reaches {target:g} min at this design without "
                "exceeding its current rating or making the aircraft too heavy for the lift "
                f"motors (at most {self.battery_max_takeoff_kg:.2f} kg); this is the "
                "longest-flying one that does neither."
            )
            c.flag(
                "endurance",
                f"Estimated {chosen['endurance_min']:.0f} min, short of the {target:g} min target.",
            )
        elif locked is None:
            sent.append("It is the lightest option that meets the endurance target.")
        packs = [o for o in options if o["kind"] == "pack" and o["usable"]]
        customs = [o for o in options if o["kind"] == "custom" and o["usable"]]
        best_pack = max(packs, key=lambda o: o["endurance_min"]) if packs else None
        best_custom = max(customs, key=lambda o: o["endurance_min"]) if customs else None
        if best_pack and best_custom:
            sent.append(
                f"Pack vs custom Li-ion: the best ready-made pack ({best_pack['label']}, "
                f"{_fmt_g(best_pack['mass_g'])}) gives about {best_pack['endurance_min']:.0f} min "
                f"for {_fmt_eur(best_pack['price_eur'])}; the best custom pack "
                f"({best_custom['label']}, {_fmt_g(best_custom['mass_g'])}) about "
                f"{best_custom['endurance_min']:.0f} min for {_fmt_eur(best_custom['price_eur'])} "
                "in cells, but it must be built (spot-welded strip, balance leads, wrap) and "
                "Li-ion sags more under hover current."
            )
        c.reasoning = sent
        design_b = self.p["battery"]
        if (
            chosen["chemistry"] != design_b["chemistry"]
            or chosen["cells_parallel"] != design_b["cells_parallel"]
            or abs(chosen["capacity_mah"] - design_b["capacity_mah"]) > 1
        ):
            c.flag(
                "design_mismatch",
                f"The design's battery is {design_b['cells_series']}S{design_b['cells_parallel']}P "
                f"{design_b['chemistry']} {design_b['capacity_mah']:g} mAh; this choice is "
                f"{self.cells}S{chosen['cells_parallel']}P {chosen['chemistry']} "
                f"{chosen['capacity_mah']:g} mAh per cell group. The analysis with parts uses the "
                "selected battery; update the battery inputs to match.",
            )
        if chosen["kind"] == "custom":
            c.flag(
                "build",
                f"Custom pack: {chosen['quantity']} cells to assemble "
                f"({self.cells}S{chosen['cells_parallel']}P); needs a spot welder or a pack "
                "builder.",
            )
        c.metrics = {
            "energy_wh": chosen["energy_wh"],
            "endurance_min": chosen["endurance_min"],
            "continuous_a": chosen["i_cont_a"],
            "peak_a": chosen["peak_a"],
            "takeoff_kg": chosen["takeoff_kg"],
            "kind": chosen["kind"],
            "cells_parallel": chosen["cells_parallel"],
            "capacity_mah": chosen["capacity_mah"],
            "chemistry": chosen["chemistry"],
            "c_cont": chosen["c_cont"],
            "c_burst": chosen["c_burst"],
        }
        seen = set()
        for o in sorted(options, key=lambda o: -o["endurance_min"]):
            key = (o["part"]["id"], o["quantity"])
            if o is chosen or key in seen:
                continue
            if o["kind"] == "custom" and not o["usable"]:
                continue
            seen.add(key)
            if not o["thrust_ok"]:
                reason = (
                    f"Too heavy: the aircraft would weigh {o['takeoff_kg']:.2f} kg, more than the "
                    f"lift motors carry with the thrust-to-weight minimum "
                    f"({self.battery_max_takeoff_kg:.2f} kg)."
                )
            elif not o.get("fits_motors", True):
                reason = (
                    f"Heavier: the aircraft would weigh {o['takeoff_kg']:.2f} kg, more than the "
                    f"chosen lift motors carry ({self.motor_max_takeoff_kg:.2f} kg); it would "
                    f"also need stronger motors (about {o['endurance_min']:.0f} min)."
                )
            elif not o["current_ok"]:
                reason = (
                    f"Its {o['i_cont_a']:.0f} A continuous rating is under the "
                    f"{o['i_needed_a']:.0f} A needed."
                )
            elif o["endurance_min"] < chosen["endurance_min"]:
                reason = (
                    f"Shorter endurance ({o['endurance_min']:.0f} vs "
                    f"{chosen['endurance_min']:.0f} min)."
                )
            else:
                reason = (
                    f"Heavier ({_fmt_g(o['mass_g'])} vs {_fmt_g(chosen['mass_g'])}) for "
                    f"{o['endurance_min'] - chosen['endurance_min']:+.0f} min."
                )
            c.alternatives.append(
                {
                    "part": o["part"],
                    "quantity": o["quantity"],
                    "label": o["label"],
                    "feasible": o["usable"],
                    "reason_lost": reason,
                    "deltas": {
                        "mass_g": o["mass_g"] - chosen["mass_g"],
                        "price_eur": (o["price_eur"] - chosen["price_eur"])
                        if o["price_eur"] is not None and chosen["price_eur"] is not None
                        else None,
                        "endurance_min": o["endurance_min"] - chosen["endurance_min"],
                    },
                }
            )
        c.alternatives = c.alternatives[:8]
        c.feasible = [o for o in options if o["usable"]]
        c.metrics["chosen_option"] = {k: v for k, v in chosen.items() if k != "part"}
        return c

    # -- avionics ----------------------------------------------------------------------------

    def _avionics(self, role: str) -> Choice:
        c = Choice(role, None)
        parts = self.cat(role)
        locked = self.locked_part(role)
        rows = []
        outputs_needed = (
            4 + CONTROL_SURFACE_SERVOS + (1 if self.p["layout"] == "quad_pusher" else 2)
        )
        for part in parts:
            s = part["spec"]
            problems: list[str] = []
            notes: list[str] = []
            if role == "autopilot":
                if s["imu_count"] < 2:
                    problems.append("has a single IMU (two or more give redundancy)")
                if s["pwm_outputs"] < outputs_needed:
                    problems.append(f"has {s['pwm_outputs']} outputs, {outputs_needed} are needed")
            elif role == "gps":
                if s["update_rate_hz"] < GPS_RATE_MIN_HZ:
                    problems.append(
                        f"updates at {s['update_rate_hz']:g} Hz, under the {GPS_RATE_MIN_HZ:g} Hz "
                        f"wanted at {self.ctx['cruise_speed_mps']:g} m/s"
                    )
            elif role in ("radio", "telemetry"):
                legal, note = radio_legality(s["frequency_mhz"])
                if legal == "illegal":
                    problems.append(note)
                elif legal == "restricted":
                    notes.append(note)
                if role == "telemetry" and s["air_rate_kbps"] < TELEMETRY_RATE_MIN_KBPS:
                    problems.append(
                        f"carries {s['air_rate_kbps']:g} kbps, under the "
                        f"{TELEMETRY_RATE_MIN_KBPS:g} kbps a full MAVLink stream wants"
                    )
                if role == "radio" and not s.get("telemetry"):
                    notes.append("no telemetry back to the transmitter")
                if legal == "restricted":
                    problems.append("is legal only at reduced power (" + note + ")")
            rows.append((part, problems, notes))
        price = {p_["id"]: unit_price(p_)[0] for p_ in parts}
        feasible = [r for r in rows if not r[1]]
        feasible.sort(key=lambda r: price[r[0]["id"]] if price[r[0]["id"]] is not None else 1e6)
        if locked is not None:
            best_row = next((r for r in rows if r[0]["id"] == locked["id"]), None)
        else:
            best_row = feasible[0] if feasible else None
        if best_row is None:
            c.unfilled_reason = f"No {ROLE_LABELS[role].lower()} in the catalogue meets the rules."
            c.alternatives = [
                {
                    "part": r[0],
                    "feasible": False,
                    "reason_lost": "It " + "; ".join(r[1]) + ".",
                    "deltas": {},
                }
                for r in rows
            ]
            return c
        best = best_row[0]
        c.part, c.quantity, c.line_mass_g = best, 1, best["mass_g"]
        s = best["spec"]
        if role == "autopilot":
            c.reasoning = [
                f"ArduPilot board with {s['imu_count']} IMUs and {s['pwm_outputs']} outputs "
                f"({outputs_needed} needed: four lift motors, "
                + ("the pusher" if self.p["layout"] == "quad_pusher" else "two tilt servos")
                + f", {CONTROL_SURFACE_SERVOS} control surfaces); {s['can_ports']} CAN ports.",
                "The cheapest board that meets this; premium boards add a third IMU, vibration "
                "isolation or an ADS-B carrier.",
            ]
        elif role == "gps":
            c.reasoning = [
                f"{', '.join(s['constellations'])} at {s['update_rate_hz']:g} Hz over "
                f"{s['interface']}" + (" with RTK." if s["rtk"] else "."),
                f"The cheapest receiver with a {GPS_RATE_MIN_HZ:g} Hz update (at "
                f"{self.ctx['cruise_speed_mps']:g} m/s the aircraft moves "
                f"{self.ctx['cruise_speed_mps'] / GPS_RATE_MIN_HZ:.1f} m between fixes).",
            ]
        elif role == "radio":
            _l, note = radio_legality(s["frequency_mhz"])
            c.reasoning = [
                f"Control link at {s['frequency_mhz']:g} MHz: {note}",
                f"{s['channels']} channels"
                + (", with telemetry back to the transmitter." if s["telemetry"] else "."),
                "The cheapest legal receiver; it needs a matching transmitter or module "
                "(not in this list). Range figures are line-of-sight claims; Open category A3 "
                "flying stays within visual line of sight.",
            ]
        else:
            _l, note = radio_legality(s["frequency_mhz"])
            c.reasoning = [
                f"Ground-station link at {s['frequency_mhz']:g} MHz: {note}",
                f"{s['air_rate_kbps']:g} kbps over {s['interface']}; the cheapest legal radio "
                f"with at least {TELEMETRY_RATE_MIN_KBPS:g} kbps.",
            ]
        for note in best_row[2]:
            c.flag("radio", note[0].upper() + note[1:] + ".")
        for problem in best_row[1]:
            c.flag("constraint", self._held(c.role) + ": it " + problem + ".")
        bp = price[best["id"]]
        for part, problems, _notes in rows:
            if part is best:
                continue
            pp = price[part["id"]]
            tier = (
                "cheaper"
                if pp is not None and bp is not None and pp < bp
                else "premium"
                if pp is not None and bp is not None and pp > bp
                else "alternative"
            )
            c.alternatives.append(
                {
                    "part": part,
                    "feasible": not problems,
                    "tier": tier,
                    "reason_lost": (
                        "It " + "; ".join(problems) + "."
                        if problems
                        else ("More expensive." if tier == "premium" else "Not the default.")
                    ),
                    "deltas": {
                        "mass_g": part["mass_g"] - best["mass_g"],
                        "price_eur": (pp - bp) if pp is not None and bp is not None else None,
                    },
                }
            )
        c.feasible = [{"part": r[0]} for r in feasible]
        return c

    # -- tubes -------------------------------------------------------------------------------

    def _tube_rows(
        self,
        moment_nm: float,
        max_od: float | None,
        length_needed_mm: float,
        tip_load: tuple[float, float] | None = None,
    ) -> list[dict[str, Any]]:
        """``tip_load`` = (force N, arm m) for the cantilever stiffness rule (booms)."""
        rows = []
        for t in self.cat("carbon_tube"):
            s = t["spec"]
            od, idd = s["outer_diameter_mm"], s["inner_diameter_mm"]
            sec = tube_section(od, (od - idd) / 2)
            stress = moment_nm * sec["c_m"] / sec["I_m4"] if sec["I_m4"] > 0 else math.inf
            allow = TUBE_ALLOWABLE_PA
            source = "500 MPa engine allowable (no published strength)"
            if s.get("tensile_strength_mpa"):
                allow = min(allow, TUBE_KNOCKDOWN * s["tensile_strength_mpa"] * 1e6)
                source = (
                    f"half the published {s['tensile_strength_mpa']:g} MPa tensile strength"
                    if allow < TUBE_ALLOWABLE_PA
                    else source
                )
            ms = allow / stress - 1 if stress > 0 else math.inf
            problems = []
            if max_od is not None and od > max_od:
                problems.append(
                    f"is {od:g} mm across, over the {max_od:.0f} mm the root depth allows"
                )
            if ms < MARGIN_WARN:
                problems.append(
                    f"reaches {stress / 1e6:.0f} MPa, margin {ms:+.2f} (at least "
                    f"{MARGIN_WARN:+.2f} wanted)"
                )
            deflection = None
            if tip_load is not None:
                e_pa = (s.get("youngs_modulus_gpa") or TUBE_E_GPA) * 1e9
                force, arm = tip_load
                deflection = force * arm**3 / (3 * e_pa * sec["I_m4"])
                if deflection > BOOM_DEFLECTION_MAX * arm:
                    problems.append(
                        f"bends {deflection * 1000:.1f} mm at the motor under full thrust, over "
                        f"{_pct(BOOM_DEFLECTION_MAX)} of the {arm * 1000:.0f} mm arm "
                        f"({BOOM_DEFLECTION_MAX * arm * 1000:.1f} mm)"
                    )
            n = max(1, math.ceil(length_needed_mm / s["length_mm"] - 1e-9))
            rows.append(
                {
                    "part": t,
                    "stress_mpa": stress / 1e6,
                    "allow_mpa": allow / 1e6,
                    "allow_source": source,
                    "margin": ms,
                    "problems": problems,
                    "quantity": n,
                    "installed_mass_g": s["mass_per_m_g"] * length_needed_mm / 1000,
                    "deflection_mm": deflection * 1000 if deflection is not None else None,
                }
            )
        return rows

    def _tube_choice(
        self,
        role: str,
        rows: list[dict[str, Any]],
        intro: str,
        length_mm: float,
        count_note: str,
    ) -> Choice:
        c = Choice(role, None)
        locked = self.locked_part(role)
        if locked is not None:
            best_row = next((r for r in rows if r["part"]["id"] == locked["id"]), None)
        else:
            ok = sorted(
                (r for r in rows if not r["problems"]),
                key=lambda r: (r["part"]["spec"]["mass_per_m_g"], unit_price(r["part"])[0] or 1e6),
            )
            best_row = ok[0] if ok else None
        if best_row is None:
            strongest = max(rows, key=lambda r: r["margin"]) if rows else None
            c.unfilled_reason = (
                intro
                + " No carbon tube in the catalogue meets it"
                + (
                    f": the strongest, {part_name(strongest['part'])}, "
                    + "; ".join(strongest["problems"])
                    + "."
                    if strongest
                    else "."
                )
            )
            c.alternatives = [
                {
                    "part": r["part"],
                    "feasible": False,
                    "reason_lost": "It " + "; ".join(r["problems"]) + ".",
                    "deltas": {"margin": r["margin"]},
                }
                for r in rows
            ]
            return c
        t = best_row["part"]
        s = t["spec"]
        c.part, c.quantity = t, best_row["quantity"]
        c.line_mass_g = best_row["installed_mass_g"]
        c.reasoning = [
            intro,
            f"The {s['outer_diameter_mm']:g} x {s['inner_diameter_mm']:g} mm "
            f"{s['layup'].replace('_', '-')} tube reaches {best_row['stress_mpa']:.0f} MPa against "
            f"{best_row['allow_mpa']:.0f} MPa allowable ({best_row['allow_source']}), a margin of "
            f"{best_row['margin']:+.2f}; it is the lightest per metre "
            f"({s['mass_per_m_g']:g} g/m) that has at least {MARGIN_WARN:+.2f}.",
            f"{length_mm / 1000:.2f} m is needed ({count_note}): {c.quantity} x "
            f"{s['length_mm'] / 1000:g} m tube"
            + ("s" if c.quantity > 1 else "")
            + f", {_fmt_g(c.line_mass_g)} installed.",
        ]
        for problem in best_row["problems"]:
            c.flag("constraint", self._held(c.role) + ": it " + problem + ".")
        c.metrics = {
            "margin": best_row["margin"],
            "stress_mpa": best_row["stress_mpa"],
            "deflection_mm": best_row.get("deflection_mm"),
        }
        if best_row.get("deflection_mm") is not None:
            c.reasoning.insert(
                2,
                f"Under full thrust its tip bends {best_row['deflection_mm']:.1f} mm, inside the "
                f"{_pct(BOOM_DEFLECTION_MAX)} stiffness rule.",
            )
        for r in rows:
            if r is best_row:
                continue
            c.alternatives.append(
                {
                    "part": r["part"],
                    "feasible": not r["problems"],
                    "reason_lost": "It " + "; ".join(r["problems"]) + "."
                    if r["problems"]
                    else (
                        f"Heavier ({r['part']['spec']['mass_per_m_g']:g} vs "
                        f"{s['mass_per_m_g']:g} g/m)."
                        if r["part"]["spec"]["mass_per_m_g"] >= s["mass_per_m_g"]
                        else "Lighter and strong enough, but not the owner's choice."
                    ),
                    "deltas": {
                        "mass_g": r["installed_mass_g"] - best_row["installed_mass_g"],
                        "price_eur": _price_delta(r["part"], t, r["quantity"], c.quantity),
                        "margin": r["margin"],
                    },
                }
            )
        c.feasible = [{"part": r["part"], "row": r} for r in rows if not r["problems"]]
        return c

    def _spar(self, mass_kg: float) -> Choice:
        ctx = self.ctx
        m_ult = (ctx["spar_moment_ult_nm"] or 0) * mass_kg / ctx["mass_kg"]
        span = self.p["wing"]["span_mm"]
        intro = (
            f"At {mass_kg:.2f} kg the wing root bending moment is {m_ult:.1f} N·m at ultimate "
            f"load ({self.settings['checks']['manoeuvre_load_factor']:g} g x "
            f"{ctx['safety_factor']:g} safety factor) and the tube must fit inside "
            f"{ctx['spar_max_od_mm']:.0f} mm of root depth."
        )
        rows = self._tube_rows(m_ult, ctx["spar_max_od_mm"], span)
        c = self._tube_choice("spar_tube", rows, intro, span, "the full span")
        if c.part is not None and c.quantity > 1:
            c.flag(
                "joint",
                f"The {span / 1000:.2f} m span needs {c.quantity} tubes joined; put the joint "
                "sleeve away from the root, where the bending moment is highest.",
            )
        if ctx["scale"] == "final" and c.part is not None:
            c.flag(
                "final_scale",
                "Final-scale designs are analysed with bending-sized carbon spar caps; this "
                "tube is a check, not the analysed structure.",
            )
        return c

    def _boom(self, mass_kg: float, lift: dict[str, Any] | None) -> Choice:
        ctx = self.ctx
        p = self.p
        sf = ctx["safety_factor"]
        arm = (ctx["boom_arm_mm"] or p["booms"]["length_mm"] / 2) / 1000
        thrust = (lift or {}).get("full", {}).get("thrust_n") or 0.0
        m_thrust = thrust * arm * sf
        m_land = ctx["boom_landing_moment_ult_nm"] * mass_kg / ctx["mass_kg"]
        moment = max(m_thrust, m_land)
        per_boom = p["booms"]["length_mm"]
        if ctx.get("tail_support_kind") == "booms" and ctx.get("tail_support_mm"):
            per_boom += ctx["tail_support_mm"]
        count = max(1, round(p["booms"]["count"]))
        intro = (
            f"Each boom carries {moment:.1f} N·m at ultimate load: "
            + (
                f"full motor thrust {thrust:.1f} N at {arm * 1000:.0f} mm from the wing spar"
                if m_thrust >= m_land
                else "a hard landing (3 g on the skids)"
            )
            + f", x {sf:g} safety factor."
        )
        rows = self._tube_rows(moment, None, per_boom, (thrust, arm) if thrust > 0 else None)
        for r in rows:
            r["quantity"] *= count
            r["installed_mass_g"] *= count
        c = self._tube_choice(
            "boom_tube",
            rows,
            intro,
            per_boom * count,
            f"{count} booms of {per_boom / 1000:.2f} m"
            + (" including the tail extension" if per_boom > p["booms"]["length_mm"] else ""),
        )
        if (
            c.part is not None
            and abs(c.part["spec"]["outer_diameter_mm"] - p["booms"]["diameter_mm"]) > 0.5
        ):
            c.flag(
                "design_mismatch",
                f"The design's booms are {p['booms']['diameter_mm']:g} mm; this tube is "
                f"{c.part['spec']['outer_diameter_mm']:g} mm. Update booms.diameter_mm.",
            )
        return c

    # -- outputs -------------------------------------------------------------------------------

    def analysis_parts(self, choices: dict[str, Choice]) -> dict[str, Any]:
        """The ``parts`` argument of ``run_analysis`` for these choices."""
        out: dict[str, Any] = {}

        def spec(role: str) -> dict[str, Any] | None:
            ch = choices.get(role)
            if ch is None or ch.part is None:
                return None
            return {
                **copy.deepcopy(ch.part["spec"]),
                "label": part_name(ch.part),
                "mass_g": ch.part["mass_g"],
                "part_id": ch.part["id"],
            }

        for role in ("lift_motor", "lift_prop", "cruise_motor", "pusher_prop", "esc", "tilt_servo"):
            s = spec(role)
            if s:
                out[role] = s
        b = choices.get("battery")
        if b is not None and b.part is not None and b.metrics:
            out["battery"] = {
                "chemistry": b.metrics["chemistry"],
                "cells_series": self.cells,
                "cells_parallel": b.metrics["cells_parallel"],
                "capacity_mah": b.metrics["capacity_mah"],
                "discharge_c_continuous": b.metrics["c_cont"],
                "discharge_c_burst": b.metrics["c_burst"],
                "mass_g": b.line_mass_g,
                "label": b.metrics["chosen_option"]["label"],
                "part_id": b.part["id"],
            }
        av = [choices[r] for r in ("autopilot", "gps", "radio", "telemetry") if r in choices]
        av = [a for a in av if a.part is not None]
        if av:
            total = sum(a.line_mass_g for a in av) + POWER_MODULE_G
            out["avionics"] = {
                "mass_g": total,
                "label": ", ".join(part_name(a.part) for a in av if a.part),
                "detail": "; ".join(
                    f"{part_name(a.part)} {a.part['mass_g']:g} g" for a in av if a.part
                )
                + f"; power module {POWER_MODULE_G:g} g (estimate)",
            }
        for role in ("spar_tube", "boom_tube"):
            s = spec(role)
            if s:
                out[role] = s
        return out


def _price_delta(
    a: dict[str, Any], b: dict[str, Any], qa: int = 1, qb: int | None = None
) -> float | None:
    pa, pb = unit_price(a)[0], unit_price(b)[0]
    if pa is None or pb is None:
        return None
    return pa * qa - pb * (qb if qb is not None else qa)


def _lost_reason(eff: float, best_eff: float, dmass: float, dprice: float | None) -> str:
    if round(eff / EFFICIENCY_STEP_G_PER_W) < round(best_eff / EFFICIENCY_STEP_G_PER_W):
        return f"Lower hover efficiency ({eff:.1f} vs {best_eff:.1f} g/W)."
    if dmass > 0.5:
        return f"Same efficiency band but heavier (+{dmass:.0f} g each)."
    if dprice is not None and dprice > 0:
        return f"Same efficiency and mass but dearer ({_fmt_eur(dprice)} more each)."
    return (
        "Not chosen: the lift motors and the battery were settled together in the mass "
        "iteration, and this pair would change that balance."
    )


def radio_legality(freq_mhz: float) -> tuple[str, str]:
    """('legal' | 'restricted' | 'illegal', plain note) for radio use in Ireland (ComReg; EU
    SRD decision and ERC Rec 70-03)."""
    if 863 <= freq_mhz <= 870:
        return (
            "legal",
            "868 MHz SRD band, licence-exempt in Ireland within the EU limits (25 mW ERP with "
            "duty cycle or LBT; 500 mW at 10 % duty in 869.40-869.65 MHz). Use the EU firmware.",
        )
    if 2400 <= freq_mhz <= 2483.5:
        return (
            "legal",
            "2.4 GHz band, licence-exempt in Ireland at up to 100 mW EIRP with listen-before-"
            "talk (use the EU LBT firmware and keep power at or under 100 mW).",
        )
    if 433.0 <= freq_mhz <= 434.8:
        return (
            "restricted",
            "433 MHz is legal in Ireland only at 10 mW ERP; set TXPOWER to 10 dBm or less, "
            "which shortens the range, or use an 868 MHz radio",
        )
    if 902 <= freq_mhz <= 928:
        return ("illegal", "uses the US 915 MHz band, which is not legal in Ireland")
    return ("restricted", f"{freq_mhz:g} MHz: check ComReg's rules before use")


# ---------------------------------------------------------------------------
# The parts list
# ---------------------------------------------------------------------------


def part_summary(part: dict[str, Any]) -> dict[str, Any]:
    price, src = unit_price(part)
    return {
        "id": part["id"],
        "category": part["category"],
        "manufacturer": part["manufacturer"],
        "model": part["model"],
        "name": part_name(part),
        "mass_g": part["mass_g"],
        "verified": bool(part.get("verified")),
        "source": part.get("source", ""),
        "price_eur": price,
        "price_source": src,
        "spec": part["spec"],
    }


def _listing_out(li: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": li.get("id"),
        "supplier_name": li["supplier_name"],
        "country": li["country"],
        "url": li["url"],
        "price_eur": li.get("price_eur"),
        "in_stock": li.get("in_stock"),
        "last_checked_at": li.get("last_checked_at"),
        "url_ok": li.get("url_ok"),
        "url_status": li.get("url_status"),
        "stale": listing_is_stale(li),
    }


def build_parts_list(
    parameters: dict[str, Any],
    mission: dict[str, Any],
    settings: dict[str, Any],
    analysis: dict[str, Any],
    catalogue: list[dict[str, Any]],
    locked: dict[str, dict[str, Any]] | None = None,
    mass_solver: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Choose a full parts set and lay it out as the parts list (section 4 of the contract).

    ``analysis`` is a generic-parts analysis of this design (fast mode is enough).
    ``mass_solver(analysis_parts) -> solve_mass result`` lets the selection iterate the mass and
    report the design's mass with the selected parts.
    """
    ctx = design_context(parameters, mission, settings, analysis)
    sel = Selector(parameters, mission, settings, ctx, catalogue, locked, mass_solver)
    run = sel.run()
    choices: dict[str, Choice] = run["choices"]
    mass_kg = run["mass_kg"]
    generic = ctx["generic_masses"]
    tier1_role_masses = {
        "lift_motor": generic.get("motors_front", 0) + generic.get("motors_rear", 0),
        "lift_prop": generic.get("props_front", 0) + generic.get("props_rear", 0),
        "esc": generic.get("escs_front", 0)
        + generic.get("escs_rear", 0)
        + generic.get("pusher_esc", 0),
        "cruise_motor": generic.get("pusher_motor", 0),
        "pusher_prop": generic.get("pusher_prop", 0),
        "tilt_servo": generic.get("tilt_mechanism", 0),
        "battery": generic.get("battery", 0),
        "spar_tube": generic.get("wing_spar", 0),
        "boom_tube": generic.get("booms", 0) + generic.get("tail_support", 0),
    }
    roles_out = []
    total_cost = 0.0
    unpriced: list[str] = []
    for role, ch in choices.items():
        part = ch.part
        price, price_src = unit_price(part) if part else (None, None)
        line_price = price * ch.quantity if price is not None else None
        if part is not None and line_price is None:
            unpriced.append(role)
        if line_price is not None:
            total_cost += line_price
        unit_mass = part["mass_g"] if part else None
        roles_out.append(
            {
                "role": role,
                "label": ROLE_LABELS[role],
                "system": ROLE_SYSTEM[role],
                "part": part_summary(part) if part else None,
                "quantity": ch.quantity,
                "unit_mass_g": unit_mass,
                "line_mass_g": round(ch.line_mass_g, 1) if part else None,
                "unit_price_eur": price,
                "price_source": price_src,
                "line_price_eur": round(line_price, 2) if line_price is not None else None,
                "best_listing": _listing_out(best_listing(part))
                if part and best_listing(part)
                else None,
                "listings": [_listing_out(li) for li in (part.get("listings") or [])]
                if part
                else [],
                "reasoning": ch.reasoning if part else [ch.unfilled_reason or "Not filled."],
                "alternatives": [
                    {
                        "part": part_summary(a["part"]),
                        "feasible": a.get("feasible", True),
                        "reason_lost": a["reason_lost"],
                        "deltas": _clean(a.get("deltas", {})),
                        **({"tier": a["tier"]} if "tier" in a else {}),
                        **({"quantity": a["quantity"]} if "quantity" in a else {}),
                        **({"label": a["label"]} if "label" in a else {}),
                    }
                    for a in ch.alternatives
                ],
                "flags": ch.flags,
                "locked": ch.locked,
                "filled": part is not None,
                "unfilled_reason": ch.unfilled_reason if part is None else None,
                "metrics": _clean({k: v for k, v in ch.metrics.items() if k != "chosen_option"}),
                "custom_pack": ch.custom_pack,
                "tier1_mass_g": round(tier1_role_masses[role], 1)
                if role in tier1_role_masses
                else None,
            }
        )
    analysis_parts = sel.analysis_parts(choices)
    av_parts_g = sum(
        ch.line_mass_g
        for r, ch in choices.items()
        if r in ("autopilot", "gps", "radio", "telemetry") and ch.part
    )
    # Consumables: wiring fraction mass from the mass model with the parts, plus an estimated
    # cost.
    selected_mass = run["selected_mass"]
    wiring_g = None
    total_mass_kg = mass_kg
    if selected_mass is not None:
        comps = {c["key"]: c["mass_g"] for c in selected_mass["result"]["components"]}
        wiring_g = comps.get("wiring")
        total_mass_kg = selected_mass["total_max_g"] / 1000
    items = CONSUMABLES["prototype"]
    factor = CONSUMABLES_FINAL_FACTOR if ctx["scale"] == "final" else 1.0
    if parameters["layout"] == "quad_pusher" or not (
        choices.get("tilt_servo")
        and choices["tilt_servo"].part
        and choices["tilt_servo"].part["spec"]["voltage_min_v"] > 5.5
    ):
        items = [i for i in items if "Servo BEC" not in i[0]]
    cons_cost = round(sum(c for _n, c in items) * factor, 2)
    consumables = {
        "label": "Wiring, connectors, power module and fasteners",
        "system": "consumables",
        "mass_g": round(wiring_g + POWER_MODULE_G, 1) if wiring_g is not None else None,
        "mass_source": (
            f"Wiring allowance {parameters['allowances']['wiring_fraction'] * 100:.1f} % of the "
            f"empty mass (Tier 1 wiring fraction) plus the {POWER_MODULE_G:g} g power module."
        ),
        "cost_eur": cons_cost,
        "cost_source": "Estimate from typical UK hobby prices"
        + (f", x {factor:g} for a final-scale aircraft" if factor != 1 else "")
        + ".",
        "items": [{"label": n, "cost_eur": round(c * factor, 2)} for n, c in items],
    }
    total_cost += cons_cost
    parts_mass = sum(r["line_mass_g"] or 0 for r in roles_out) + (consumables["mass_g"] or 0)
    tier1_equiv = (
        sum(tier1_role_masses.get(r["role"], 0) for r in roles_out if r["filled"])
        + generic.get("avionics", 0)
        + generic.get("wiring", 0)
    )
    budget = float((settings.get("budget") or {}).get("prototype_eur", 5000.0))
    if total_cost > budget:
        status, message = (
            "over",
            (
                f"EUR {total_cost:,.0f} is EUR {total_cost - budget:,.0f} over the "
                f"EUR {budget:,.0f} budget."
            ),
        )
    elif total_cost > 0.9 * budget:
        status, message = (
            "near",
            (
                f"EUR {total_cost:,.0f} uses {total_cost / budget * 100:.0f} % of the EUR "
                f"{budget:,.0f} budget."
            ),
        )
    else:
        status, message = (
            "under",
            (
                f"EUR {total_cost:,.0f} leaves EUR {budget - total_cost:,.0f} of the EUR "
                f"{budget:,.0f} budget."
            ),
        )
    if unpriced:
        message += (
            f" {len(unpriced)} line(s) have no price ("
            + ", ".join(ROLE_LABELS[r] for r in unpriced)
            + ") and are not counted."
        )
    upgrades = _upgrades(sel, choices, total_mass_kg)
    endurance_sel = None
    b = choices.get("battery")
    if b is not None and b.part is not None:
        endurance_sel = sel.endurance_min(total_mass_kg, b.metrics["energy_wh"], sel.hover_factor)
    unfilled = [
        {"role": r["role"], "label": r["label"], "reason": r["unfilled_reason"]}
        for r in roles_out
        if not r["filled"]
    ]
    return {
        "selection_version": SELECTION_VERSION,
        "layout": parameters["layout"],
        "roles": roles_out,
        "consumables": consumables,
        "totals": {
            "mass_g": round(parts_mass, 1),
            "tier1_mass_g": round(tier1_equiv, 1),
            "mass_vs_tier1_g": round(parts_mass - tier1_equiv, 1),
            "avionics_parts_g": round(av_parts_g + POWER_MODULE_G, 1),
            "avionics_allowance_g": parameters["allowances"]["avionics_g"],
            "takeoff_mass_kg": round(total_mass_kg, 3),
            "takeoff_mass_generic_kg": round(ctx["mass_kg"], 3),
            "estimated_endurance_min": round(endurance_sel, 1)
            if endurance_sel is not None
            else None,
            "generic_endurance_min": round(ctx["endurance_min"], 1)
            if ctx["endurance_min"] is not None
            else None,
            "endurance_note": "Estimate for ranking (generic analysis scaled to the selected "
            "parts); run Analyse for the full numbers with these parts.",
            "cost_eur": round(total_cost, 2),
            "budget_eur": budget,
            "budget_status": status,
            "budget_message": message,
            "budget_fraction": round(total_cost / budget, 4) if budget > 0 else None,
            "unpriced_roles": unpriced,
        },
        "upgrades": upgrades,
        "unfilled": unfilled,
        "uk_import_note": UK_IMPORT_NOTE,
        "analysis_parts": analysis_parts,
        "selections": {
            role: {
                "part_id": ch.part["id"],
                "category": ch.part["category"],
                "quantity": ch.quantity,
                "locked": ch.locked,
            }
            for role, ch in choices.items()
            if ch.part is not None
        },
        "context": _clean({k: v for k, v in ctx.items() if k not in ("generic_masses",)}),
    }


def _clean(v: Any) -> Any:
    if isinstance(v, float):
        return round(v, 4) if math.isfinite(v) else None
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_clean(x) for x in v]
    return v


def _upgrades(sel: Selector, choices: dict[str, Choice], mass_kg: float) -> list[dict[str, Any]]:
    """Alternatives that cost more and improve endurance, mass or margins, with EUR per minute."""
    ctx = sel.ctx
    out: list[dict[str, Any]] = []
    b = choices.get("battery")
    energy = b.metrics.get("energy_wh") if b is not None and b.part is not None else None
    if energy is None:
        energy = ctx["pack_energy_wh"] or 0.0
    lm = choices.get("lift_motor")
    base_gw = lm.metrics.get("hover_g_per_w") if lm is not None and lm.part is not None else None
    base_hf = sel.hover_factor
    base_end = sel.endurance_min(mass_kg, energy, base_hf)

    def add(
        role: str,
        part: dict[str, Any],
        dprice: float | None,
        dmass_g: float,
        dend: float,
        why: str,
        label: str | None = None,
    ) -> None:
        if dprice is None or dprice <= 0.5:
            return
        if dend < 0.2 and dmass_g > -5 and not why:
            return
        out.append(
            {
                "role": role,
                "role_label": ROLE_LABELS[role],
                "part": part_summary(part),
                "label": label or part_name(part),
                "extra_cost_eur": round(dprice, 2),
                "mass_change_g": round(dmass_g, 1),
                "endurance_gain_min": round(dend, 1),
                "eur_per_min": round(dprice / dend, 2) if dend >= 0.2 else None,
                "trade_off": why
                or (
                    f"{_fmt_eur(dprice)} more for about {dend:+.1f} min of wing flight"
                    + (f" and {dmass_g:+.0f} g" if abs(dmass_g) >= 1 else "")
                    + "."
                ),
            }
        )

    # Lift motors (with the chosen propeller)
    if lm is not None and lm.part is not None:
        for alt in lm.feasible:
            part = alt["part"]
            if part is lm.part:
                continue
            ev = alt["eval"]
            dmass = 4 * (part["mass_g"] - lm.part["mass_g"])
            hf = base_hf * base_gw / ev["g_per_w"] if ev.get("g_per_w") and base_gw else base_hf
            dend = sel.endurance_min(mass_kg + dmass / 1000, energy, hf) - base_end
            add(
                "lift_motor",
                part,
                _price_delta(part, lm.part, 4),
                dmass,
                dend,
                ""
                if dend >= 0.2
                else (
                    f"{ev['g_per_w']:.1f} g/W in hover vs {base_gw:.1f}; more thrust margin "
                    f"({ev['max_thrust_g']:,.0f} g per motor)."
                    if ev.get("max_thrust_g", 0) > (lm.metrics.get("max_thrust_g") or 0) * 1.1
                    else ""
                ),
            )
    lp = choices.get("lift_prop")
    if lp is not None and lp.part is not None:
        for alt in lp.feasible:
            part = alt["part"]
            if part is lp.part:
                continue
            ev = alt["eval"]
            dmass = 4 * (part["mass_g"] - lp.part["mass_g"])
            gw = ev.get("g_per_w")
            hf = base_hf * base_gw / gw if gw and base_gw else base_hf
            dend = sel.endurance_min(mass_kg + dmass / 1000, energy, hf) - base_end
            qa = 2 if sold_as_pair(part) else 4
            add("lift_prop", part, _price_delta(part, lp.part, qa, lp.quantity), dmass, dend, "")
    # Battery
    if b is not None and b.part is not None:
        chosen = b.metrics["chosen_option"]
        for o in b.feasible:
            if o["part"]["id"] == b.part["id"] and o["quantity"] == b.quantity:
                continue
            if o["price_eur"] is None or chosen.get("price_eur") is None:
                continue
            dend = o["endurance_min"] - chosen["endurance_min"]
            if dend < 0.2 or not o.get("fits_motors"):
                continue
            add(
                "battery",
                o["part"],
                o["price_eur"] - chosen["price_eur"],
                o["mass_g"] - chosen["mass_g"],
                dend,
                "",
                label=o["label"],
            )
    # Mass-only and margin upgrades (servos, ESC, avionics, tubes)
    for role in ("esc", "tilt_servo", "autopilot", "gps", "telemetry", "spar_tube", "boom_tube"):
        ch = choices.get(role)
        if ch is None or ch.part is None:
            continue
        for a in ch.alternatives:
            if not a.get("feasible"):
                continue
            d = a.get("deltas") or {}
            dprice = d.get("price_eur")
            dmass = d.get("mass_g") or 0.0
            if dprice is None or dprice <= 0.5:
                continue
            dend = sel.endurance_min(mass_kg + dmass / 1000, energy, base_hf) - base_end
            why = ""
            if (
                role == "esc"
                and a["part"]["spec"].get("telemetry")
                and not ch.part["spec"].get("telemetry")
            ):
                why = "Adds ESC telemetry (rpm, current, temperature in the logs)."
            elif (
                role == "esc"
                and (d.get("current_margin") or 0)
                > (
                    ch.part["spec"]["continuous_current_a"]
                    / max(ch.metrics.get("peak_current_a", 1), 1)
                )
                + 0.2
            ):
                why = f"More current margin ({d['current_margin']:.1f} x the peak)."
            elif (
                role == "tilt_servo"
                and (d.get("torque_margin") or 0) > (ch.metrics.get("torque_margin") or 0) + 0.5
            ):
                why = f"More tilt torque margin ({d['torque_margin']:.1f} x the hinge moment)."
            elif (
                role == "autopilot"
                and a["part"]["spec"]["imu_count"] > ch.part["spec"]["imu_count"]
            ):
                why = (
                    f"{a['part']['spec']['imu_count']} IMUs instead of "
                    f"{ch.part['spec']['imu_count']} (more redundancy)."
                )
            elif role == "gps" and a["part"]["spec"].get("rtk") and not ch.part["spec"].get("rtk"):
                why = "RTK-capable, multi-band positioning."
            elif (
                role in ("spar_tube", "boom_tube")
                and (d.get("margin") or 0) > (ch.metrics.get("margin") or 0) + 0.5
            ):
                why = f"Stronger: margin {d['margin']:+.2f} instead of {ch.metrics['margin']:+.2f}."
            if dmass < -5 and not why:
                why = f"Saves {-dmass:.0f} g ({dend:+.1f} min)."
            if not why and dend < 0.2:
                continue
            add(role, a["part"], dprice, dmass, dend, why)
    out.sort(
        key=lambda u: (
            u["eur_per_min"] is None,
            u["eur_per_min"] if u["eur_per_min"] is not None else u["extra_cost_eur"],
        )
    )
    per_role: dict[str, int] = {}
    kept = []
    for u in out:
        if per_role.get(u["role"], 0) >= UPGRADES_PER_ROLE:
            continue
        per_role[u["role"]] = per_role.get(u["role"], 0) + 1
        kept.append(u)
    return kept[:12]


__all__ = [
    "ROLE_LABELS",
    "ROLE_ORDER",
    "ROLE_SYSTEM",
    "SELECTION_VERSION",
    "SYSTEM_LABELS",
    "UK_IMPORT_NOTE",
    "best_listing",
    "build_parts_list",
    "design_context",
    "listing_is_stale",
    "radio_legality",
    "unit_price",
]
