"""Part categories and the specification fields the engine will need for each.

Each category has a Pydantic spec model. ``GET /api/parts/categories`` serves the field list
generated from these models so the UI can build forms without hand-copied metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.introspect import field_list


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ThrustPoint(_Spec):
    prop: str = Field(description="Propeller used for this test point, e.g. '15x5.5'.")
    voltage_v: float = Field(gt=0, description="Supply voltage during the test.")
    throttle_pct: float = Field(ge=0, le=100, description="Throttle setting in percent.")
    thrust_g: float = Field(ge=0, description="Measured thrust in grams.")
    current_a: float = Field(ge=0, description="Measured current in amps.")
    power_w: float = Field(ge=0, description="Electrical power in watts.")
    rpm: float = Field(ge=0, description="Propeller speed in revolutions per minute.")


class MotorSpec(_Spec):
    kv_rpm_per_v: float = Field(gt=0, description="Motor speed constant: rpm per volt.")
    resistance_ohm: float = Field(ge=0, description="Winding resistance (phase to phase).")
    no_load_current_a: float = Field(ge=0, description="Current drawn with no propeller.")
    max_current_a: float = Field(gt=0, description="Maximum continuous current.")
    max_power_w: float = Field(gt=0, description="Maximum continuous electrical power.")
    lipo_cells_min: int = Field(ge=1, description="Minimum battery cell count (S).")
    lipo_cells_max: int = Field(ge=1, description="Maximum battery cell count (S).")
    stator_size: str = Field(description="Stator size code, e.g. '4110' (41 mm, 10 mm tall).")
    shaft_mm: float = Field(gt=0, description="Shaft diameter.")
    mount_pattern: str = Field(description="Bolt pattern, e.g. '25x25 M3'.")
    thrust_data: list[ThrustPoint] = Field(
        default_factory=list,
        description="Measured test points: prop, voltage, throttle, thrust, current, "
        "power and rpm.",
    )

    @model_validator(mode="after")
    def _cells(self) -> MotorSpec:
        if self.lipo_cells_max < self.lipo_cells_min:
            raise ValueError("lipo_cells_max must be at least lipo_cells_min.")
        return self


class PropellerSpec(_Spec):
    diameter_mm: float = Field(gt=0, description="Propeller diameter.")
    pitch_mm: float = Field(gt=0, description="Propeller pitch: forward travel per turn.")
    trade_size: str | None = Field(
        None, description="Trade size as sold, e.g. '15x5.5' (inches), shown beside mm values."
    )
    blades: int = Field(ge=2, description="Number of blades.")
    folding: bool = Field(description="Whether the blades fold back when stopped.")
    hub_bore_mm: float = Field(gt=0, description="Centre hole diameter.")
    material: str = Field(description="Blade material, e.g. carbon, nylon, wood.")
    max_rpm: float | None = Field(None, gt=0, description="Manufacturer rpm limit, if given.")


class EscSpec(_Spec):
    continuous_current_a: float = Field(gt=0, description="Continuous current rating.")
    burst_current_a: float = Field(gt=0, description="Short burst current rating.")
    lipo_cells_min: int = Field(ge=1, description="Minimum battery cell count (S).")
    lipo_cells_max: int = Field(ge=1, description="Maximum battery cell count (S).")
    firmware: str = Field(description="ESC firmware family, e.g. BLHeli_32, AM32.")
    bec_v: float | None = Field(None, gt=0, description="Built-in BEC output voltage, if any.")
    telemetry: bool = Field(description="Whether the ESC reports telemetry to the autopilot.")


class ServoSpec(_Spec):
    torque_kg_cm: float = Field(gt=0, description="Stall torque.")
    speed_s_per_60deg: float = Field(gt=0, description="Time to move 60 degrees.")
    voltage_min_v: float = Field(gt=0, description="Minimum supply voltage.")
    voltage_max_v: float = Field(gt=0, description="Maximum supply voltage.")
    gear_material: str = Field(description="Gear material, e.g. metal, plastic.")
    width_mm: float = Field(gt=0, description="Case width.")
    length_mm: float = Field(gt=0, description="Case length.")
    height_mm: float = Field(gt=0, description="Case height.")
    digital: bool = Field(description="Digital (true) or analogue (false) servo.")


class BatterySpec(_Spec):
    chemistry: Literal["lipo", "li-ion"] = Field(description="Cell chemistry.")
    cells_series: int = Field(ge=1, description="Cells in series (S).")
    cells_parallel: int = Field(ge=1, description="Cells in parallel (P).")
    capacity_mah: float = Field(gt=0, description="Pack capacity.")
    nominal_voltage_v: float = Field(gt=0, description="Nominal pack voltage.")
    discharge_c_continuous: float = Field(gt=0, description="Continuous discharge rating.")
    discharge_c_burst: float = Field(gt=0, description="Burst discharge rating.")
    length_mm: float = Field(gt=0, description="Pack length.")
    width_mm: float = Field(gt=0, description="Pack width.")
    height_mm: float = Field(gt=0, description="Pack height.")
    connector: str = Field(description="Main connector, e.g. XT60, XT90.")


class CellSpec(_Spec):
    chemistry: str = Field(description="Cell chemistry, e.g. li-ion.")
    capacity_mah: float = Field(gt=0, description="Cell capacity.")
    nominal_voltage_v: float = Field(gt=0, description="Nominal cell voltage.")
    max_continuous_discharge_a: float = Field(gt=0, description="Continuous discharge current.")
    diameter_mm: float = Field(gt=0, description="Cell diameter.")
    length_mm: float = Field(gt=0, description="Cell length.")
    format: str = Field(description="Cell format, e.g. '21700'.")


class AutopilotSpec(_Spec):
    firmware: Literal["ardupilot"] = Field(description="Autopilot firmware; ArduPilot only.")
    pwm_outputs: int = Field(ge=1, description="Number of PWM servo/motor outputs.")
    can_ports: int = Field(ge=0, description="Number of CAN ports.")
    uarts: int = Field(ge=0, description="Number of serial (UART) ports.")
    imu_count: int = Field(ge=1, description="Number of inertial measurement units.")
    voltage_in_min_v: float = Field(gt=0, description="Minimum supply voltage.")
    voltage_in_max_v: float = Field(gt=0, description="Maximum supply voltage.")


class GpsSpec(_Spec):
    constellations: list[str] = Field(
        description="Satellite systems supported, e.g. GPS, Galileo, GLONASS, BeiDou."
    )
    rtk: bool = Field(description="Whether centimetre-level RTK positioning is supported.")
    update_rate_hz: float = Field(gt=0, description="Position update rate.")
    interface: str = Field(description="Connection to the autopilot, e.g. UART, CAN.")


class RadioSpec(_Spec):
    kind: Literal["rc_link"] = Field(description="Radio role; the pilot's control link.")
    frequency_mhz: float = Field(gt=0, description="Operating frequency.")
    range_km_los: float = Field(gt=0, description="Range in line of sight (A3 rules apply).")
    channels: int = Field(ge=1, description="Number of control channels.")
    telemetry: bool = Field(description="Whether the link carries telemetry back.")


class TelemetrySpec(_Spec):
    frequency_mhz: float = Field(gt=0, description="Operating frequency.")
    range_km_los: float = Field(gt=0, description="Range in line of sight.")
    air_rate_kbps: float = Field(gt=0, description="Over-the-air data rate.")
    interface: str = Field(description="Connection to the autopilot, e.g. UART.")


class CarbonTubeSpec(_Spec):
    outer_diameter_mm: float = Field(gt=0, description="Outer diameter.")
    inner_diameter_mm: float = Field(gt=0, description="Inner diameter.")
    length_mm: float = Field(gt=0, description="Length as sold.")
    layup: Literal["pultruded", "roll_wrapped"] = Field(
        description="How the tube is made. Roll-wrapped is stiffer in bending for its mass."
    )
    mass_per_m_g: float = Field(gt=0, description="Mass per metre.")
    youngs_modulus_gpa: float | None = Field(None, gt=0, description="Stiffness, if published.")
    tensile_strength_mpa: float | None = Field(
        None, gt=0, description="Tensile strength, if published."
    )

    @model_validator(mode="after")
    def _wall(self) -> CarbonTubeSpec:
        if self.inner_diameter_mm >= self.outer_diameter_mm:
            raise ValueError("inner_diameter_mm must be smaller than outer_diameter_mm.")
        return self


@dataclass(frozen=True)
class CategoryDef:
    key: str
    label: str
    description: str
    model: type[BaseModel]


CATEGORIES: list[CategoryDef] = [
    CategoryDef("motor", "Motor", "Brushless lift and cruise motors.", MotorSpec),
    CategoryDef("propeller", "Propeller", "Fixed and folding propellers.", PropellerSpec),
    CategoryDef("esc", "ESC", "Electronic speed controllers.", EscSpec),
    CategoryDef("servo", "Servo", "Tilt and control-surface servos.", ServoSpec),
    CategoryDef("battery", "Battery", "Complete battery packs.", BatterySpec),
    CategoryDef("cell", "Cell", "Individual cells for custom packs.", CellSpec),
    CategoryDef("autopilot", "Autopilot", "Flight controllers running ArduPilot.", AutopilotSpec),
    CategoryDef("gps", "GPS", "Satellite positioning receivers.", GpsSpec),
    CategoryDef("radio", "Radio", "Pilot control links.", RadioSpec),
    CategoryDef("telemetry", "Telemetry", "Ground-station data links.", TelemetrySpec),
    CategoryDef("carbon_tube", "Carbon tube", "Spar and boom tubes.", CarbonTubeSpec),
]

CATEGORY_KEYS: tuple[str, ...] = tuple(c.key for c in CATEGORIES)
_BY_KEY: dict[str, CategoryDef] = {c.key: c for c in CATEGORIES}


def spec_model_for(key: str) -> type[BaseModel]:
    """The spec model for a category key; raises KeyError for an unknown key."""
    return _BY_KEY[key].model


def validate_spec(category: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise a spec; raises ``pydantic.ValidationError``."""
    return spec_model_for(category).model_validate(spec).model_dump()


def category_payloads() -> list[dict[str, Any]]:
    """The ``GET /api/parts/categories`` payload."""
    return [
        {
            "key": c.key,
            "label": c.label,
            "description": c.description,
            "fields": field_list(c.model),
        }
        for c in CATEGORIES
    ]
