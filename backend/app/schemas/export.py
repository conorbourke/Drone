"""Request and response bodies for file exports (Phase 5)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.analysis import Source


class ExportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    source: Source = Field(
        default="draft", description='"draft" or {"version_id": ...} of this project.'
    )


class ExportListItem(BaseModel):
    id: int
    project_id: int
    version_id: int | None
    version_number: int | None
    source: Literal["draft", "version"]
    status: Literal["queued", "running", "done", "error"]
    progress: float = Field(description="0 to 1.")
    stage: str
    error: str | None = Field(description="Plain message when status is 'error'.")
    inputs_hash: str
    reused_from_id: int | None
    queue_position: int | None = Field(description="Jobs ahead while queued (0 = next).")
    parts: Literal["selected", "generic"] = Field(
        description="Whether the Phase 4 parts list was used ('selected') or generic sizes."
    )
    analysis_id: int | None = Field(
        description="The analysis whose CG, neutral point and spar sizing were used, if any."
    )
    duration_s: float | None
    peak_rss_mb: float | None = Field(description="Peak memory of the CAD child process.")
    total_size_bytes: int | None
    file_count: int | None
    summary: dict[str, Any] | None = Field(
        description="When done: parts, pieces, all_pieces_fit, watertight_all, plates, files, "
        "groups (file count per group), envelope_mm, bed_mm, bom_totals, warnings."
    )
    zip_url: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ExportOut(ExportListItem):
    manifest: dict[str, Any] | None = Field(
        description="The CAD library's manifest (schema 'vtol-files/1') when done."
    )
