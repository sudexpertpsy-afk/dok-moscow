"""Календарь: таблица calendar_events

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-01 17:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calendar_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("remind_days_before", sa.Integer(), nullable=True),
        sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counterparty_id", sa.Integer(), nullable=True),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("contract_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["counterparty_id"], ["counterparties.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_calendar_events_org_id", "calendar_events", ["org_id"])
    op.create_index("ix_calendar_events_org_due", "calendar_events", ["org_id", "due_on"])
    op.create_index("ix_calendar_events_org_status", "calendar_events", ["org_id", "status"])
    op.create_index("ix_calendar_events_counterparty_id", "calendar_events", ["counterparty_id"])
    op.create_index("ix_calendar_events_document_id", "calendar_events", ["document_id"])
    op.create_index("ix_calendar_events_contract_id", "calendar_events", ["contract_id"])


def downgrade() -> None:
    op.drop_index("ix_calendar_events_contract_id", table_name="calendar_events")
    op.drop_index("ix_calendar_events_document_id", table_name="calendar_events")
    op.drop_index("ix_calendar_events_counterparty_id", table_name="calendar_events")
    op.drop_index("ix_calendar_events_org_status", table_name="calendar_events")
    op.drop_index("ix_calendar_events_org_due", table_name="calendar_events")
    op.drop_index("ix_calendar_events_org_id", table_name="calendar_events")
    op.drop_table("calendar_events")
