"""add project random seed

Revision ID: a2f4d81c9b73
Revises: f6d4b2a917c3
Create Date: 2026-07-27 00:40:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2f4d81c9b73"
down_revision: str | Sequence[str] | None = "f6d4b2a917c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("random_seed", sa.Integer(), server_default="20260725", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("projects", "random_seed")
