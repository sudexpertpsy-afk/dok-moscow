"""W-25: вход через Яндекс ID (oauth_identities)

Revision ID: j0e1f2a3b4c5
Revises: i9d0e1f2a3b4
Create Date: 2026-08-01 23:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "j0e1f2a3b4c5"
down_revision: str | None = "i9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("users", "password_hash", existing_type=sa.String(length=255), nullable=True)
    op.create_table(
        "oauth_identities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("sub", sa.String(length=255), nullable=False),
        sa.Column("provider_email", sa.String(length=320), nullable=True),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_oauth_identities_user_id", "oauth_identities", ["user_id"], unique=False)
    op.create_index(
        "uq_oauth_identities_provider_sub",
        "oauth_identities",
        ["provider", "sub"],
        unique=True,
    )
    op.add_column(
        "payment_settings",
        sa.Column(
            "yandex_login_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("payment_settings", "yandex_login_enabled")
    op.drop_index("uq_oauth_identities_provider_sub", table_name="oauth_identities")
    op.drop_index("ix_oauth_identities_user_id", table_name="oauth_identities")
    op.drop_table("oauth_identities")
    op.alter_column("users", "password_hash", existing_type=sa.String(length=255), nullable=False)
