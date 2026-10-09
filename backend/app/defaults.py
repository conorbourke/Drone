"""Default documents: design parameters, mission and settings.

This module is the single source of truth for every default value. The Pydantic models in
``app.schemas`` read their field defaults from here, and ``PUT /api/settings`` stores only the
values that differ from ``DEFAULT_SETTINGS``.

The design defaults describe a *plausible* 2.5 kg front-tilt prototype. They are starting
values for a new project, not an analysed design: the engine that produces real numbers
arrives in Phases 2 and 3.
"""

from __future__ import annotations

from typing import Any

DESIGN_SCHEMA_VERSION = 2
MISSION_SCHEMA_VERSION = 1
SETTINGS_SCHEMA_VERSION = 2

DEFAULTS_NOTE = (
    "Starting values for a new project, not an analysed design. "
    "The analysis engine arrives in Phases 2 and 3."
)

DEFAULT_DESIGN_PARAMETERS: dict[str, Any] = {
    "schema_version": DESIGN_SCHEMA_VERSION,
    "layout": "front_tilt",
    "wing": {
        "span_mm": 1800.0,
        "root_chord_mm": 260.0,
        "tip_chord_mm": 180.0,
        "sweep_deg": 0.0,
        "dihedral_deg": 3.0,
        "incidence_deg": 2.0,
        "twist_deg": 0.0,
        "airfoil": "sd7037",
        "x_le_mm": 300.0,
        "z_mm": 0.0,
    },
    "fuselage": {
        "length_mm": 900.0,
        "width_mm": 110.0,
        "height_mm": 120.0,
        "cross_section": "rounded_rect",
    },
    "booms": {
        "count": 2,
        "lateral_offset_mm": 300.0,
        "length_mm": 700.0,
        "x_offset_mm": -250.0,
        "diameter_mm": 20.0,
    },
    "motors": {
        "front_x_mm": 40.0,
        "rear_x_mm": 660.0,
        "height_mm": 25.0,
    },
    "tilt": {
        "axis_x_mm": 40.0,
        "max_angle_deg": 90.0,
    },
    "pusher": {
        "prop_diameter_mm": 254.0,
        "x_mm": 880.0,
    },
    "tail": {
        "type": "inverted_v",
        "span_mm": 500.0,
        "chord_mm": 140.0,
        "arm_mm": 620.0,
        "height_mm": 180.0,
        "v_angle_deg": 40.0,
        "airfoil": "naca0009",
    },
    "nose_bay": {
        "length_mm": 180.0,
        "width_mm": 100.0,
        "height_mm": 100.0,
    },
    "landing_gear": {
        "type": "skids",
        "height_mm": 90.0,
    },
    "propulsion": {
        "prop_diameter_mm": 330.0,
        "prop_pitch_mm": 140.0,
        "prop_blades": 2,
    },
    "battery": {
        "chemistry": "lipo",
        "cells_series": 6,
        "cells_parallel": 1,
        "capacity_mah": 5000.0,
        "x_mm": 290.0,
    },
    "allowances": {
        "avionics_g": 220.0,
        "wiring_fraction": 0.06,
    },
}

DEFAULT_MISSION: dict[str, Any] = {
    "schema_version": MISSION_SCHEMA_VERSION,
    "scale": "prototype",
    "target_takeoff_mass_kg": 2.5,
    "target_endurance_min": 45.0,
    "cruise_speed_mps": 16.0,
    "payload_min_g": 150.0,
    "payload_max_g": 400.0,
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "schema_version": SETTINGS_SCHEMA_VERSION,
    "printer": {
        "name": "Bambu Lab P2S",
        "build_volume_mm": {"x": 256.0, "y": 256.0, "z": 256.0},
        "usable_envelope_mm": {"x": 240.0, "y": 240.0, "z": 240.0},
    },
    "limits": {
        "design_mtow_kg": 24.0,
        "legal_mtow_kg": 25.0,
        "warn_mtow_kg": 23.0,
    },
    "checks": {
        "hover_thrust_to_weight_min": 2.0,
        "static_margin_min": 0.05,
        "static_margin_max": 0.20,
        "cruise_to_stall_speed_ratio_min": 1.3,
        "battery_reserve_fraction": 0.20,
        "battery_current_max_fraction_of_rating": 0.80,
        "manoeuvre_load_factor": 3.0,
        "structural_safety_factor": 1.5,
        "transition_thrust_margin_min": 1.3,
    },
    "analysis": {"ncrit": 9.0},
    "units": {"system": "metric"},
}

_PROPOSED = "proposed, confirm in Phase 3"

# Label, plain-language description and source for every leaf of the settings document,
# keyed by dotted path. Served by GET /api/settings as ``meta`` so the UI never guesses a
# label from a key (``static_margin_min`` is a minimum, not minutes).
SETTINGS_META: dict[str, dict[str, str]] = {
    "printer.name": {
        "label": "Printer",
        "description": "The 3D printer the print files are split for.",
        "source": "Build brief: Bambu Lab P2S.",
    },
    "printer.build_volume_mm.x": {
        "label": "Build volume X",
        "description": "Printer build volume along X, in millimetres.",
        "source": "Bambu Lab P2S specification: 256 x 256 x 256 mm.",
    },
    "printer.build_volume_mm.y": {
        "label": "Build volume Y",
        "description": "Printer build volume along Y, in millimetres.",
        "source": "Bambu Lab P2S specification: 256 x 256 x 256 mm.",
    },
    "printer.build_volume_mm.z": {
        "label": "Build volume Z (height)",
        "description": "Printer build volume along Z (height), in millimetres.",
        "source": "Bambu Lab P2S specification: 256 x 256 x 256 mm.",
    },
    "printer.usable_envelope_mm.x": {
        "label": "Usable envelope X",
        "description": "Largest part size along X that print files are split to, leaving a "
        "margin inside the build volume.",
        "source": "Build brief: usable envelope 240 x 240 x 240 mm by default.",
    },
    "printer.usable_envelope_mm.y": {
        "label": "Usable envelope Y",
        "description": "Largest part size along Y that print files are split to.",
        "source": "Build brief: usable envelope 240 x 240 x 240 mm by default.",
    },
    "printer.usable_envelope_mm.z": {
        "label": "Usable envelope Z (height)",
        "description": "Largest part height that print files are split to.",
        "source": "Build brief: usable envelope 240 x 240 x 240 mm by default.",
    },
    "limits.design_mtow_kg": {
        "label": "Design take-off mass limit",
        "description": "Design limit for maximum take-off mass. A design above this fails the "
        "mass check. It is a check, not an input constraint: drafts can still be saved.",
        "source": "Build brief: 24 kg design limit, 1 kg under the legal limit.",
    },
    "limits.legal_mtow_kg": {
        "label": "Legal take-off mass limit",
        "description": "Legal upper limit for a homebuilt drone in the EU Open category (A3).",
        "source": "EU Regulation 2019/947, Open category: below 25 kg.",
    },
    "limits.warn_mtow_kg": {
        "label": "Warning take-off mass",
        "description": "Take-off mass at which the UI starts showing a warning banner.",
        "source": f"Phase 1 default (1 kg under the design limit); {_PROPOSED}.",
    },
    "checks.hover_thrust_to_weight_min": {
        "label": "Minimum hover thrust-to-weight",
        "description": "Minimum ratio of total hover thrust to aircraft weight. 2.0 means the "
        "motors can lift twice the aircraft weight, so hover sits near half throttle with "
        "margin for gusts, descent control and a motor-out case.",
        "source": f"Common multirotor/VTOL design rule; {_PROPOSED}.",
    },
    "checks.static_margin_min": {
        "label": "Minimum static margin",
        "description": "Lowest acceptable static margin, as a fraction of the mean aerodynamic "
        "chord. Below this the aircraft is close to unstable in pitch.",
        "source": f"Raymer, Aircraft Design: A Conceptual Approach; typical UAV 5-15 %; "
        f"{_PROPOSED}.",
    },
    "checks.static_margin_max": {
        "label": "Maximum static margin",
        "description": "Highest acceptable static margin. Above this the aircraft is sluggish "
        "and carries extra trim drag.",
        "source": f"Raymer, Aircraft Design: A Conceptual Approach; {_PROPOSED}.",
    },
    "checks.cruise_to_stall_speed_ratio_min": {
        "label": "Minimum cruise-to-stall speed ratio",
        "description": "Cruise speed must be at least this many times the stall speed. "
        "Stored the way the source states it (cruise / stall).",
        "source": f"Standard 1.3 x stall approach margin in aviation practice; {_PROPOSED}.",
    },
    "checks.battery_reserve_fraction": {
        "label": "Battery reserve fraction",
        "description": "Fraction of battery energy kept in reserve and never planned for use.",
        "source": f"LiPo/Li-ion practice: do not discharge below about 20 %; {_PROPOSED}.",
    },
    "checks.battery_current_max_fraction_of_rating": {
        "label": "Battery current, maximum fraction of rating",
        "description": "Peak current draw may be at most this fraction of the pack's "
        "continuous discharge rating (0.8 = 80 %).",
        "source": f"Keeps the pack inside its rating with headroom for ageing and cold; "
        f"{_PROPOSED}.",
    },
    "checks.manoeuvre_load_factor": {
        "label": "Manoeuvre load factor",
        "description": "The largest load, in multiples of the aircraft weight (g), that the "
        "wing spar and booms must carry in a pull-up or a gust. The structure checks multiply "
        "the weight by this and by the safety factor.",
        "source": "3.0 g, a common small-UAV design value (CS-23 normal category uses 3.8 g for "
        "light aircraft; Gundlach, Designing Unmanned Aircraft Systems, 2014, ch. 9 gives "
        "3-4 g for small UAVs); proposed in Phase 3, confirm.",
    },
    "checks.structural_safety_factor": {
        "label": "Structural safety factor",
        "description": "Extra margin on top of the manoeuvre load before a part may break: the "
        "spar and booms are checked at load factor x safety factor against the material "
        "strength.",
        "source": "Ultimate load = 1.5 x limit load (CS-23.303 / FAR 23.303 factor of safety); "
        "proposed in Phase 3, confirm.",
    },
    "checks.transition_thrust_margin_min": {
        "label": "Minimum transition thrust margin",
        "description": "Through the change from hover to wing flight, the thrust the motors can "
        "give must be at least this many times the thrust needed, leaving room to correct "
        "gusts and attitude.",
        "source": "Engine rule: available / required thrust of at least 1.3 through the "
        "transition (control authority for gusts and attitude); proposed in Phase 3, confirm.",
    },
    "analysis.ncrit": {
        "label": "XFOIL transition parameter (Ncrit)",
        "description": "How smooth the air and the wing surface are assumed to be when XFOIL "
        "works out the airfoil drag. 9 is clean air and a smooth wing; lower values (4-6) "
        "model a rougher printed surface or gusty air and give more drag.",
        "source": "Drela, XFOIL 6.9 user guide: Ncrit 9 for an average wind tunnel or clean "
        "air; 1-14 is the usable range.",
    },
    "units.system": {
        "label": "Unit system",
        "description": "Unit system used throughout the app. Only metric is supported.",
        "source": "Build brief: metric units throughout.",
    },
}
