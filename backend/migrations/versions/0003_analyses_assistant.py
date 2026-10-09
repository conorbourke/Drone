"""Phase 3: analyses, assistant threads and messages.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("inputs_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("stage", sa.String(length=200), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("reused_from_id", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_analyses_owner", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_analyses_project", ondelete="CASCADE"
        ),
        # Phase 1 policy: rows that reference a version use RESTRICT, so a version with
        # analyses can never be deleted silently (the API answers 409).
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["design_versions.id"],
            name="fk_analyses_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reused_from_id"],
            ["analyses.id"],
            name="fk_analyses_reused_from",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_analyses_owner_id", "analyses", ["owner_id"])
    op.create_index("ix_analyses_project_id", "analyses", ["project_id"])
    op.create_index("ix_analyses_version_id", "analyses", ["version_id"])
    op.create_index("ix_analyses_inputs_hash", "analyses", ["inputs_hash"])

    op.create_table(
        "assistant_threads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_assistant_threads_owner", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_assistant_threads_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("project_id", name="uq_assistant_threads_project"),
    )
    op.create_index("ix_assistant_threads_owner_id", "assistant_threads", ["owner_id"])

    op.create_table(
        "assistant_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["assistant_threads.id"],
            name="fk_assistant_messages_thread",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_assistant_messages_thread_id", "assistant_messages", ["thread_id"])


def downgrade() -> None:
    op.drop_index("ix_assistant_messages_thread_id", table_name="assistant_messages")
    op.drop_table("assistant_messages")
    op.drop_index("ix_assistant_threads_owner_id", table_name="assistant_threads")
    op.drop_table("assistant_threads")
    op.drop_index("ix_analyses_inputs_hash", table_name="analyses")
    op.drop_index("ix_analyses_version_id", table_name="analyses")
    op.drop_index("ix_analyses_project_id", table_name="analyses")
    op.drop_index("ix_analyses_owner_id", table_name="analyses")
    op.drop_table("analyses")
