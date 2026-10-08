"""Parts catalogue: categories, parts and supplier listings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.deps import DbSession, current_user
from app.models import Part, PartListing
from app.parts_catalog import CATEGORY_KEYS, category_payloads, validate_spec
from app.routers.common import conflict, not_found
from app.schemas.parts import (
    CategoryOut,
    ListingCreate,
    ListingOut,
    PartCreate,
    PartOut,
    PartUpdate,
)

router = APIRouter(prefix="/api/parts", tags=["parts"], dependencies=[Depends(current_user)])


def _spec_errors(exc: ValidationError) -> RequestValidationError:
    """Re-raise a spec validation failure in FastAPI's 422 shape, under body.spec."""
    errors: list[Any] = []
    for err in exc.errors(include_url=False):
        err = dict(err)
        err["loc"] = ("body", "spec", *err.get("loc", ()))
        errors.append(err)
    return RequestValidationError(errors)


def _category_error(category: str) -> RequestValidationError:
    return RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": ("body", "category"),
                "msg": f"Unknown part category '{category}'. "
                f"Choose one of: {', '.join(CATEGORY_KEYS)}.",
                "input": category,
            }
        ]
    )


def _validated_spec(category: str, spec: dict[str, Any]) -> dict[str, Any]:
    if category not in CATEGORY_KEYS:
        raise _category_error(category)
    try:
        return validate_spec(category, spec)
    except ValidationError as exc:
        raise _spec_errors(exc) from None


def _identity_conflict(category: str, manufacturer: str, model: str) -> HTTPException:
    return conflict(f"A {category} part '{manufacturer} {model}' already exists.")


def _load_part(db: Session, part_id: int) -> Part:
    part = db.scalar(select(Part).where(Part.id == part_id).options(selectinload(Part.listings)))
    if part is None:
        raise not_found("Part")
    return part


@router.get("/categories", response_model=list[CategoryOut])
def list_categories() -> list[dict[str, Any]]:
    return category_payloads()


@router.get("", response_model=list[PartOut])
def list_parts(
    db: DbSession,
    category: str | None = Query(None, description="Filter by category key."),
    q: str | None = Query(None, description="Case-insensitive match on manufacturer or model."),
) -> list[Part]:
    stmt = select(Part).options(selectinload(Part.listings))
    if category:
        stmt = stmt.where(Part.category == category)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(Part.manufacturer.ilike(pattern), Part.model.ilike(pattern)))
    stmt = stmt.order_by(Part.category, Part.manufacturer, Part.model)
    return list(db.scalars(stmt).all())


@router.post("", response_model=PartOut, status_code=status.HTTP_201_CREATED)
def create_part(body: PartCreate, db: DbSession) -> Part:
    spec = _validated_spec(body.category, body.spec)
    part = Part(
        category=body.category,
        manufacturer=body.manufacturer,
        model=body.model,
        mass_g=body.mass_g,
        price_eur_estimate=body.price_eur_estimate,
        spec=spec,
        source=body.source,
        verified=body.verified,
        notes=body.notes,
    )
    for listing in body.listings:
        part.listings.append(PartListing(**listing.model_dump()))
    db.add(part)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _identity_conflict(body.category, body.manufacturer, body.model) from None
    return _load_part(db, part.id)


@router.get("/{part_id}", response_model=PartOut)
def get_part(part_id: int, db: DbSession) -> Part:
    return _load_part(db, part_id)


@router.patch("/{part_id}", response_model=PartOut)
def update_part(part_id: int, body: PartUpdate, db: DbSession) -> Part:
    part = _load_part(db, part_id)
    changes = body.model_dump(exclude_unset=True)
    category = changes.get("category", part.category)
    if "spec" in changes or category != part.category:
        changes["spec"] = _validated_spec(category, changes.get("spec", part.spec))
    for key, value in changes.items():
        setattr(part, key, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _identity_conflict(part.category, part.manufacturer, part.model) from None
    return _load_part(db, part.id)


@router.delete("/{part_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_part(part_id: int, db: DbSession) -> None:
    part = _load_part(db, part_id)
    db.delete(part)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise conflict("This part is still referenced by a design and cannot be deleted.") from None


@router.post("/{part_id}/listings", response_model=ListingOut, status_code=status.HTTP_201_CREATED)
def create_listing(part_id: int, body: ListingCreate, db: DbSession) -> PartListing:
    part = _load_part(db, part_id)
    listing = PartListing(part_id=part.id, **body.model_dump())
    db.add(listing)
    db.commit()
    db.refresh(listing)
    return listing


@router.delete("/listings/{listing_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_listing(listing_id: int, db: DbSession) -> None:
    listing = db.get(PartListing, listing_id)
    if listing is None:
        raise not_found("Listing")
    db.delete(listing)
    db.commit()
