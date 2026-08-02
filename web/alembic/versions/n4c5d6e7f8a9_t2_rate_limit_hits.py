"""T2: таблица rate_limit_hits для multi-replica лимитов

Revision ID: n4c5d6e7f8a9
Revises: m3b4c5d6e7f8
Create Date: 2026-08-02 04:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "n4c5d6e7f8a9"
down_revision: str | None = "m3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_hits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bucket", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_rate_limit_hits_bucket_key_created",
        "rate_limit_hits",
        ["bucket", "key", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_rate_limit_hits_bucket_key_created", table_name="rate_limit_hits")
    op.drop_table("rate_limit_hits")
