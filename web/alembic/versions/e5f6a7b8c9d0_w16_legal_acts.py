"""W-16: таблицы раздела «Законодательство»

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-01 18:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "legal_acts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("act_kind", sa.String(length=128), nullable=False),
        sa.Column("number", sa.String(length=128), nullable=False),
        sa.Column("adopted_on", sa.Date(), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("authority", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=False),
        sa.Column("eo_number", sa.String(length=64), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("tracked_articles", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_legal_acts_category_sort", "legal_acts", ["category", "sort_order"])

    op.create_table(
        "act_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("act_id", sa.Integer(), nullable=False),
        sa.Column("revision_date", sa.Date(), nullable=True),
        sa.Column("change_basis", sa.Text(), nullable=True),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("pdf_path", sa.String(length=1024), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("loaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("loaded_by_user_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["act_id"], ["legal_acts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["loaded_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_act_versions_act_id", "act_versions", ["act_id"])
    op.create_index("ix_act_versions_act_status", "act_versions", ["act_id", "status"])
    op.create_index(
        "uq_act_versions_one_published",
        "act_versions",
        ["act_id"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
        sqlite_where=sa.text("status = 'published'"),
    )

    op.create_table(
        "act_fragments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("act_id", sa.Integer(), nullable=False),
        sa.Column("article_ref", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["act_id"], ["legal_acts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_act_fragments_act_id", "act_fragments", ["act_id"])
    op.create_index("ix_act_fragments_act_sort", "act_fragments", ["act_id", "sort_order"])
    op.create_index("uq_act_fragments_act_ref", "act_fragments", ["act_id", "article_ref"], unique=True)

    op.create_table(
        "act_watch_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("act_id", sa.Integer(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("draft_version_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["act_id"], ["legal_acts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["draft_version_id"], ["act_versions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_act_watch_log_act_id", "act_watch_log", ["act_id"])
    op.create_index("ix_act_watch_log_act_checked", "act_watch_log", ["act_id", "checked_at"])


def downgrade() -> None:
    op.drop_index("ix_act_watch_log_act_checked", table_name="act_watch_log")
    op.drop_index("ix_act_watch_log_act_id", table_name="act_watch_log")
    op.drop_table("act_watch_log")
    op.drop_index("uq_act_fragments_act_ref", table_name="act_fragments")
    op.drop_index("ix_act_fragments_act_sort", table_name="act_fragments")
    op.drop_index("ix_act_fragments_act_id", table_name="act_fragments")
    op.drop_table("act_fragments")
    op.drop_index("uq_act_versions_one_published", table_name="act_versions")
    op.drop_index("ix_act_versions_act_status", table_name="act_versions")
    op.drop_index("ix_act_versions_act_id", table_name="act_versions")
    op.drop_table("act_versions")
    op.drop_index("ix_legal_acts_category_sort", table_name="legal_acts")
    op.drop_table("legal_acts")
