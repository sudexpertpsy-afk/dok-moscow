"""Support tickets numbering columns + org onboarding (W-49 B)

Revision ID: b49numbering01
Revises: a49support01
Create Date: 2026-09-09 18:40:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b49numbering01"
down_revision: str | None = "a49support01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column(
        "counters",
        sa.Column("number_template", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "counters",
        sa.Column(
            "reset_yearly",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "counters",
        sa.Column("cycle_year", sa.Integer(), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("onboarding", JsonType, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "onboarding")
    op.drop_column("counters", "cycle_year")
    op.drop_column("counters", "reset_yearly")
    op.drop_column("counters", "number_template")
