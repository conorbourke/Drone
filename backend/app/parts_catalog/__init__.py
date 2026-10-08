"""Parts catalogue: category spec models, their field metadata and the seed loader."""

from __future__ import annotations

from app.parts_catalog.categories import (
    CATEGORIES,
    CATEGORY_KEYS,
    CategoryDef,
    category_payloads,
    spec_model_for,
    validate_spec,
)

__all__ = [
    "CATEGORIES",
    "CATEGORY_KEYS",
    "CategoryDef",
    "category_payloads",
    "spec_model_for",
    "validate_spec",
]
