"""Phase 7: mould sets share the exports table.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-09

A mould set ("Generate moulds": the nose, fuselage and wing-root fairing moulds of a draft or
version) is the same kind of job as a Phase 5 file export: an inputs snapshot and its hash, a
child process with the time and memory guards, a manifest, files under
``{APP_DATA_DIR}/files/exports/{id}/`` removed with the row, its version or its project. So it
is a row of ``exports`` with ``kind = "moulds"`` instead of a second table; existing rows are
``kind = "files"``. Downgrading deletes the mould rows (their directories have no row then and
are removed by the startup sweep).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A plain ALTER TABLE ADD COLUMN: the table keeps its AUTOINCREMENT (ids name directories).
    op.add_column(
        "exports",
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="files"),
    )
    op.create_index("ix_exports_kind", "exports", ["kind"])


def downgrade() -> None:
    op.execute(
        "UPDATE exports SET reused_from_id = NULL WHERE reused_from_id IN "
        "(SELECT id FROM exports WHERE kind = 'moulds')"
    )
    op.execute("DELETE FROM exports WHERE kind = 'moulds'")
    op.drop_index("ix_exports_kind", table_name="exports")
    # SQLite may rebuild the table to drop a column: keep AUTOINCREMENT on the copy.
    with op.batch_alter_table("exports", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.drop_column("kind")
