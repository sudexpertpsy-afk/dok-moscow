"""W-34: журнал действий ops-agent из админки

Revision ID: o5d6e7f8a9b0
Revises: n4c5d6e7f8a9
Create Date: 2026-08-02 06:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "o5d6e7f8a9b0"
down_revision: str | None = "n4c5d6e7f8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "ops_journal",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("details", JsonType, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index("ix_ops_journal_created", "ops_journal", ["created_at"])
    op.create_index("ix_ops_journal_action", "ops_journal", ["action"])
    op.create_index("ix_ops_journal_user_id", "ops_journal", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_ops_journal_user_id", table_name="ops_journal")
    op.drop_index("ix_ops_journal_action", table_name="ops_journal")
    op.drop_index("ix_ops_journal_created", table_name="ops_journal")
    op.drop_table("ops_journal")
