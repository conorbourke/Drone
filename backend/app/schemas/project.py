"""Project and draft request/response bodies."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.design import DesignParameters
from app.schemas.mission import Mission


def _clean_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("A name is required.")
    return value


class ProjectCreate(BaseModel):
    name: str = Field(max_length=200, description="Project name, unique per owner.")
    description: str = Field("", max_length=5000, description="Free-text description.")

    _clean = field_validator("name")(_clean_name)


class ProjectUpdate(BaseModel):
    name: str | None = Field(None, max_length=200, description="New project name.")
    description: str | None = Field(None, max_length=5000, description="New description.")

    _clean = field_validator("name")(lambda v: None if v is None else _clean_name(v))


class LatestVersion(BaseModel):
    id: int
    number: int = Field(description="Permanent per-project version label.")
    name: str


class ProjectListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    version_count: int = Field(description="Number of saved versions.")
    latest_version: LatestVersion | None = Field(
        description="The highest-numbered saved version, or null when none exists."
    )
    next_version_number: int = Field(
        description="The number the next saved version will get (never reused after a delete)."
    )


class DraftIn(BaseModel):
    parameters: DesignParameters = Field(description="Design parameters document.")
    mission: Mission = Field(description="Mission document.")


class DraftOut(BaseModel):
    parameters: dict[str, Any] = Field(description="Design parameters at the current schema.")
    mission: dict[str, Any] = Field(description="Mission at the current schema.")
    based_on_version_id: int | None = Field(
        description="Version the draft was last restored from or saved as; null if none."
    )
    updated_at: datetime = Field(description="When the draft was last written (UTC).")


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    draft: DraftOut
    version_count: int
    next_version_number: int = Field(
        description="The number the next saved version will get (never reused after a delete)."
    )
