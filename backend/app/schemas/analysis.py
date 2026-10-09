"""Request and response bodies for analyses, scale-to-weight and the validation report."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class VersionSource(_In):
    version_id: int = Field(description="A saved version of this project.")


Source = Literal["draft"] | VersionSource


class AnalysisCreate(_In):
    source: Source = Field(
        default="draft", description='"draft" or {"version_id": ...} of this project.'
    )
    kind: Literal["full"] = Field(
        default="full",
        description="Only 'full' here (analysis, then recommendations); scale-to-weight has "
        "its own endpoint.",
    )
    parts: Literal["selected", "generic"] = Field(
        default="selected",
        description="Phase 4: 'selected' analyses the design with its parts list (the owner's "
        "locked parts and the engine's picks; generic parts when the catalogue is empty); "
        "'generic' uses the statistical motors, propellers, battery and allowances.",
    )


class ScaleCreate(_In):
    target_takeoff_mass_kg: float = Field(
        gt=0.2,
        le=25.0,
        description="New take-off mass in kilograms (at most the 25 kg legal limit; the 24 kg "
        "design limit is checked, not enforced).",
    )
    source: Source = Field(default="draft")


class AnalysisListItem(BaseModel):
    id: int
    project_id: int
    version_id: int | None
    version_number: int | None = Field(
        description="The version's permanent number, when the analysis is of a version."
    )
    source: Literal["draft", "version"]
    kind: Literal["full", "scale"]
    status: Literal["queued", "running", "done", "error"]
    progress: float = Field(description="0 to 1.")
    stage: str = Field(description="Short label of what the engine is doing.")
    error: str | None
    duration_s: float | None
    inputs_hash: str
    reused_from_id: int | None = Field(
        description="Set when an identical earlier analysis was reused instead of re-running."
    )
    queue_position: int | None = Field(
        description="Jobs ahead of this one while queued (0 = next), else null."
    )
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    headline: dict[str, Any] | None = Field(
        description="A few key numbers (Quantity objects) once done, for lists."
    )
    parts: Literal["selected", "generic"] = Field(
        "generic",
        description="Whether the analysis used the selected catalogue parts ('selected') or "
        "the generic statistical ones ('generic', also when no part was selected).",
    )


class AnalysisOut(AnalysisListItem):
    target_takeoff_mass_kg: float | None = Field(description="Scale jobs only.")
    result: dict[str, Any] | None = Field(
        description="kind 'full': the AnalysisResult plus 'recommendations' (null while the "
        "sweep is still running). kind 'scale': the scale result. Present while running once "
        "the first stage is finished."
    )


class ValidationJobOut(BaseModel):
    status: Literal["idle", "queued", "running", "done", "error"]
    progress: float = 0.0
    stage: str = ""
    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    trigger: Literal["startup", "owner"] | None = None


class ValidationOut(BaseModel):
    report: dict[str, Any] | None = Field(
        description="The validation report (schema 'validation-report/1'), or null."
    )
    source: Literal["app", "snapshot"] | None = Field(
        description="'app': written by this server; 'snapshot': the committed report from the "
        "repository, shown until this server's first run finishes."
    )
    job: ValidationJobOut
