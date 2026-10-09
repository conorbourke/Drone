"""Request and response bodies for mould sets and the full-scale checks (Phase 7)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.analysis import Source
from app.schemas.export import ExportListItem

MouldPart = Literal["nose", "fuselage", "wing_root_fairing"]


class MouldCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    source: Source = Field(
        default="draft", description='"draft" or {"version_id": ...} of this project.'
    )
    parts: list[MouldPart] = Field(
        default_factory=lambda: ["nose", "fuselage", "wing_root_fairing"],
        min_length=1,
        max_length=3,
        description="Which carbon parts get moulds: nose (nose bay shell), fuselage (fuselage "
        "shell) and wing_root_fairing (the right fairing; the left one is its mirror image).",
    )
    min_draft_deg: float = Field(
        default=2.0,
        ge=0.5,
        le=10.0,
        description="Smallest draft angle a mould face may have before it is flagged, degrees. "
        "Faces below it are reported; the part surface is never changed.",
    )
    vent_channels: bool = Field(
        default=False, description="Add a resin/vent channel along the flange."
    )

    @field_validator("parts")
    @classmethod
    def _unique_in_order(cls, value: list[str]) -> list[str]:
        order = ["nose", "fuselage", "wing_root_fairing"]
        return [p for p in order if p in set(value)]


class MouldListItem(ExportListItem):
    mould_parts: list[str] = Field(description="The parts this mould set covers.")
    options: dict[str, Any] = Field(description="min_draft_deg and vent_channels as requested.")


class MouldOut(MouldListItem):
    manifest: dict[str, Any] | None = Field(
        description="The mould library's manifest (schema 'vtol-moulds/1') when done."
    )


class FullscaleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    source: Source = Field(
        default="draft", description='"draft" or {"version_id": ...} of this project.'
    )


class FullscaleOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_: str = Field(alias="schema", description="'fullscale-checks/1'.")
    valid: bool
    source: Literal["draft", "version"]
    version_id: int | None
    version_number: int | None
    analysis_id: int | None = Field(
        description="The full analysis that was reused (same design, mission and settings), "
        "or null when a quick analysis was run for these checks."
    )
    analysis_source: Literal["reused", "quick"]
    parts: Literal["selected", "generic"] = Field(
        description="Whether the reused analysis used the Phase 4 parts list or generic sizes."
    )
    catalogue_size: int = Field(description="Parts in the catalogue the tube checks used.")
    computed_at: datetime
    checks: list[dict[str, Any]]
