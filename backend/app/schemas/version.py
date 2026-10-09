"""Design version request/response bodies."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.design import DesignParameters
from app.schemas.mission import Mission

VERSION_NAME_MAX_LENGTH = 200


def _clean_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("A name is required.")
    return value


class VersionCreate(BaseModel):
    name: str = Field(
        max_length=VERSION_NAME_MAX_LENGTH,
        description="Version name, unique within the project.",
    )
    notes: str = Field("", max_length=10000, description="Free-text notes.")
    parameters: DesignParameters | None = Field(
        None,
        description="Explicit design parameters. When omitted the project draft is snapshotted.",
    )
    mission: Mission | None = Field(
        None, description="Explicit mission. When omitted the project draft is snapshotted."
    )

    _clean = field_validator("name")(_clean_name)

    @model_validator(mode="after")
    def _both_or_neither(self) -> VersionCreate:
        if (self.parameters is None) != (self.mission is None):
            raise ValueError(
                "Supply both 'parameters' and 'mission' to save an explicit document, "
                "or neither to snapshot the current draft."
            )
        return self

    @property
    def is_explicit(self) -> bool:
        return self.parameters is not None


class PatchBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: int = Field(description="A saved version of the same project.")


class VersionFromPatch(BaseModel):
    """ "Try as new version": a base document plus a parameter patch, saved as a new version."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: str = Field(max_length=VERSION_NAME_MAX_LENGTH)
    notes: str = Field("", max_length=10000)
    base: Literal["draft"] | PatchBase = Field(
        "draft", description='"draft" or {"version_id": ...}: the document the patch applies to.'
    )
    patch: dict[str, Any] = Field(
        description="{dotted.path: value}; paths are design parameters (wing.span_mm) or, with "
        "a 'mission.' prefix, mission fields."
    )

    _clean = field_validator("name")(_clean_name)


class VersionUpdate(BaseModel):
    name: str | None = Field(None, max_length=VERSION_NAME_MAX_LENGTH)
    notes: str | None = Field(None, max_length=10000)

    _clean = field_validator("name")(lambda v: None if v is None else _clean_name(v))


class DuplicateRequest(BaseModel):
    name: str | None = Field(
        None,
        max_length=VERSION_NAME_MAX_LENGTH,
        description="Name for the copy. Defaults to '<name> (copy)', de-duplicated.",
    )

    _clean = field_validator("name")(lambda v: None if v is None else _clean_name(v))


class VersionListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: int = Field(description="Permanent per-project label; never reused after a delete.")
    name: str
    notes: str
    parent_version_id: int | None = Field(description="Version this one was derived from.")
    created_at: datetime


class VersionOut(BaseModel):
    id: int
    project_id: int
    number: int
    name: str
    notes: str
    parameters: dict[str, Any] = Field(description="Design parameters at the current schema.")
    mission: dict[str, Any] = Field(description="Mission at the current schema.")
    parent_version_id: int | None
    created_at: datetime
    parts_selection: dict[str, Any] | None = Field(
        None,
        description="Phase 4: the stored parts selection of this version (per role: part, "
        "quantity, locked, masses; plus 'masses_g' by Tier 1 mass component), or null.",
    )
