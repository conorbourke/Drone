"""Phase 6: flight data (logs, calibration factors, built weights).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-09

``flight_logs``: one uploaded ArduPilot log per row (file name and size, where the file is
under ``{APP_DATA_DIR}/files/logs/``, firmware, vehicle, start time, flight duration, the
worker's status and progress, the processed summary, the full result with the chart series,
and the comparison with the design's analysis). ``project_id`` cascades; ``version_id`` (the
version that flew; null for the draft) is ``ON DELETE RESTRICT``, the Phase 1 policy, so a
version with flight logs cannot be deleted silently. AUTOINCREMENT: ids are never reused.

``calibrations``: one applied factor per project and name (value, uncertainty, number of logs,
source log ids); ``version_id`` RESTRICT as above.

``built_weights``: the owner's weighed component masses per project, with the prediction at
the time of entry.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _owner_project(table: str) -> list[sa.SchemaItem]:
    return [
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=f"fk_{table}_owner", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=f"fk_{table}_project", ondelete="CASCADE"
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "flight_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_name", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sample", sa.Boolean(), nullable=False),
        sa.Column("takeoff_mass_kg", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("stage", sa.String(length=200), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("firmware", sa.String(length=200), nullable=True),
        sa.Column("vehicle_type", sa.String(length=100), nullable=True),
        sa.Column("log_start_at", sa.DateTime(), nullable=True),
        sa.Column("flight_duration_s", sa.Float(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("comparison", sa.JSON(), nullable=True),
        sa.Column("analysis_id", sa.Integer(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        *_owner_project("flight_logs"),
        # Phase 1 policy: measurements never disappear with a version.
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["design_versions.id"],
            name="fk_flight_logs_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"], ["analyses.id"], name="fk_flight_logs_analysis", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("storage_name", name="uq_flight_logs_storage_name"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_flight_logs_owner_id", "flight_logs", ["owner_id"])
    op.create_index("ix_flight_logs_project_id", "flight_logs", ["project_id"])
    op.create_index("ix_flight_logs_version_id", "flight_logs", ["version_id"])

    op.create_table(
        "calibrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("uncertainty", sa.Float(), nullable=False),
        sa.Column("n_logs", sa.Integer(), nullable=False),
        sa.Column("source_log_ids", sa.JSON(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        *_owner_project("calibrations"),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["design_versions.id"],
            name="fk_calibrations_version",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("project_id", "name", name="uq_calibrations_project_name"),
    )
    op.create_index("ix_calibrations_owner_id", "calibrations", ["owner_id"])
    op.create_index("ix_calibrations_project_id", "calibrations", ["project_id"])
    op.create_index("ix_calibrations_version_id", "calibrations", ["version_id"])

    op.create_table(
        "built_weights",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=60), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("group", sa.String(length=40), nullable=False),
        sa.Column("subgroup", sa.String(length=40), nullable=False),
        sa.Column("predicted_g", sa.Float(), nullable=False),
        sa.Column("measured_g", sa.Float(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        *_owner_project("built_weights"),
        sa.UniqueConstraint("project_id", "key", name="uq_built_weights_project_key"),
    )
    op.create_index("ix_built_weights_owner_id", "built_weights", ["owner_id"])
    op.create_index("ix_built_weights_project_id", "built_weights", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_built_weights_project_id", table_name="built_weights")
    op.drop_index("ix_built_weights_owner_id", table_name="built_weights")
    op.drop_table("built_weights")
    op.drop_index("ix_calibrations_version_id", table_name="calibrations")
    op.drop_index("ix_calibrations_project_id", table_name="calibrations")
    op.drop_index("ix_calibrations_owner_id", table_name="calibrations")
    op.drop_table("calibrations")
    op.drop_index("ix_flight_logs_version_id", table_name="flight_logs")
    op.drop_index("ix_flight_logs_project_id", table_name="flight_logs")
    op.drop_index("ix_flight_logs_owner_id", table_name="flight_logs")
    op.drop_table("flight_logs")
