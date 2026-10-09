"""Airfoil library: section shapes with their computed geometry and XFOIL polars."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.deps import current_user
from app.engine.airfoils import LIBRARY, LIBRARY_BY_ID, airfoil_detail, airfoil_summary
from app.routers.common import not_found

router = APIRouter(prefix="/api/airfoils", tags=["airfoils"], dependencies=[Depends(current_user)])


@router.get("")
def list_airfoils() -> list[dict[str, Any]]:
    """Every library section with its geometry and the per-Reynolds-number polar summary."""
    return [airfoil_summary(entry) for entry in LIBRARY]


@router.get("/{airfoil_id}")
def get_airfoil(airfoil_id: str) -> dict[str, Any]:
    """One section: the summary plus unit-chord coordinates (Selig order) and full polars."""
    entry = LIBRARY_BY_ID.get(airfoil_id)
    if entry is None:
        raise not_found("Airfoil")
    return airfoil_detail(entry)
