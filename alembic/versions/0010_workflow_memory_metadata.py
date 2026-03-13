"""Add workflow memory metadata table.

Revision ID: 0010
Revises: 0009
Create Date: 2026-03-13

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "workflow_memory" in inspector.get_table_names():
        return

    op.create_table(
        "workflow_memory",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("repo", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("artifact_ref", sa.Text(), nullable=False),
        sa.Column("artifact_path", sa.Text(), nullable=True),
        sa.Column("task_id", sa.String(12), nullable=True),
        sa.Column("pipeline_id", sa.String(12), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_workflow_memory_repo", "workflow_memory", ["repo"])
    op.create_index("ix_workflow_memory_kind", "workflow_memory", ["kind"])
    op.create_index("ix_workflow_memory_created_at", "workflow_memory", ["created_at"])
    op.create_index(
        "ix_workflow_memory_repo_kind_created_at",
        "workflow_memory",
        ["repo", "kind", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_memory_repo_kind_created_at", table_name="workflow_memory")
    op.drop_index("ix_workflow_memory_created_at", table_name="workflow_memory")
    op.drop_index("ix_workflow_memory_kind", table_name="workflow_memory")
    op.drop_index("ix_workflow_memory_repo", table_name="workflow_memory")
    op.drop_table("workflow_memory")
