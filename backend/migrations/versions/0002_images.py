"""Phase 2: reference images and Claude image readings.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=50), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width_px", sa.Integer(), nullable=False),
        sa.Column("height_px", sa.Integer(), nullable=False),
        sa.Column("view", sa.String(length=20), nullable=False),
        sa.Column("storage_name", sa.String(length=64), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_images_owner", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_images_project", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("storage_name", name="uq_images_storage_name"),
    )
    op.create_index("ix_images_owner_id", "images", ["owner_id"])
    op.create_index("ix_images_project_id", "images", ["project_id"])

    op.create_table(
        "image_readings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("reference", sa.JSON(), nullable=False),
        sa.Column("image_ids", sa.JSON(), nullable=False),
        sa.Column("proposal", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_image_readings_owner", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_image_readings_project",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_image_readings_owner_id", "image_readings", ["owner_id"])
    op.create_index("ix_image_readings_project_id", "image_readings", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_image_readings_project_id", table_name="image_readings")
    op.drop_index("ix_image_readings_owner_id", table_name="image_readings")
    op.drop_table("image_readings")
    op.drop_index("ix_images_project_id", table_name="images")
    op.drop_index("ix_images_owner_id", table_name="images")
    op.drop_table("images")
