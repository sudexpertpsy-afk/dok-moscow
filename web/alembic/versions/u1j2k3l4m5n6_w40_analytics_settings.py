"""W-40: analytics_settings singleton

Revision ID: u1j2k3l4m5n6
Revises: t0i1d2e3f4g5
Create Date: 2026-08-02 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u1j2k3l4m5n6"
down_revision: str | None = "t0i1d2e3f4g5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analytics_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("yandex_metrika_id", sa.String(length=16), nullable=False, server_default=""),
        sa.Column(
            "yandex_metrika_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "yandex_metrika_webvisor",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "yandex_metrika_clickmap",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "yandex_metrika_track_forms",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("ga4_measurement_id", sa.String(length=32), nullable=False, server_default=""),
        sa.Column(
            "ga4_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "yandex_webmaster_code", sa.String(length=64), nullable=False, server_default=""
        ),
        sa.Column(
            "yandex_webmaster_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "google_site_verification",
            sa.String(length=128),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "google_site_verification_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO analytics_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.drop_table("analytics_settings")
