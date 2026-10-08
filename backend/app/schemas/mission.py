"""Mission document (schema_version 1): what the aircraft must do."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.defaults import DEFAULT_MISSION as _M
from app.defaults import MISSION_SCHEMA_VERSION

Scale = Literal["prototype", "final"]


class Mission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = Field(
        MISSION_SCHEMA_VERSION, description="Version of this document's layout."
    )
    scale: Scale = Field(
        _M["scale"],
        description="Which aircraft this mission is for: the 3D-printed prototype or the "
        "full-scale carbon-fibre version.",
        json_schema_extra={
            "label": "Scale",
            "unit": None,
            "enum": [
                {"value": "prototype", "label": "Prototype (2-3 kg, 3D printed)"},
                {"value": "final", "label": "Final (up to 24 kg, carbon fibre)"},
            ],
        },
    )
    target_takeoff_mass_kg: float = Field(
        _M["target_takeoff_mass_kg"],
        gt=0,
        description="Target take-off mass including batteries and the heaviest camera. "
        "The 24 kg design limit is checked, not enforced, so any value can be saved.",
        json_schema_extra={"label": "Target take-off mass", "unit": "kg"},
    )
    target_endurance_min: float = Field(
        _M["target_endurance_min"],
        gt=0,
        description="How long the aircraft should stay airborne in wing-borne flight.",
        json_schema_extra={"label": "Target endurance", "unit": "min"},
    )
    cruise_speed_mps: float = Field(
        _M["cruise_speed_mps"],
        gt=0,
        description="Intended cruise speed in metres per second (16 m/s is about 58 km/h).",
        json_schema_extra={"label": "Cruise speed", "unit": "m/s"},
    )
    payload_min_g: float = Field(
        _M["payload_min_g"],
        ge=0,
        description="Lightest camera/payload the nose bay must carry. Balance is checked at "
        "both ends of the payload range.",
        json_schema_extra={"label": "Minimum payload", "unit": "g"},
    )
    payload_max_g: float = Field(
        _M["payload_max_g"],
        ge=0,
        description="Heaviest camera/payload the nose bay must carry.",
        json_schema_extra={"label": "Maximum payload", "unit": "g"},
    )

    @model_validator(mode="after")
    def _payload_range(self) -> Mission:
        if self.payload_max_g < self.payload_min_g:
            raise ValueError(
                "The maximum payload must be at least the minimum payload "
                f"({self.payload_max_g:g} g < {self.payload_min_g:g} g)."
            )
        return self
