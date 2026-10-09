"""Parts catalogue: categories, parts and supplier listings."""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import suppliers
from app.db import utcnow
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


@router.post("/{part_id}/refresh-listings")
def refresh_listings(
    part_id: int,
    request: Request,
    db: DbSession,
    wait: bool = Query(
        True,
        description="true: search now and answer when done (up to a few minutes); false: queue "
        "it on the worker and answer 202 at once (poll GET /api/parts/{id}).",
    ),
) -> Any:
    """Phase 4: Claude with web search looks up current Irish and UK listings for this part;
    every link is checked before it is stored as working. One refresh per part per hour."""
    settings = request.app.state.settings
    if not suppliers.is_available(settings):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": suppliers.MISSING_KEY_MESSAGE},
        )
    part = _load_part(db, part_id)
    until = suppliers.rate_limited_until(part)
    if until is not None:
        wait_s = max(1, int((until - utcnow()).total_seconds()))
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(wait_s)},
            content={
                "detail": "This part's listings were refreshed less than an hour ago. Try "
                f"again after {until:%H:%M} UTC.",
                "retry_after_s": wait_s,
            },
        )
    if not wait:
        worker = getattr(request.app.state, "analysis_worker", None)
        if worker is None or not worker.running:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "The worker is starting up. Try again in a moment."},
            )
        suppliers.mark_queued(db, part)
        db.commit()
        with contextlib.suppress(RuntimeError):
            worker.submit_refresh(part.id)
        body = PartOut.model_validate(_load_part(db, part_id)).model_dump(mode="json")
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"part": body})
    db.close()  # the refresh uses its own sessions; do not hold this one for minutes
    try:
        summary = suppliers.refresh_part(request.app.state.session_factory, settings, part_id)
    except suppliers.SupplierError as exc:
        return JSONResponse(status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": str(exc)})
    with request.app.state.session_factory() as fresh:
        body = PartOut.model_validate(_load_part(fresh, part_id)).model_dump(mode="json")
    return {"part": body, "refresh": summary}
