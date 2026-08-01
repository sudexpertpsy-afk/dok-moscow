"""W-22: поисковый индекс НПА (tsvector, GIN, pg_trgm, триггер)

Revision ID: h8c9d0e1f2a3
Revises: f6a7b8c9d0e1
Create Date: 2026-08-01 20:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "h8c9d0e1f2a3"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.add_column(
        "act_versions",
        sa.Column("text_origin", sa.String(length=32), nullable=True),
    )

    op.create_table(
        "legal_search_docs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("act_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=True),
        sa.Column("fragment_id", sa.Integer(), nullable=True),
        sa.Column("article_ref", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("heading", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("requisites", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column(
            "indexed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["act_id"], ["legal_acts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["fragment_id"], ["act_fragments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["version_id"], ["act_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_legal_search_docs_act", "legal_search_docs", ["act_id"])
    op.create_index("ix_legal_search_docs_version", "legal_search_docs", ["version_id"])
    op.create_index("ix_legal_search_docs_fragment", "legal_search_docs", ["fragment_id"])
    op.execute(
        "CREATE INDEX ix_legal_search_docs_tsv ON legal_search_docs USING GIN (search_vector)"
    )
    op.execute(
        "CREATE INDEX ix_legal_search_docs_req_trgm ON legal_search_docs "
        "USING GIN (requisites gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_legal_search_docs_heading_trgm ON legal_search_docs "
        "USING GIN (heading gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_legal_acts_title_trgm ON legal_acts USING GIN (title gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_legal_acts_number_trgm ON legal_acts USING GIN (number gin_trgm_ops)"
    )

    # Пересчёт tsvector строки индекса
    op.execute(
        """
        CREATE OR REPLACE FUNCTION legal_search_docs_tsv_update() RETURNS trigger AS $$
        BEGIN
          NEW.search_vector :=
            setweight(to_tsvector('russian', coalesce(NEW.heading, '')), 'A')
            || setweight(to_tsvector('russian', coalesce(NEW.body_text, '')), 'B');
          NEW.indexed_at := now();
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_legal_search_docs_tsv ON legal_search_docs;
        CREATE TRIGGER trg_legal_search_docs_tsv
        BEFORE INSERT OR UPDATE OF heading, body_text
        ON legal_search_docs
        FOR EACH ROW EXECUTE FUNCTION legal_search_docs_tsv_update();
        """
    )

    # При публикации/снятии редакции — пометка для приложения; черновики не в индексе
    # (пересборку делает web/app/services/legal_search.rebuild_act_index;
    #  триггер удаляет строки архивных/черновых версий)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION legal_search_on_version_status() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'UPDATE' AND NEW.status IS DISTINCT FROM OLD.status THEN
            IF NEW.status <> 'published' THEN
              DELETE FROM legal_search_docs WHERE version_id = NEW.id;
            END IF;
          END IF;
          IF TG_OP = 'DELETE' THEN
            DELETE FROM legal_search_docs WHERE version_id = OLD.id;
            RETURN OLD;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_legal_search_version_status ON act_versions;
        CREATE TRIGGER trg_legal_search_version_status
        AFTER UPDATE OF status OR DELETE ON act_versions
        FOR EACH ROW EXECUTE FUNCTION legal_search_on_version_status();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_legal_search_version_status ON act_versions")
    op.execute("DROP FUNCTION IF EXISTS legal_search_on_version_status()")
    op.execute("DROP TRIGGER IF EXISTS trg_legal_search_docs_tsv ON legal_search_docs")
    op.execute("DROP FUNCTION IF EXISTS legal_search_docs_tsv_update()")
    op.execute("DROP INDEX IF EXISTS ix_legal_acts_number_trgm")
    op.execute("DROP INDEX IF EXISTS ix_legal_acts_title_trgm")
    op.execute("DROP INDEX IF EXISTS ix_legal_search_docs_heading_trgm")
    op.execute("DROP INDEX IF EXISTS ix_legal_search_docs_req_trgm")
    op.execute("DROP INDEX IF EXISTS ix_legal_search_docs_tsv")
    op.drop_index("ix_legal_search_docs_fragment", table_name="legal_search_docs")
    op.drop_index("ix_legal_search_docs_version", table_name="legal_search_docs")
    op.drop_index("ix_legal_search_docs_act", table_name="legal_search_docs")
    op.drop_table("legal_search_docs")
    op.drop_column("act_versions", "text_origin")
