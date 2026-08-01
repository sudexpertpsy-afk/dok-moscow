"""W-24: двухфакторная аутентификация (TOTP)

Revision ID: i9d0e1f2a3b4
Revises: g7b8c9d0e1f2
Create Date: 2026-08-01 22:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "i9d0e1f2a3b4"
down_revision: str | None = "g7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_secret_encrypted", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "totp_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "users",
        sa.Column("backup_codes_hashes", JsonType, nullable=True),
    )
    op.add_column(
        "payment_settings",
        sa.Column(
            "require_2fa_for_org_admins",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("payment_settings", "require_2fa_for_org_admins")
    op.drop_column("users", "backup_codes_hashes")
    op.drop_column("users", "totp_enabled")
    op.drop_column("users", "totp_secret_encrypted")
