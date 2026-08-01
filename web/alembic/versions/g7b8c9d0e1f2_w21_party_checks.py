"""W-21: журнал проверок контрагента, статус ЕГРЮЛ, лимит

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-01 18:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "g7b8c9d0e1f2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "counterparties",
        sa.Column("egrul_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "counterparties",
        sa.Column("egrul_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "payment_settings",
        sa.Column(
            "party_check_daily_limit",
            sa.Integer(),
            nullable=False,
            server_default="100",
        ),
    )
    op.create_table(
        "party_checks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("counterparty_id", sa.Integer(), nullable=True),
        sa.Column("inn", sa.String(length=12), nullable=False),
        sa.Column("query", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("status_label", sa.String(length=64), nullable=True),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["counterparty_id"], ["counterparties.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_party_checks_org_id", "party_checks", ["org_id"])
    op.create_index("ix_party_checks_user_id", "party_checks", ["user_id"])
    op.create_index("ix_party_checks_counterparty_id", "party_checks", ["counterparty_id"])
    op.create_index("ix_party_checks_org_checked", "party_checks", ["org_id", "checked_at"])
    op.create_index("ix_party_checks_org_inn", "party_checks", ["org_id", "inn"])


def downgrade() -> None:
    op.drop_index("ix_party_checks_org_inn", table_name="party_checks")
    op.drop_index("ix_party_checks_org_checked", table_name="party_checks")
    op.drop_index("ix_party_checks_counterparty_id", table_name="party_checks")
    op.drop_index("ix_party_checks_user_id", table_name="party_checks")
    op.drop_index("ix_party_checks_org_id", table_name="party_checks")
    op.drop_table("party_checks")
    op.drop_column("payment_settings", "party_check_daily_limit")
    op.drop_column("counterparties", "egrul_checked_at")
    op.drop_column("counterparties", "egrul_status")
