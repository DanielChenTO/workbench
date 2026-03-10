"""Add role and timeout columns to tasks table for orchestrator support.

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-09

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add role column: 'worker' (default) or 'orchestrator'
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {c["name"] for c in inspector.get_columns("tasks")}

    if "role" not in existing_columns:
        op.add_column(
            "tasks",
            sa.Column("role", sa.String(20), nullable=False, server_default="worker"),
        )
        op.create_index("ix_tasks_role", "tasks", ["role"])

    # Add per-task timeout override (nullable — uses config default when NULL)
    if "timeout" not in existing_columns:
        op.add_column(
            "tasks",
            sa.Column("timeout", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    op.drop_index("ix_tasks_role", table_name="tasks")
    op.drop_column("tasks", "timeout")
    op.drop_column("tasks", "role")
