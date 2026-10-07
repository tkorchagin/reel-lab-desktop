"""add media filter

Revision ID: 9c2d4e7a1b60
Revises: 5f4a2c91d730
Create Date: 2026-07-26 18:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c2d4e7a1b60"
down_revision: str | Sequence[str] | None = "5f4a2c91d730"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("media_filter", sa.String(length=16), server_default="all", nullable=False),
    )
    op.execute(
        "UPDATE projects SET media_filter = "
        "CASE WHEN include_photos = 1 THEN 'all' ELSE 'video' END"
    )


def downgrade() -> None:
    op.drop_column("projects", "media_filter")
