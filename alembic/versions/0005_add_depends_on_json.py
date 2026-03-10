"""Add depends_on_json column to tasks and pipelines tables.

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-09

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("depends_on_json", sa.Text(), nullable=True))
    op.add_column("pipelines", sa.Column("depends_on_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("pipelines", "depends_on_json")
    op.drop_column("tasks", "depends_on_json")
