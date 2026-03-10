"""Add pipelines table and pipeline_id/stage_name to tasks.

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-09

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- New pipelines table ---
    op.create_table(
        "pipelines",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("repo", sa.String(100), nullable=True),
        sa.Column("stages_json", sa.Text(), nullable=False),
        sa.Column("current_stage_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_task_id", sa.String(12), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("max_review_iterations", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("review_iteration", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("task_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- Add pipeline link columns to tasks ---
    op.add_column("tasks", sa.Column("pipeline_id", sa.String(12), nullable=True))
    op.add_column("tasks", sa.Column("stage_name", sa.String(50), nullable=True))
    op.create_index("ix_tasks_pipeline_id", "tasks", ["pipeline_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_pipeline_id", table_name="tasks")
    op.drop_column("tasks", "stage_name")
    op.drop_column("tasks", "pipeline_id")
    op.drop_table("pipelines")
