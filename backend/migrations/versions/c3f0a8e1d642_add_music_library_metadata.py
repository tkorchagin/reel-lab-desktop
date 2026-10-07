"""add music library metadata

Revision ID: c3f0a8e1d642
Revises: 9c2d4e7a1b60
Create Date: 2026-07-26 19:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3f0a8e1d642"
down_revision: str | Sequence[str] | None = "9c2d4e7a1b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "music_tracks",
        sa.Column("metadata_json", sa.JSON(), server_default="{}", nullable=False),
    )
    op.add_column(
        "music_tracks",
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("music_tracks", "sort_order")
    op.drop_column("music_tracks", "metadata_json")
