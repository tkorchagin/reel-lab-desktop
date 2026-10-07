"""add project clip reuse mode

Revision ID: b1c9d7e2a403
Revises: 63a7e1a6b8c2
Create Date: 2026-07-27 14:05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1c9d7e2a403"
down_revision: str | Sequence[str] | None = "63a7e1a6b8c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "clip_reuse_mode",
            sa.String(length=16),
            server_default="multiple",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "clip_reuse_mode")
