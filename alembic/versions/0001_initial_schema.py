"""Initial schema — tasks table with all columns including context pipeline.

Revision ID: 0001
Revises:
Create Date: 2025-06-28 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        # Primary key
        sa.Column("id", sa.String(12), primary_key=True),
        # --- Input fields ---
        sa.Column("input_type", sa.String(20), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default=""),
        sa.Column("repo", sa.String(100), nullable=True),
        sa.Column("autonomy", sa.String(20), nullable=False, server_default="full"),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("extra_instructions", sa.Text(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("file_content", sa.Text(), nullable=True),
        sa.Column("file_format", sa.String(10), nullable=True),
        # --- State ---
        sa.Column("status", sa.String(20), nullable=False, server_default="queued", index=True),
        sa.Column("phase", sa.String(50), nullable=True),
        sa.Column("branch", sa.String(200), nullable=True),
        sa.Column("pr_url", sa.Text(), nullable=True),
        sa.Column("resolved_prompt", sa.Text(), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        # --- Timestamps ---
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        # --- FSM / supervision ---
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("unblock_response", sa.Text(), nullable=True),
        # --- Context pipeline ---
        sa.Column("context_json", sa.Text(), nullable=True),
        sa.Column("parent_task_id", sa.String(12), nullable=True, index=True),
        sa.Column("summary", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("tasks")
