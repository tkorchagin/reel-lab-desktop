"""add media order

Revision ID: 5f4a2c91d730
Revises: 17aaf478e320
Create Date: 2026-07-26 17:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5f4a2c91d730"
down_revision: str | Sequence[str] | None = "17aaf478e320"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("media_order", sa.String(length=20), server_default="manual", nullable=False),
    )
    op.execute(
        "UPDATE projects SET media_order = "
        "CASE WHEN preserve_order = 1 THEN 'manual' ELSE 'random' END"
    )


def downgrade() -> None:
    op.drop_column("projects", "media_order")
