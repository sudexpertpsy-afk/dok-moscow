"""W-17: поля мониторинга НПА и diff редакции

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-01 19:30:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("legal_acts", sa.Column("ips_nd", sa.String(length=32), nullable=True))
    op.add_column(
        "legal_acts",
        sa.Column("watch_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("legal_acts", sa.Column("watch_name", sa.String(length=255), nullable=True))
    op.add_column("act_versions", sa.Column("diff_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("act_versions", "diff_text")
    op.drop_column("legal_acts", "watch_name")
    op.drop_column("legal_acts", "watch_enabled")
    op.drop_column("legal_acts", "ips_nd")
