"""Add stage_status_json column to pipelines for parallel stage execution.

Revision ID: 0008
Revises: 0007
Create Date: 2026-03-10

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {c["name"] for c in inspector.get_columns("pipelines")}

    if "stage_status_json" not in existing_columns:
        op.add_column(
            "pipelines",
            sa.Column("stage_status_json", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("pipelines", "stage_status_json")
