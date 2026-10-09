"""Request and response bodies for flight data (Phase 6)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FlightLogItem(BaseModel):
    id: int
    project_id: int
    version_id: int | None = Field(description="The version that flew; null for the draft.")
    version_number: int | None
    source: Literal["draft", "version"]
    filename: str
    size_bytes: int
    sample: bool = Field(description="The bundled simulator sample flight.")
    takeoff_mass_kg: float | None = Field(
        description="Weighed take-off mass of the flight; null uses the predicted mass."
    )
    status: Literal["queued", "running", "done", "error"]
    progress: float = Field(description="0 to 1.")
    stage: str
    error: str | None = Field(description="Plain message when status is 'error'.")
    queue_position: int | None
    firmware: str | None
    vehicle_type: str | None
    log_start_at: datetime | None = Field(description="GPS time of the log start (UTC).")
    flight_duration_s: float | None
    summary: dict[str, Any] | None = Field(description="Phases timeline and headline numbers.")
    counts: dict[str, int] | None = Field(
        description="Comparison rows inside, outside and unavailable."
    )
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class FlightLogDetail(FlightLogItem):
    result: dict[str, Any] | None = Field(
        description="process_log output without the chart series (see /series)."
    )
    comparison: dict[str, Any] | None = Field(description="Predicted against measured.")
    sample_note: str | None


class FlightLogPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    takeoff_mass_kg: float | None = Field(
        default=None, gt=0, le=30, description="Weighed take-off mass (kg); null to clear."
    )
    version_id: int | None = Field(
        default=None, description="The version that flew; null for the draft."
    )


class CalibrationApply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factors: list[str] | None = Field(
        default=None, description="Factor names to apply; omitted applies every offered one."
    )


class BuiltWeightIn(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    key: str = Field(min_length=1, max_length=60)
    measured_g: float | None = Field(
        default=None, gt=0, le=30000, description="Weighed mass (g); null clears the entry."
    )
    note: str = Field(default="", max_length=500, description="Optional note (photo, scale).")


class BuiltWeightsPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BuiltWeightIn] = Field(max_length=200)
