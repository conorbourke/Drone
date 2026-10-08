"""Idempotent parts seed loader.

    python -m app.parts_catalog.load seed/parts.example.json

Upserts every entry by ``(category, manufacturer, model)``; listings are upserted by
``(part, supplier_name, url)``. Running it twice changes nothing. The app never seeds on its
own in production.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.models import Part, PartListing
from app.parts_catalog.categories import validate_spec
from app.schemas.parts import ListingCreate, PartCreate


def upsert_part(db: Session, entry: dict[str, Any]) -> tuple[Part, bool]:
    """Insert or update one part from a seed entry. Returns (part, created)."""
    data = PartCreate.model_validate(entry)
    spec = validate_spec(data.category, data.spec)
    part = db.scalar(
        select(Part).where(
            Part.category == data.category,
            Part.manufacturer == data.manufacturer,
            Part.model == data.model,
        )
    )
    created = part is None
    if part is None:
        part = Part(category=data.category, manufacturer=data.manufacturer, model=data.model)
        db.add(part)
    part.mass_g = data.mass_g
    part.price_eur_estimate = data.price_eur_estimate
    part.spec = spec
    part.source = data.source
    part.verified = data.verified
    part.notes = data.notes
    db.flush()
    for listing in data.listings:
        upsert_listing(db, part, listing)
    return part, created


def upsert_listing(db: Session, part: Part, listing: ListingCreate) -> PartListing:
    existing = db.scalar(
        select(PartListing).where(
            PartListing.part_id == part.id,
            PartListing.supplier_name == listing.supplier_name,
            PartListing.url == listing.url,
        )
    )
    if existing is None:
        existing = PartListing(
            part_id=part.id, supplier_name=listing.supplier_name, url=listing.url
        )
        db.add(existing)
    existing.country = listing.country
    existing.price_eur = listing.price_eur
    existing.in_stock = listing.in_stock
    existing.last_checked_at = _aware(listing.last_checked_at)
    db.flush()
    return existing


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def load_file(db: Session, path: Path) -> tuple[int, int]:
    """Load a seed file. Returns (created, updated)."""
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("The seed file must contain a JSON list of parts.")
    created = updated = 0
    for entry in entries:
        _, was_created = upsert_part(db, entry)
        if was_created:
            created += 1
        else:
            updated += 1
    db.commit()
    return created, updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load parts from a JSON seed file (idempotent).")
    parser.add_argument("path", type=Path, help="Seed file, e.g. seed/parts.example.json")
    parser.add_argument(
        "--database-url", default=None, help="SQLAlchemy URL (default: from the environment)."
    )
    args = parser.parse_args(argv)
    url = args.database_url or get_settings().resolved_database_url
    engine = make_engine(url)
    factory = make_session_factory(engine)
    with factory() as db:
        created, updated = load_file(db, args.path)
    print(f"Loaded {args.path}: {created} created, {updated} updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
