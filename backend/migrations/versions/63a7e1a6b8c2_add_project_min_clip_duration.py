"""add project min clip duration

Revision ID: 63a7e1a6b8c2
Revises: a2f4d81c9b73
Create Date: 2026-07-27 13:40:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "63a7e1a6b8c2"
down_revision: str | Sequence[str] | None = "a2f4d81c9b73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("min_clip_duration", sa.Float(), server_default="0.6", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("projects", "min_clip_duration")
