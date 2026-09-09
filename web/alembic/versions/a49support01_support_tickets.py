"""Support tickets: обращения и предложения улучшений

Revision ID: a49support01
Revises: z48billinghygiene01
Create Date: 2026-09-09 17:55:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a49support01"
down_revision: str | None = "z48billinghygiene01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "support_tickets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("admin_note", sa.Text(), nullable=True),
        sa.Column("admin_reply", sa.Text(), nullable=True),
        sa.Column("page_url", sa.String(length=1024), nullable=True),
        sa.Column("app_version", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("attachment_path", sa.String(length=512), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_support_tickets_org_id", "support_tickets", ["org_id"])
    op.create_index("ix_support_tickets_user_id", "support_tickets", ["user_id"])
    op.create_index(
        "ix_support_tickets_org_status", "support_tickets", ["org_id", "status"]
    )
    op.create_index(
        "ix_support_tickets_status_created",
        "support_tickets",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_support_tickets_kind_status", "support_tickets", ["kind", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_support_tickets_kind_status", table_name="support_tickets")
    op.drop_index("ix_support_tickets_status_created", table_name="support_tickets")
    op.drop_index("ix_support_tickets_org_status", table_name="support_tickets")
    op.drop_index("ix_support_tickets_user_id", table_name="support_tickets")
    op.drop_index("ix_support_tickets_org_id", table_name="support_tickets")
    op.drop_table("support_tickets")
