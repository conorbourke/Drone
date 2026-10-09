"""Initial schema: users, projects, design_versions, parts, part_listings, app_settings.

Revision ID: 0001
Revises:
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("is_owner", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("draft_parameters", sa.JSON(), nullable=False),
        sa.Column("draft_mission", sa.JSON(), nullable=False),
        sa.Column("draft_based_on_version_id", sa.Integer(), nullable=True),
        sa.Column("draft_updated_at", sa.DateTime(), nullable=False),
        sa.Column("next_version_number", sa.Integer(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_projects_owner", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["draft_based_on_version_id"],
            ["design_versions.id"],
            name="fk_projects_draft_based_on_version",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("owner_id", "name", name="uq_projects_owner_name"),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])

    op.create_table(
        "design_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("mission", sa.JSON(), nullable=False),
        sa.Column("parent_version_id", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_versions_project", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_versions_owner", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_version_id"],
            ["design_versions.id"],
            name="fk_versions_parent",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("project_id", "number", name="uq_versions_project_number"),
        sa.UniqueConstraint("project_id", "name", name="uq_versions_project_name"),
    )
    op.create_index("ix_design_versions_project_id", "design_versions", ["project_id"])
    op.create_index("ix_design_versions_owner_id", "design_versions", ["owner_id"])

    op.create_table(
        "parts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("manufacturer", sa.String(length=200), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("mass_g", sa.Float(), nullable=False),
        sa.Column("price_eur_estimate", sa.Float(), nullable=True),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("category", "manufacturer", "model", name="uq_parts_identity"),
    )
    op.create_index("ix_parts_category", "parts", ["category"])

    op.create_table(
        "part_listings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("part_id", sa.Integer(), nullable=False),
        sa.Column("supplier_name", sa.String(length=200), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("price_eur", sa.Float(), nullable=True),
        sa.Column("in_stock", sa.Boolean(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["part_id"], ["parts.id"], name="fk_listings_part", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_part_listings_part_id", "part_listings", ["part_id"])

    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_app_settings_owner", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("owner_id", name="uq_app_settings_owner"),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_index("ix_part_listings_part_id", table_name="part_listings")
    op.drop_table("part_listings")
    op.drop_index("ix_parts_category", table_name="parts")
    op.drop_table("parts")
    op.drop_index("ix_design_versions_owner_id", table_name="design_versions")
    op.drop_index("ix_design_versions_project_id", table_name="design_versions")
    op.drop_table("design_versions")
    op.drop_index("ix_projects_owner_id", table_name="projects")
    op.drop_table("projects")
    op.drop_table("users")
