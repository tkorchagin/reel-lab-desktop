"""add browser sessions

Revision ID: f6d4b2a917c3
Revises: e8b17c4a2d90
Create Date: 2026-07-26 23:58:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6d4b2a917c3"
down_revision: str | Sequence[str] | None = "e8b17c4a2d90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_sessions",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.execute(
        sa.text(
            "INSERT INTO user_sessions (token_hash, user_id, created_at) "
            "SELECT token_hash, id, created_at FROM users"
        )
    )
    op.create_table(
        "session_transfers",
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("next_path", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("code_hash"),
    )
    op.create_index("ix_session_transfers_user_id", "session_transfers", ["user_id"])
    op.create_index("ix_session_transfers_expires_at", "session_transfers", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_session_transfers_expires_at", table_name="session_transfers")
    op.drop_index("ix_session_transfers_user_id", table_name="session_transfers")
    op.drop_table("session_transfers")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")
