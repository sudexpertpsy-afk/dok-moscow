"""W-50 B / W-50.1: nullable invite org, pending_*, status, is_internal, signups, purge_mode

Revision ID: a50bc01
Revises: b49numbering01
Create Date: 2026-09-12 19:30:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a50bc01"
down_revision: str | None = "b49numbering01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "is_internal",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.alter_column(
        "invites",
        "org_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column(
        "invites",
        sa.Column("pending_org_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "invites",
        sa.Column("pending_tariff_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "invites",
        sa.Column("pending_months", sa.Integer(), nullable=True),
    )
    op.add_column(
        "invites",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "invites",
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="active",
        ),
    )

    # Предварительный backfill is_active (до status-логики) — для совместимости
    op.execute(
        sa.text("UPDATE invites SET is_active = false WHERE used_at IS NOT NULL")
    )
    op.execute(
        sa.text(
            "UPDATE invites SET is_active = false "
            "WHERE used_at IS NULL AND expires_at <= CURRENT_TIMESTAMP"
        )
    )

    # W-50.1: status + sync is_active (accepted / expired / active)
    from app.services.invite_migration import (
        assert_no_active_invite_email_dups,
        backfill_invite_status,
    )

    bind = op.get_bind()
    backfill_invite_status(bind)
    assert_no_active_invite_email_dups(bind)

    if bind.dialect.name == "postgresql":
        op.create_index(
            "ix_invites_active_email_lower",
            "invites",
            [sa.text("lower(email)")],
            unique=True,
            postgresql_where=sa.text("is_active IS TRUE"),
        )
    elif bind.dialect.name == "sqlite":
        try:
            op.create_index(
                "ix_invites_active_email_lower",
                "invites",
                [sa.text("lower(email)")],
                unique=True,
                sqlite_where=sa.text("is_active = 1"),
            )
        except Exception:
            pass

    op.create_table(
        "signups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("org_name", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column(
            "tariff_code",
            sa.String(length=64),
            nullable=False,
            server_default="specialist",
        ),
        sa.Column(
            "period",
            sa.String(length=32),
            nullable=False,
            server_default="month",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("email", name="uq_signups_email"),
        sa.UniqueConstraint("token", name="uq_signups_token"),
    )
    op.create_index("ix_signups_email", "signups", ["email"], unique=True)
    op.create_index("ix_signups_token", "signups", ["token"], unique=True)

    op.add_column(
        "payment_settings",
        sa.Column(
            "two_fa_policy_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "payment_settings",
        sa.Column("two_fa_policy_enabled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE payment_settings SET two_fa_policy_enabled_at = CURRENT_TIMESTAMP "
            "WHERE two_fa_policy_enabled_at IS NULL"
        )
    )

    # W-50.1 §3: purge dry|live (по умолчанию dry)
    op.add_column(
        "payment_settings",
        sa.Column(
            "purge_mode",
            sa.String(length=16),
            nullable=False,
            server_default="dry",
        ),
    )
    op.add_column(
        "payment_settings",
        sa.Column("purge_mode_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE payment_settings SET purge_mode_changed_at = CURRENT_TIMESTAMP "
            "WHERE purge_mode_changed_at IS NULL"
        )
    )
    # is_internal seed по именам убран (W-50.1 §4) — manage.py mark_internal


def downgrade() -> None:
    op.drop_column("payment_settings", "purge_mode_changed_at")
    op.drop_column("payment_settings", "purge_mode")
    op.drop_column("payment_settings", "two_fa_policy_enabled_at")
    op.drop_column("payment_settings", "two_fa_policy_enabled")

    op.drop_index("ix_signups_token", table_name="signups")
    op.drop_index("ix_signups_email", table_name="signups")
    op.drop_table("signups")

    bind = op.get_bind()
    if bind.dialect.name in ("postgresql", "sqlite"):
        try:
            op.drop_index("ix_invites_active_email_lower", table_name="invites")
        except Exception:
            pass

    op.drop_column("invites", "status")
    op.drop_column("invites", "pending_months")
    op.drop_column("invites", "pending_tariff_code")
    op.drop_column("invites", "pending_org_name")
    op.drop_column("invites", "is_active")
    op.alter_column(
        "invites",
        "org_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_column("organizations", "is_internal")
