"""Phase 4: part selections, supplier-lookup columns, and the real parts seed.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-09

Schema:

* ``part_selections``: the part chosen per role for a project's draft (``version_id`` null) or a
  version. ``version_id`` and ``part_id`` are ``ON DELETE CASCADE`` (a selection is derived
  design data that goes with its version, project or part; it must never block a delete).
* ``parts``: ``listings_refreshed_at`` (one Claude refresh per part per hour),
  ``listings_refresh_status``, ``listings_refresh_message``.
* ``part_listings``: ``url_ok``, ``url_status``, ``url_checked_at`` (server-side link check).

Data (chosen over a startup hook because it must run exactly once: an owner who later deletes
every part does not get the seed back on the next restart):

1. The Phase 1 example placeholder parts (``seed/parts.example.json``) are deleted when they are
   still exactly as loaded (same identity, source, mass, price, spec, notes and listing URLs).
2. If the parts table is then empty, ``seed/parts.json`` (53 real parts with 69 Irish and UK
   listings, all ``verified: false``) is inserted. A table that already holds parts is left
   alone, so the owner's catalogue is never overwritten.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

SEED_DIR = Path(__file__).resolve().parents[2] / "seed"
SEED_FILE = SEED_DIR / "parts.json"
EXAMPLE_FILE = SEED_DIR / "parts.example.json"

_parts = sa.table(
    "parts",
    sa.column("id", sa.Integer),
    sa.column("category", sa.String),
    sa.column("manufacturer", sa.String),
    sa.column("model", sa.String),
    sa.column("mass_g", sa.Float),
    sa.column("price_eur_estimate", sa.Float),
    sa.column("spec", sa.JSON),
    sa.column("source", sa.Text),
    sa.column("verified", sa.Boolean),
    sa.column("notes", sa.Text),
    sa.column("created_at", sa.DateTime),
    sa.column("updated_at", sa.DateTime),
)
_listings = sa.table(
    "part_listings",
    sa.column("id", sa.Integer),
    sa.column("part_id", sa.Integer),
    sa.column("supplier_name", sa.String),
    sa.column("country", sa.String),
    sa.column("url", sa.Text),
    sa.column("price_eur", sa.Float),
    sa.column("in_stock", sa.Boolean),
    sa.column("last_checked_at", sa.DateTime),
    sa.column("created_at", sa.DateTime),
    sa.column("updated_at", sa.DateTime),
)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def _naive_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _load(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _same(a: Any, b: Any) -> bool:
    """Equal JSON values, with 400 == 400.0 (stored specs are validated model dumps)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return float(a) == float(b)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def _remove_untouched_examples(conn: sa.Connection) -> int:
    removed = 0
    for entry in _load(EXAMPLE_FILE):
        row = conn.execute(
            sa.select(_parts).where(
                _parts.c.category == entry["category"],
                _parts.c.manufacturer == entry["manufacturer"],
                _parts.c.model == entry["model"],
            )
        ).first()
        if row is None:
            continue
        m = row._mapping
        urls = sorted(
            r[0]
            for r in conn.execute(
                sa.select(_listings.c.url).where(_listings.c.part_id == m["id"])
            ).all()
        )
        untouched = (
            m["source"] == entry.get("source", "")
            and float(m["mass_g"]) == float(entry["mass_g"])
            and m["price_eur_estimate"] == entry.get("price_eur_estimate")
            and m["notes"] == entry.get("notes", "")
            and not m["verified"]
            and _spec_matches(m["spec"], entry["spec"])
            and urls == sorted(li["url"] for li in entry.get("listings", []))
        )
        if untouched:
            conn.execute(sa.delete(_listings).where(_listings.c.part_id == m["id"]))
            conn.execute(sa.delete(_parts).where(_parts.c.id == m["id"]))
            removed += 1
    return removed


def _spec_matches(stored: Any, seeded: dict[str, Any]) -> bool:
    """Specs are stored normalised (validated model dump, optional fields filled with their
    defaults), so compare the keys the seed entry gives and require every other stored key to
    be empty."""
    if isinstance(stored, str):
        stored = json.loads(stored)
    if not isinstance(stored, dict):
        return False
    for key, value in stored.items():
        if key in seeded:
            if not _same(value, seeded[key]):
                return False
        elif value not in (None, [], ""):
            return False
    return all(key in stored for key in seeded)


def _seed_if_empty(conn: sa.Connection) -> int:
    count = conn.execute(sa.select(sa.func.count()).select_from(_parts)).scalar_one()
    if count:
        return 0
    now = datetime.now(UTC).replace(tzinfo=None)
    inserted = 0
    for entry in _load(SEED_FILE):
        result = conn.execute(
            sa.insert(_parts).values(
                category=entry["category"],
                manufacturer=entry["manufacturer"].strip(),
                model=entry["model"].strip(),
                mass_g=float(entry["mass_g"]),
                price_eur_estimate=entry.get("price_eur_estimate"),
                spec=entry["spec"],
                source=entry.get("source", ""),
                verified=bool(entry.get("verified", False)),
                notes=entry.get("notes", ""),
                created_at=now,
                updated_at=now,
            )
        )
        part_id = result.lastrowid
        for listing in entry.get("listings", []):
            conn.execute(
                sa.insert(_listings).values(
                    part_id=part_id,
                    supplier_name=listing["supplier_name"].strip(),
                    country=listing["country"],
                    url=listing["url"].strip(),
                    price_eur=listing.get("price_eur"),
                    in_stock=listing.get("in_stock"),
                    last_checked_at=_naive_utc(listing.get("last_checked_at")),
                    created_at=now,
                    updated_at=now,
                )
            )
        inserted += 1
    return inserted


def upgrade() -> None:
    op.create_table(
        "part_selections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("part_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_part_selections_owner", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_part_selections_project",
            ondelete="CASCADE",
        ),
        # Not the Phase 1 RESTRICT policy: selections are derived data that go with their
        # version; they must never block deleting it.
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["design_versions.id"],
            name="fk_part_selections_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["part_id"], ["parts.id"], name="fk_part_selections_part", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_part_selections_owner_id", "part_selections", ["owner_id"])
    op.create_index("ix_part_selections_project_id", "part_selections", ["project_id"])
    op.create_index("ix_part_selections_version_id", "part_selections", ["version_id"])
    op.create_index("ix_part_selections_part_id", "part_selections", ["part_id"])
    op.create_index(
        "uq_part_selections_version_role",
        "part_selections",
        ["version_id", "role"],
        unique=True,
        sqlite_where=sa.text("version_id IS NOT NULL"),
    )
    op.create_index(
        "uq_part_selections_draft_role",
        "part_selections",
        ["project_id", "role"],
        unique=True,
        sqlite_where=sa.text("version_id IS NULL"),
    )

    with op.batch_alter_table("parts") as batch:
        batch.add_column(sa.Column("listings_refreshed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("listings_refresh_status", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("listings_refresh_message", sa.Text(), nullable=True))
    with op.batch_alter_table("part_listings") as batch:
        batch.add_column(sa.Column("url_ok", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("url_status", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("url_checked_at", sa.DateTime(), nullable=True))

    conn = op.get_bind()
    _remove_untouched_examples(conn)
    _seed_if_empty(conn)


def downgrade() -> None:
    with op.batch_alter_table("part_listings") as batch:
        batch.drop_column("url_checked_at")
        batch.drop_column("url_status")
        batch.drop_column("url_ok")
    with op.batch_alter_table("parts") as batch:
        batch.drop_column("listings_refresh_message")
        batch.drop_column("listings_refresh_status")
        batch.drop_column("listings_refreshed_at")
    op.drop_index("uq_part_selections_draft_role", table_name="part_selections")
    op.drop_index("uq_part_selections_version_role", table_name="part_selections")
    op.drop_index("ix_part_selections_part_id", table_name="part_selections")
    op.drop_index("ix_part_selections_version_id", table_name="part_selections")
    op.drop_index("ix_part_selections_project_id", table_name="part_selections")
    op.drop_index("ix_part_selections_owner_id", table_name="part_selections")
    op.drop_table("part_selections")
    # The seeded parts stay: they are ordinary catalogue rows the owner may have edited.
