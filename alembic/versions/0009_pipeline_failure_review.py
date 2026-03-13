"""Add human review metadata to pipelines.

Revision ID: 0009
Revises: 0008
Create Date: 2026-03-12

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {c["name"] for c in inspector.get_columns("pipelines")}

    if "human_review_required" not in existing_columns:
        op.add_column(
            "pipelines",
            sa.Column(
                "human_review_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    if "failure_report" not in existing_columns:
        op.add_column(
            "pipelines",
            sa.Column("failure_report", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("pipelines", "failure_report")
    op.drop_column("pipelines", "human_review_required")
