"""W-36: единое окно CMS, тарифы, промокоды и анонсы

Revision ID: q7f8a9b0c1d2
Revises: p6e7f8a9b0c1
Create Date: 2026-08-02 06:45:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "q7f8a9b0c1d2"
down_revision: str | None = "p6e7f8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("tariffs", sa.Column("blurb", sa.Text(), nullable=False, server_default=""))
    op.add_column(
        "tariffs",
        sa.Column("features", JsonType, nullable=False, server_default=sa.text("'[]'")),
    )
    op.alter_column("tariffs", "blurb", server_default=None)
    op.alter_column("tariffs", "features", server_default=None)

    op.create_table(
        "tariff_price_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tariff_id",
            sa.Integer(),
            sa.ForeignKey("tariffs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tariff_code", sa.String(length=32), nullable=False),
        sa.Column("old_price_month_kop", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_price_month_kop", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("old_price_year_kop", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_price_year_kop", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("old_blurb", sa.Text(), nullable=False, server_default=""),
        sa.Column("new_blurb", sa.Text(), nullable=False, server_default=""),
        sa.Column("old_features", JsonType, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("new_features", JsonType, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_tariff_price_log_tariff_id", "tariff_price_log", ["tariff_id"])
    op.create_index("ix_tariff_price_log_tariff_code", "tariff_price_log", ["tariff_code"])
    op.create_index("ix_tariff_price_log_updated_by", "tariff_price_log", ["updated_by"])

    op.create_table(
        "content_blocks",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("body_md", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.create_index("ix_content_blocks_status", "content_blocks", ["status"])
    op.create_index("ix_content_blocks_updated_by", "content_blocks", ["updated_by"])

    op.create_table(
        "content_block_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "block_key",
            sa.String(length=64),
            sa.ForeignKey("content_blocks.key", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("body_md", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="published"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.create_index(
        "uq_content_block_versions_key_version",
        "content_block_versions",
        ["block_key", "version"],
        unique=True,
    )
    op.create_index("ix_content_block_versions_block_key", "content_block_versions", ["block_key"])
    op.create_index("ix_content_block_versions_created_by", "content_block_versions", ["created_by"])

    op.create_table(
        "promo_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False, server_default="percent"),
        sa.Column("value", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tariff_codes", JsonType, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("periods", JsonType, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_promo_codes_code", "promo_codes", ["code"], unique=True)
    op.create_index("ix_promo_codes_is_active", "promo_codes", ["is_active"])

    op.add_column("payments", sa.Column("amount_base_kop", sa.Integer(), nullable=True))
    op.add_column(
        "payments",
        sa.Column("discount_kop", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("payments", sa.Column("promo_code_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_payments_promo_code_id_promo_codes",
        "payments",
        "promo_codes",
        ["promo_code_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_payments_promo_code_id", "payments", ["promo_code_id"])
    op.alter_column("payments", "discount_kop", server_default=None)

    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("body_md", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.create_index("ix_announcements_status", "announcements", ["status"])
    op.create_index("ix_announcements_is_active", "announcements", ["is_active"])
    op.create_index("ix_announcements_updated_by", "announcements", ["updated_by"])


def downgrade() -> None:
    op.drop_index("ix_announcements_updated_by", table_name="announcements")
    op.drop_index("ix_announcements_is_active", table_name="announcements")
    op.drop_index("ix_announcements_status", table_name="announcements")
    op.drop_table("announcements")

    op.drop_index("ix_payments_promo_code_id", table_name="payments")
    op.drop_constraint("fk_payments_promo_code_id_promo_codes", "payments", type_="foreignkey")
    op.drop_column("payments", "promo_code_id")
    op.drop_column("payments", "discount_kop")
    op.drop_column("payments", "amount_base_kop")

    op.drop_index("ix_promo_codes_is_active", table_name="promo_codes")
    op.drop_index("ix_promo_codes_code", table_name="promo_codes")
    op.drop_table("promo_codes")

    op.drop_index("ix_content_block_versions_created_by", table_name="content_block_versions")
    op.drop_index("ix_content_block_versions_block_key", table_name="content_block_versions")
    op.drop_index("uq_content_block_versions_key_version", table_name="content_block_versions")
    op.drop_table("content_block_versions")

    op.drop_index("ix_content_blocks_updated_by", table_name="content_blocks")
    op.drop_index("ix_content_blocks_status", table_name="content_blocks")
    op.drop_table("content_blocks")

    op.drop_index("ix_tariff_price_log_updated_by", table_name="tariff_price_log")
    op.drop_index("ix_tariff_price_log_tariff_code", table_name="tariff_price_log")
    op.drop_index("ix_tariff_price_log_tariff_id", table_name="tariff_price_log")
    op.drop_table("tariff_price_log")

    op.drop_column("tariffs", "features")
    op.drop_column("tariffs", "blurb")
