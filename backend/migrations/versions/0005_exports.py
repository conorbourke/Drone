"""Phase 5: file exports.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-09

``exports``: one "Generate files" job per row (status, progress, the inputs snapshot and its
hash, the manifest, where the files are and how big they are). ``project_id`` cascades, and so
does ``version_id``: deliberately not the Phase 1 RESTRICT policy, because an export is
reproducible from its version and must never block deleting it. The files under
``{APP_DATA_DIR}/files/exports/{id}/`` are removed by the API together with the row. The table
uses SQLite AUTOINCREMENT so an id (the directory name) is never reused after a delete.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("inputs_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("stage", sa.String(length=200), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("manifest", sa.JSON(), nullable=True),
        sa.Column("files_dir", sa.String(length=255), nullable=True),
        sa.Column("total_size_bytes", sa.Integer(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("peak_rss_mb", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("reused_from_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_exports_owner", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_exports_project", ondelete="CASCADE"
        ),
        # Not RESTRICT: exports are reproducible and go with their version.
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["design_versions.id"],
            name="fk_exports_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reused_from_id"],
            ["exports.id"],
            name="fk_exports_reused_from",
            ondelete="SET NULL",
        ),
        # Ids are never reused: they name the files directory, and a job cancelled by a delete
        # must never touch a later export's row or files.
        sqlite_autoincrement=True,
    )
    op.create_index("ix_exports_owner_id", "exports", ["owner_id"])
    op.create_index("ix_exports_project_id", "exports", ["project_id"])
    op.create_index("ix_exports_version_id", "exports", ["version_id"])
    op.create_index("ix_exports_inputs_hash", "exports", ["inputs_hash"])


def downgrade() -> None:
    op.drop_index("ix_exports_inputs_hash", table_name="exports")
    op.drop_index("ix_exports_version_id", table_name="exports")
    op.drop_index("ix_exports_project_id", table_name="exports")
    op.drop_index("ix_exports_owner_id", table_name="exports")
    op.drop_table("exports")
