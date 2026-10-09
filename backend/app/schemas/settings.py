"""Settings document with its invariants, and the GET/PUT response shape."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.defaults import DEFAULT_SETTINGS as _S
from app.defaults import SETTINGS_SCHEMA_VERSION


class _Doc(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Volume(_Doc):
    x: float = Field(gt=0, description="Size along X in millimetres.")
    y: float = Field(gt=0, description="Size along Y in millimetres.")
    z: float = Field(gt=0, description="Size along Z (height) in millimetres.")


class Printer(_Doc):
    name: str = Field(_S["printer"]["name"], min_length=1, max_length=200)
    build_volume_mm: Volume = Field(
        default_factory=lambda: Volume(**_S["printer"]["build_volume_mm"])
    )
    usable_envelope_mm: Volume = Field(
        default_factory=lambda: Volume(**_S["printer"]["usable_envelope_mm"])
    )

    @model_validator(mode="after")
    def _envelope_inside_volume(self) -> Printer:
        for axis in ("x", "y", "z"):
            usable = getattr(self.usable_envelope_mm, axis)
            build = getattr(self.build_volume_mm, axis)
            if usable > build:
                raise ValueError(
                    f"The usable envelope along {axis.upper()} ({usable:g} mm) cannot be "
                    f"larger than the printer's build volume ({build:g} mm)."
                )
        return self


class Limits(_Doc):
    design_mtow_kg: float = Field(_S["limits"]["design_mtow_kg"], gt=0)
    legal_mtow_kg: float = Field(_S["limits"]["legal_mtow_kg"], gt=0)
    warn_mtow_kg: float = Field(_S["limits"]["warn_mtow_kg"], gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> Limits:
        if self.legal_mtow_kg > 25:
            raise ValueError(
                "The legal take-off mass limit cannot exceed 25 kg (EU Open category A3)."
            )
        if self.design_mtow_kg > self.legal_mtow_kg:
            raise ValueError(
                f"The design limit ({self.design_mtow_kg:g} kg) must not exceed the legal "
                f"limit ({self.legal_mtow_kg:g} kg)."
            )
        if self.warn_mtow_kg > self.design_mtow_kg:
            raise ValueError(
                f"The warning threshold ({self.warn_mtow_kg:g} kg) must not exceed the design "
                f"limit ({self.design_mtow_kg:g} kg)."
            )
        return self


class Checks(_Doc):
    hover_thrust_to_weight_min: float = Field(_S["checks"]["hover_thrust_to_weight_min"])
    static_margin_min: float = Field(_S["checks"]["static_margin_min"])
    static_margin_max: float = Field(_S["checks"]["static_margin_max"])
    cruise_to_stall_speed_ratio_min: float = Field(_S["checks"]["cruise_to_stall_speed_ratio_min"])
    battery_reserve_fraction: float = Field(_S["checks"]["battery_reserve_fraction"])
    battery_current_max_fraction_of_rating: float = Field(
        _S["checks"]["battery_current_max_fraction_of_rating"]
    )
    manoeuvre_load_factor: float = Field(_S["checks"]["manoeuvre_load_factor"])
    structural_safety_factor: float = Field(_S["checks"]["structural_safety_factor"])
    transition_thrust_margin_min: float = Field(_S["checks"]["transition_thrust_margin_min"])

    @model_validator(mode="after")
    def _sane(self) -> Checks:
        if self.hover_thrust_to_weight_min <= 1:
            raise ValueError(
                "The minimum hover thrust-to-weight ratio must be greater than 1, otherwise "
                "the aircraft cannot lift its own weight."
            )
        if not 0 < self.static_margin_min < self.static_margin_max:
            raise ValueError(
                "The static margin range must satisfy 0 < minimum < maximum "
                f"(got {self.static_margin_min:g} to {self.static_margin_max:g})."
            )
        if self.cruise_to_stall_speed_ratio_min < 1:
            raise ValueError(
                "The cruise-to-stall speed ratio must be at least 1: cruising slower than "
                "the stall speed is not possible."
            )
        if not 0 < self.battery_reserve_fraction < 1:
            raise ValueError(
                "The battery reserve fraction must be between 0 and 1 (for example 0.2 "
                "for a 20 % reserve)."
            )
        if not 0 < self.battery_current_max_fraction_of_rating < 1:
            raise ValueError(
                "The battery current fraction must be between 0 and 1 (for example 0.8 "
                "for 80 % of the pack's continuous rating)."
            )
        if not 1 <= self.manoeuvre_load_factor <= 10:
            raise ValueError(
                "The manoeuvre load factor must be between 1 and 10 g (1 g is level flight; "
                "small drones are usually designed for 3 to 4 g)."
            )
        if not 1 <= self.structural_safety_factor <= 5:
            raise ValueError(
                "The structural safety factor must be between 1 and 5 (1.5 is the usual "
                "aviation value)."
            )
        if not 1 <= self.transition_thrust_margin_min <= 5:
            raise ValueError(
                "The minimum transition thrust margin must be between 1 and 5: below 1 the "
                "motors could not hold the aircraft up during the transition."
            )
        return self


class Analysis(_Doc):
    ncrit: float = Field(_S["analysis"]["ncrit"])

    @model_validator(mode="after")
    def _sane(self) -> Analysis:
        if not 1 <= self.ncrit <= 14:
            raise ValueError(
                "The XFOIL transition parameter Ncrit must be between 1 and 14 (9 is clean "
                "air and a smooth wing)."
            )
        return self


class Units(_Doc):
    system: Literal["metric"] = Field(_S["units"]["system"])


class SettingsDocument(_Doc):
    schema_version: Literal[2] = SETTINGS_SCHEMA_VERSION
    printer: Printer = Field(default_factory=Printer)
    limits: Limits = Field(default_factory=Limits)
    checks: Checks = Field(default_factory=Checks)
    analysis: Analysis = Field(default_factory=Analysis)
    units: Units = Field(default_factory=Units)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_older(cls, data: Any) -> Any:
        """A document written for an older schema (for example by a browser tab opened
        before an update) is upgraded first; its new fields then take their defaults."""
        if isinstance(data, dict) and isinstance(data.get("schema_version"), int):
            from app.schemas.migrate import upgrade_settings

            if data["schema_version"] < SETTINGS_SCHEMA_VERSION:
                return upgrade_settings(data)
        return data


class SettingsMeta(BaseModel):
    label: str
    description: str
    source: str
    is_default: bool


class SettingsResponse(BaseModel):
    settings: SettingsDocument
    meta: dict[str, SettingsMeta]
    warnings: list[str] = Field(
        default_factory=list,
        description="Plain-language notes, for example stored values that no longer fit the "
        "current defaults and were reset.",
    )
