"""Reference images and Claude image readings: request and response bodies."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ImageView = Literal["front", "side", "top", "three_quarter", "other"]
ReferenceParameter = Literal["wing.span_mm", "fuselage.length_mm"]
ReadingStatus = Literal["ok", "refused", "error"]

IMAGE_VIEW_OPTIONS = [
    {"value": "front", "label": "Front"},
    {"value": "side", "label": "Side"},
    {"value": "top", "label": "Top"},
    {"value": "three_quarter", "label": "Three-quarter"},
    {"value": "other", "label": "Other"},
]


class ImageOut(BaseModel):
    id: int
    filename: str = Field(description="Original file name, sanitised.")
    view: ImageView = Field(description="Which view of the aircraft the image shows.")
    width_px: int
    height_px: int
    size_bytes: int
    url: str = Field(description="Where the browser loads the file: /api/images/{id}/file.")
    created_at: datetime


class ImageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view: ImageView


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    parameter: ReferenceParameter = Field(
        description="Which real dimension you know: the wingspan or the fuselage length."
    )
    value_mm: float = Field(gt=0, le=30_000, description="Its real size in millimetres.")


class ReadingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: Reference
    image_ids: list[int] | None = Field(
        None,
        description="Images to read (1 to 4). Default: every image in the project.",
    )


class ReadingOut(BaseModel):
    id: int
    project_id: int
    model: str
    reference: dict[str, Any]
    image_ids: list[int]
    status: ReadingStatus
    proposal: dict[str, Any] | None = Field(
        description="{layout, layout_confidence, layout_reason, parameters: {dotted.path: "
        "{value, unit, confidence, note}}, unmapped_notes, warnings}; null unless status is ok."
    )
    error: str | None = Field(description="Plain message when refused or failed.")
    usage: dict[str, Any] | None = Field(description="Input and output tokens.")
    created_at: datetime
