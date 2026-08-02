"""W-35: закладки, заметки, слежение и история НПА в кабинете

Revision ID: p6e7f8a9b0c1
Revises: o5d6e7f8a9b0
Create Date: 2026-08-02 06:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p6e7f8a9b0c1"
down_revision: str | None = "o5d6e7f8a9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "law_bookmarks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "act_id",
            sa.Integer(),
            sa.ForeignKey("legal_acts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "fragment_id",
            sa.Integer(),
            sa.ForeignKey("act_fragments.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("bookmark_key", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index("ix_law_bookmarks_user_id", "law_bookmarks", ["user_id"])
    op.create_index("ix_law_bookmarks_act_id", "law_bookmarks", ["act_id"])
    op.create_index("ix_law_bookmarks_fragment_id", "law_bookmarks", ["fragment_id"])
    op.create_index(
        "uq_law_bookmarks_user_key", "law_bookmarks", ["user_id", "bookmark_key"], unique=True
    )

    op.create_table(
        "law_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "fragment_id",
            sa.Integer(),
            sa.ForeignKey("act_fragments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index("ix_law_notes_user_id", "law_notes", ["user_id"])
    op.create_index("ix_law_notes_fragment_id", "law_notes", ["fragment_id"])
    op.create_index(
        "uq_law_notes_user_fragment", "law_notes", ["user_id", "fragment_id"], unique=True
    )

    op.create_table(
        "law_watches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "act_id",
            sa.Integer(),
            sa.ForeignKey("legal_acts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index("ix_law_watches_user_id", "law_watches", ["user_id"])
    op.create_index("ix_law_watches_act_id", "law_watches", ["act_id"])
    op.create_index("uq_law_watches_user_act", "law_watches", ["user_id", "act_id"], unique=True)

    op.create_table(
        "law_views",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "act_id",
            sa.Integer(),
            sa.ForeignKey("legal_acts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "fragment_id",
            sa.Integer(),
            sa.ForeignKey("act_fragments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "viewed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index("ix_law_views_user_id", "law_views", ["user_id"])
    op.create_index("ix_law_views_act_id", "law_views", ["act_id"])
    op.create_index("ix_law_views_user_viewed", "law_views", ["user_id", "viewed_at"])

    op.create_table(
        "law_watch_notices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "act_id",
            sa.Integer(),
            sa.ForeignKey("legal_acts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "version_id",
            sa.Integer(),
            sa.ForeignKey("act_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_law_watch_notices_user_id", "law_watch_notices", ["user_id"])
    op.create_index("ix_law_watch_notices_act_id", "law_watch_notices", ["act_id"])
    op.create_index(
        "ix_law_watch_notices_user_sent", "law_watch_notices", ["user_id", "sent_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_law_watch_notices_user_sent", table_name="law_watch_notices")
    op.drop_index("ix_law_watch_notices_act_id", table_name="law_watch_notices")
    op.drop_index("ix_law_watch_notices_user_id", table_name="law_watch_notices")
    op.drop_table("law_watch_notices")

    op.drop_index("ix_law_views_user_viewed", table_name="law_views")
    op.drop_index("ix_law_views_act_id", table_name="law_views")
    op.drop_index("ix_law_views_user_id", table_name="law_views")
    op.drop_table("law_views")

    op.drop_index("uq_law_watches_user_act", table_name="law_watches")
    op.drop_index("ix_law_watches_act_id", table_name="law_watches")
    op.drop_index("ix_law_watches_user_id", table_name="law_watches")
    op.drop_table("law_watches")

    op.drop_index("uq_law_notes_user_fragment", table_name="law_notes")
    op.drop_index("ix_law_notes_fragment_id", table_name="law_notes")
    op.drop_index("ix_law_notes_user_id", table_name="law_notes")
    op.drop_table("law_notes")

    op.drop_index("uq_law_bookmarks_user_key", table_name="law_bookmarks")
    op.drop_index("ix_law_bookmarks_fragment_id", table_name="law_bookmarks")
    op.drop_index("ix_law_bookmarks_act_id", table_name="law_bookmarks")
    op.drop_index("ix_law_bookmarks_user_id", table_name="law_bookmarks")
    op.drop_table("law_bookmarks")
