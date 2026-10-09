"""Design parameters document (schema_version 1).

All lengths are millimetres, angles degrees. Each field's ``description`` is the plain-language
explanation shown in the UI; the frontend reads it from ``GET /api/schema/design``.
Validation is limited to shape and sanity: the 24 kg design limit is a check, not an input
constraint, so a draft or version is never rejected for exceeding it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.defaults import DEFAULT_DESIGN_PARAMETERS as _D
from app.defaults import DESIGN_SCHEMA_VERSION

Layout = Literal["front_tilt", "rear_tilt", "quad_pusher"]
CrossSection = Literal["ellipse", "rounded_rect"]
TailType = Literal["conventional", "v_tail", "inverted_v", "twin_boom_h"]
LandingGearType = Literal["skids", "legs", "none"]

LAYOUT_OPTIONS = [
    {
        "value": "front_tilt",
        "label": "Front tilt",
        "note": "Front pair of motors tilts forward to pull in cruise; rear pair stops. "
        "Common and supported by ArduPilot QuadPlane tiltrotor.",
    },
    {
        "value": "rear_tilt",
        "label": "Rear tilt",
        "note": "Less common in ArduPilot; support to be confirmed in Phase 2",
    },
    {
        "value": "quad_pusher",
        "label": "Quad + pusher",
        "note": "Four fixed lift motors plus a separate pusher motor. No tilt mechanism; "
        "simplest transition, a little heavier.",
    },
]


class _Doc(BaseModel):
    # allow_inf_nan=False: NaN/Infinity are not JSON and would come back as null.
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Wing(_Doc):
    span_mm: float = Field(
        _D["wing"]["span_mm"],
        gt=0,
        description="Wingspan: tip-to-tip distance across both wings.",
        json_schema_extra={"label": "Wingspan", "unit": "mm"},
    )
    root_chord_mm: float = Field(
        _D["wing"]["root_chord_mm"],
        gt=0,
        description="Chord (front-to-back width of the wing) where the wing meets the fuselage.",
        json_schema_extra={"label": "Root chord", "unit": "mm"},
    )
    tip_chord_mm: float = Field(
        _D["wing"]["tip_chord_mm"],
        gt=0,
        description="Chord at the wing tip. Must not exceed the root chord.",
        json_schema_extra={"label": "Tip chord", "unit": "mm"},
    )
    sweep_deg: float = Field(
        _D["wing"]["sweep_deg"],
        ge=-45,
        le=60,
        description="Backward angle of the wing leading edge. 0 is a straight wing.",
        json_schema_extra={"label": "Sweep", "unit": "°"},
    )
    dihedral_deg: float = Field(
        _D["wing"]["dihedral_deg"],
        ge=-20,
        le=30,
        description="Upward angle of each wing from the horizontal. A little dihedral helps "
        "roll stability.",
        json_schema_extra={"label": "Dihedral", "unit": "°"},
    )
    incidence_deg: float = Field(
        _D["wing"]["incidence_deg"],
        ge=-10,
        le=15,
        description="Angle of the wing chord relative to the fuselage centreline.",
        json_schema_extra={"label": "Incidence", "unit": "°"},
    )
    airfoil: str = Field(
        _D["wing"]["airfoil"],
        min_length=1,
        max_length=50,
        description="Identifier of the wing section shape. sd7037 is a well-known "
        "low-speed glider section; the Phase 3 library offers alternatives.",
        json_schema_extra={"label": "Airfoil", "unit": None},
    )
    x_le_mm: float = Field(
        _D["wing"]["x_le_mm"],
        ge=0,
        description="Position of the wing root leading edge, measured back from the fuselage nose.",
        json_schema_extra={"label": "Wing position from nose", "unit": "mm"},
    )
    z_mm: float = Field(
        _D["wing"]["z_mm"],
        description="Vertical offset of the wing from the fuselage centreline. "
        "0 is mid-wing, positive is up.",
        json_schema_extra={"label": "Wing height", "unit": "mm"},
    )

    @model_validator(mode="after")
    def _tip_not_wider_than_root(self) -> Wing:
        if self.tip_chord_mm > self.root_chord_mm:
            raise ValueError(
                "The tip chord must not be larger than the root chord "
                f"({self.tip_chord_mm:g} mm > {self.root_chord_mm:g} mm)."
            )
        return self


class Fuselage(_Doc):
    length_mm: float = Field(
        _D["fuselage"]["length_mm"],
        gt=0,
        description="Overall fuselage length from nose to tail.",
        json_schema_extra={"label": "Fuselage length", "unit": "mm"},
    )
    width_mm: float = Field(
        _D["fuselage"]["width_mm"],
        gt=0,
        description="Maximum fuselage width.",
        json_schema_extra={"label": "Fuselage width", "unit": "mm"},
    )
    height_mm: float = Field(
        _D["fuselage"]["height_mm"],
        gt=0,
        description="Maximum fuselage height.",
        json_schema_extra={"label": "Fuselage height", "unit": "mm"},
    )
    cross_section: CrossSection = Field(
        _D["fuselage"]["cross_section"],
        description="Shape of the fuselage cross-section.",
        json_schema_extra={
            "label": "Cross-section",
            "unit": None,
            "enum": [
                {"value": "ellipse", "label": "Ellipse"},
                {"value": "rounded_rect", "label": "Rounded rectangle"},
            ],
        },
    )


class Booms(_Doc):
    count: int = Field(
        _D["booms"]["count"],
        ge=1,
        le=4,
        description="Number of motor booms. Two (one each side) is the standard quad layout.",
        json_schema_extra={"label": "Boom count", "unit": None},
    )
    lateral_offset_mm: float = Field(
        _D["booms"]["lateral_offset_mm"],
        gt=0,
        description="Distance of each boom from the aircraft centreline.",
        json_schema_extra={"label": "Boom offset from centreline", "unit": "mm"},
    )
    length_mm: float = Field(
        _D["booms"]["length_mm"],
        gt=0,
        description="Length of each boom.",
        json_schema_extra={"label": "Boom length", "unit": "mm"},
    )
    x_offset_mm: float = Field(
        _D["booms"]["x_offset_mm"],
        description="Position of the boom front relative to the wing leading edge. "
        "Negative means the boom starts ahead of the wing.",
        json_schema_extra={"label": "Boom front position", "unit": "mm"},
    )


class Motors(_Doc):
    front_x_mm: float = Field(
        _D["motors"]["front_x_mm"],
        ge=0,
        description="Position of the front motors along the boom, measured from the boom front.",
        json_schema_extra={"label": "Front motor position", "unit": "mm"},
    )
    rear_x_mm: float = Field(
        _D["motors"]["rear_x_mm"],
        ge=0,
        description="Position of the rear motors along the boom, measured from the boom front.",
        json_schema_extra={"label": "Rear motor position", "unit": "mm"},
    )
    height_mm: float = Field(
        _D["motors"]["height_mm"],
        description="Height of the motor above the boom centreline.",
        json_schema_extra={"label": "Motor height", "unit": "mm"},
    )

    @model_validator(mode="after")
    def _front_ahead_of_rear(self) -> Motors:
        if self.rear_x_mm <= self.front_x_mm:
            raise ValueError("The rear motors must sit behind the front motors along the boom.")
        return self


class Tilt(_Doc):
    axis_x_mm: float = Field(
        _D["tilt"]["axis_x_mm"],
        ge=0,
        description="Position of the tilt axis along the boom, from the boom front. "
        "Ignored for the quad + pusher layout.",
        json_schema_extra={"label": "Tilt axis position", "unit": "mm"},
    )
    max_angle_deg: float = Field(
        _D["tilt"]["max_angle_deg"],
        gt=0,
        le=120,
        description="How far the tilting motors rotate from vertical (hover) towards "
        "horizontal (cruise). 90 is fully forward.",
        json_schema_extra={"label": "Maximum tilt angle", "unit": "°"},
    )


class Pusher(_Doc):
    prop_diameter_mm: float = Field(
        _D["pusher"]["prop_diameter_mm"],
        gt=0,
        description="Diameter of the pusher propeller. Only used for the quad + pusher layout.",
        json_schema_extra={"label": "Pusher propeller diameter", "unit": "mm"},
    )
    x_mm: float = Field(
        _D["pusher"]["x_mm"],
        ge=0,
        description="Position of the pusher motor, measured back from the fuselage nose.",
        json_schema_extra={"label": "Pusher motor position", "unit": "mm"},
    )


class Tail(_Doc):
    type: TailType = Field(
        _D["tail"]["type"],
        description="Tail arrangement.",
        json_schema_extra={
            "label": "Tail type",
            "unit": None,
            "enum": [
                {"value": "conventional", "label": "Conventional (horizontal + vertical)"},
                {"value": "v_tail", "label": "V-tail"},
                {"value": "inverted_v", "label": "Inverted V"},
                {"value": "twin_boom_h", "label": "Twin-boom H-tail"},
            ],
        },
    )
    span_mm: float = Field(
        _D["tail"]["span_mm"],
        gt=0,
        description="Width of the tail surfaces, tip to tip.",
        json_schema_extra={"label": "Tail span", "unit": "mm"},
    )
    chord_mm: float = Field(
        _D["tail"]["chord_mm"],
        gt=0,
        description="Front-to-back width of the tail surfaces.",
        json_schema_extra={"label": "Tail chord", "unit": "mm"},
    )
    arm_mm: float = Field(
        _D["tail"]["arm_mm"],
        gt=0,
        description="Tail arm: distance from the wing quarter-chord to the tail quarter-chord. "
        "A longer arm gives more pitch stability for the same tail size.",
        json_schema_extra={"label": "Tail arm", "unit": "mm"},
    )
    height_mm: float = Field(
        _D["tail"]["height_mm"],
        ge=0,
        description="Vertical extent of the tail surfaces.",
        json_schema_extra={"label": "Tail height", "unit": "mm"},
    )


class NoseBay(_Doc):
    length_mm: float = Field(
        _D["nose_bay"]["length_mm"],
        gt=0,
        description="Length of the swappable nose camera bay. Geometry only: the payload mass "
        "range lives in the mission.",
        json_schema_extra={"label": "Nose bay length", "unit": "mm"},
    )
    width_mm: float = Field(
        _D["nose_bay"]["width_mm"],
        gt=0,
        description="Width of the nose bay.",
        json_schema_extra={"label": "Nose bay width", "unit": "mm"},
    )
    height_mm: float = Field(
        _D["nose_bay"]["height_mm"],
        gt=0,
        description="Height of the nose bay.",
        json_schema_extra={"label": "Nose bay height", "unit": "mm"},
    )


class LandingGear(_Doc):
    type: LandingGearType = Field(
        _D["landing_gear"]["type"],
        description="Landing gear style for vertical landings.",
        json_schema_extra={
            "label": "Landing gear",
            "unit": None,
            "enum": [
                {"value": "skids", "label": "Skids"},
                {"value": "legs", "label": "Legs"},
                {"value": "none", "label": "None (belly landing)"},
            ],
        },
    )
    height_mm: float = Field(
        _D["landing_gear"]["height_mm"],
        ge=0,
        description="Ground clearance provided by the landing gear.",
        json_schema_extra={"label": "Gear height", "unit": "mm"},
    )


class DesignParameters(_Doc):
    schema_version: Literal[1] = Field(
        DESIGN_SCHEMA_VERSION, description="Version of this document's layout."
    )
    layout: Layout = Field(
        _D["layout"],
        description="How the aircraft transitions from hover to cruise. Only layouts "
        "ArduPilot can fly are offered.",
        json_schema_extra={"label": "Layout", "unit": None, "enum": LAYOUT_OPTIONS},
    )
    wing: Wing = Field(default_factory=Wing, description="Main wing geometry.")
    fuselage: Fuselage = Field(default_factory=Fuselage, description="Fuselage geometry.")
    booms: Booms = Field(default_factory=Booms, description="Motor boom placement.")
    motors: Motors = Field(default_factory=Motors, description="Lift motor placement.")
    tilt: Tilt = Field(
        default_factory=Tilt, description="Tilt mechanism (ignored for quad + pusher)."
    )
    pusher: Pusher = Field(
        default_factory=Pusher, description="Pusher motor (only used for quad + pusher)."
    )
    tail: Tail = Field(default_factory=Tail, description="Tail geometry.")
    nose_bay: NoseBay = Field(default_factory=NoseBay, description="Nose camera bay geometry.")
    landing_gear: LandingGear = Field(default_factory=LandingGear, description="Landing gear.")

    @model_validator(mode="after")
    def _wing_fits_fuselage(self) -> DesignParameters:
        if self.wing.x_le_mm + self.wing.root_chord_mm > self.fuselage.length_mm:
            raise ValueError(
                "The wing root would extend past the end of the fuselage: wing position "
                f"({self.wing.x_le_mm:g} mm) plus root chord ({self.wing.root_chord_mm:g} mm) "
                f"exceeds the fuselage length ({self.fuselage.length_mm:g} mm)."
            )
        return self
