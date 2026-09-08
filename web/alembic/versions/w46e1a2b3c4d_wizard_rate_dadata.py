"""W-46E: wizard_sessions, rate_counters, dadata_cache

Revision ID: w46e1a2b3c4d
Revises: v2k3l4m5n6o7
Create Date: 2026-09-07 22:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "w46e1a2b3c4d"
down_revision: str | None = "v2k3l4m5n6o7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "wizard_sessions",
        sa.Column("sid", sa.String(length=64), primary_key=True),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("state", JsonType, nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_wizard_sessions_expires", "wizard_sessions", ["expires_at"])
    op.create_index("ix_wizard_sessions_org_id", "wizard_sessions", ["org_id"])
    op.create_index("ix_wizard_sessions_user_id", "wizard_sessions", ["user_id"])

    op.create_table(
        "rate_counters",
        sa.Column("key", sa.String(length=191), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "dadata_cache",
        sa.Column("cache_key", sa.String(length=512), primary_key=True),
        sa.Column("payload", JsonType, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_dadata_cache_expires", "dadata_cache", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_dadata_cache_expires", table_name="dadata_cache")
    op.drop_table("dadata_cache")
    op.drop_table("rate_counters")
    op.drop_index("ix_wizard_sessions_user_id", table_name="wizard_sessions")
    op.drop_index("ix_wizard_sessions_org_id", table_name="wizard_sessions")
    op.drop_index("ix_wizard_sessions_expires", table_name="wizard_sessions")
    op.drop_table("wizard_sessions")
