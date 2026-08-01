"""W-30: таблица jobs и индексы под рост данных

Revision ID: m3b4c5d6e7f8
Revises: l2a3b4c5d6e7
Create Date: 2026-08-02 01:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "m3b4c5d6e7f8"
down_revision: str | None = "l2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("type", sa.Enum("document_pdf", "package_pdf", "package_zip", name="job_type", native_enum=False), nullable=False),
        sa.Column("status", sa.Enum("pending", "running", "succeeded", "failed", name="job_status", native_enum=False), nullable=False),
        sa.Column("payload", JsonType, nullable=False),
        sa.Column("result", JsonType, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_jobs_org_id", "jobs", ["org_id"])
    op.create_index("ix_jobs_user_id", "jobs", ["user_id"])
    op.create_index("ix_jobs_type", "jobs", ["type"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_status_created", "jobs", ["status", "created_at"])
    op.create_index("ix_jobs_org_created", "jobs", ["org_id", "created_at"])

    # Доп. индексы под дашборд/календарь (документы и платежи уже проиндексированы)
    for sql in (
        "CREATE INDEX IF NOT EXISTS ix_calendar_events_org_due ON calendar_events (org_id, due_on)",
        "CREATE INDEX IF NOT EXISTS ix_contracts_org_ends ON contracts (org_id, ends_on)",
    ):
        try:
            op.execute(sa.text(sql))
        except Exception:
            pass


def downgrade() -> None:
    op.drop_index("ix_jobs_org_created", table_name="jobs")
    op.drop_index("ix_jobs_status_created", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_type", table_name="jobs")
    op.drop_index("ix_jobs_user_id", table_name="jobs")
    op.drop_index("ix_jobs_org_id", table_name="jobs")
    op.drop_table("jobs")
