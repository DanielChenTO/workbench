"""Add todos table for kanban board.

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-09

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Use IF NOT EXISTS for idempotency (safe to re-run)
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "todos" in inspector.get_table_names():
        return

    op.create_table(
        "todos",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="backlog"),
        sa.Column("priority", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("column_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tags", sa.Text(), nullable=True),
        # Jira integration
        sa.Column("jira_key", sa.String(50), nullable=True, unique=True),
        sa.Column("jira_url", sa.String(500), nullable=True),
        sa.Column("jira_status", sa.String(100), nullable=True),
        sa.Column("jira_last_synced", sa.DateTime(timezone=True), nullable=True),
        # Source tracking
        sa.Column("source", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("source_ref", sa.String(500), nullable=True),
        # Timestamps
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_todos_status", "todos", ["status"])
    op.create_index("ix_todos_jira_key", "todos", ["jira_key"], unique=True)
    op.create_index("ix_todos_source", "todos", ["source"])


def downgrade() -> None:
    op.drop_index("ix_todos_source", table_name="todos")
    op.drop_index("ix_todos_jira_key", table_name="todos")
    op.drop_index("ix_todos_status", table_name="todos")
    op.drop_table("todos")
