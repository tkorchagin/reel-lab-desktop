"""add project music start

Revision ID: e8b17c4a2d90
Revises: c3f0a8e1d642
Create Date: 2026-07-26 20:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8b17c4a2d90"
down_revision: str | Sequence[str] | None = "c3f0a8e1d642"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("music_start", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "music_start")
