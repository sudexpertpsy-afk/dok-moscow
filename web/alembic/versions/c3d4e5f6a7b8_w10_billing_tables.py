"""W-10: таблицы биллинга (tariffs, subscriptions, payments, payment_settings)

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-08-01 16:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "tariffs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("price_month_kop", sa.Integer(), nullable=False),
        sa.Column("price_year_kop", sa.Integer(), nullable=False),
        sa.Column("limit_documents_month", sa.Integer(), nullable=True),
        sa.Column("limit_users", sa.Integer(), nullable=True),
        sa.Column("watermark", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index(op.f("ix_tariffs_code"), "tariffs", ["code"], unique=True)

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("tariff_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("customer_key", sa.String(length=128), nullable=True),
        sa.Column("rebill_id", sa.String(length=128), nullable=True),
        sa.Column("is_beta", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tariff_id"], ["tariffs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_subscriptions_org_id"), "subscriptions", ["org_id"], unique=False)
    op.create_index(op.f("ix_subscriptions_tariff_id"), "subscriptions", ["tariff_id"], unique=False)
    op.create_index(op.f("ix_subscriptions_status"), "subscriptions", ["status"], unique=False)
    op.create_index("ix_subscriptions_org_status", "subscriptions", ["org_id", "status"], unique=False)

    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=True),
        sa.Column("amount_kop", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("tbank_payment_id", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("receipt_status", sa.String(length=64), nullable=True),
        sa.Column("receipt_url", sa.String(length=1024), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("raw_events", JsonType, nullable=False),
        sa.Column("manual_basis", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_payments_org_id"), "payments", ["org_id"], unique=False)
    op.create_index(op.f("ix_payments_subscription_id"), "payments", ["subscription_id"], unique=False)
    op.create_index(op.f("ix_payments_tbank_payment_id"), "payments", ["tbank_payment_id"], unique=False)
    op.create_index("ix_payments_org_created", "payments", ["org_id", "created_at"], unique=False)
    op.create_index("ix_payments_status", "payments", ["status"], unique=False)

    op.create_table(
        "payment_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("terminal_key", sa.String(length=128), nullable=True),
        sa.Column("password_encrypted", sa.Text(), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column(
            "recurrents_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("taxation", sa.String(length=32), nullable=False),
        sa.Column("vat_rate", sa.String(length=16), nullable=False),
        sa.Column("default_receipt_email", sa.String(length=320), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Сид тарифов + singleton настроек
    tariffs = sa.table(
        "tariffs",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("price_month_kop", sa.Integer),
        sa.column("price_year_kop", sa.Integer),
        sa.column("limit_documents_month", sa.Integer),
        sa.column("limit_users", sa.Integer),
        sa.column("watermark", sa.Boolean),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(
        tariffs,
        [
            {
                "code": "guest",
                "name": "Гость",
                "price_month_kop": 0,
                "price_year_kop": 0,
                "limit_documents_month": 3,
                "limit_users": 1,
                "watermark": True,
                "is_active": True,
            },
            {
                "code": "specialist",
                "name": "Специалист",
                "price_month_kop": 99000,
                "price_year_kop": 990000,
                "limit_documents_month": None,
                "limit_users": 1,
                "watermark": False,
                "is_active": True,
            },
            {
                "code": "organization",
                "name": "Организация",
                "price_month_kop": 249000,
                "price_year_kop": 2490000,
                "limit_documents_month": None,
                "limit_users": 5,
                "watermark": False,
                "is_active": True,
            },
        ],
    )
    op.execute(
        sa.text(
            "INSERT INTO payment_settings "
            "(id, mode, recurrents_enabled, taxation, vat_rate) "
            "VALUES (1, 'test', false, 'usn_income', 'none')"
        )
    )

    # Бета-подписки для существующих организаций (до 2026-10-01 UTC)
    op.execute(
        sa.text(
            """
            INSERT INTO subscriptions (
                org_id, tariff_id, period, starts_at, ends_at, status,
                auto_renew, is_beta
            )
            SELECT
                o.id,
                t.id,
                'month',
                NOW(),
                TIMESTAMPTZ '2026-10-01 23:59:59+00',
                'trial',
                false,
                true
            FROM organizations o
            CROSS JOIN tariffs t
            WHERE t.code = 'specialist'
              AND NOT EXISTS (
                  SELECT 1 FROM subscriptions s WHERE s.org_id = o.id
              )
            """
        )
    )


def downgrade() -> None:
    op.drop_table("payment_settings")
    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_index("ix_payments_org_created", table_name="payments")
    op.drop_index(op.f("ix_payments_tbank_payment_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_subscription_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_org_id"), table_name="payments")
    op.drop_table("payments")
    op.drop_index("ix_subscriptions_org_status", table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_status"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_tariff_id"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_org_id"), table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index(op.f("ix_tariffs_code"), table_name="tariffs")
    op.drop_table("tariffs")
