"""Field metadata for the design and mission documents, generated from the Pydantic models."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.defaults import DEFAULTS_NOTE
from app.deps import current_user
from app.schemas.design import DesignParameters
from app.schemas.introspect import document_schema
from app.schemas.mission import Mission

router = APIRouter(prefix="/api/schema", tags=["schema"], dependencies=[Depends(current_user)])


@router.get("/design")
def design_schema() -> dict[str, dict[str, Any]]:
    return document_schema(DesignParameters)


@router.get("/mission")
def mission_schema() -> dict[str, dict[str, Any]]:
    return document_schema(Mission)


@router.get("/notes")
def schema_notes() -> dict[str, str]:
    """Plain-language notes the UI shows beside the documents (for example that a new
    project's numbers are starting values, not an analysed design)."""
    return {"defaults": DEFAULTS_NOTE}
