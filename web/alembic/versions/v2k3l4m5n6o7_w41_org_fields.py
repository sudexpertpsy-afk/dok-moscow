"""W-41: org_fields — словарь пользовательских полей организации

Revision ID: v2k3l4m5n6o7
Revises: u1j2k3l4m5n6
Create Date: 2026-08-02 14:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "v2k3l4m5n6o7"
down_revision: str | None = "u1j2k3l4m5n6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Как в models._STR_ENUM: VARCHAR + check на уровне приложения, без PG ENUM.
_ORG_FIELD_TYPE = sa.Enum(
    "string",
    "multiline",
    "date",
    "money",
    "checkbox",
    "select",
    "counter",
    name="org_field_type",
    native_enum=False,
)


def upgrade() -> None:
    # На случай частичного прогона предыдущей версии миграции (PG ENUM).
    op.execute(sa.text("DROP TYPE IF EXISTS org_field_type"))
    op.create_table(
        "org_fields",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("field_type", _ORG_FIELD_TYPE, nullable=False),
        sa.Column(
            "required", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("default_value", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column("hint", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("options", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_org_fields_org_id", "org_fields", ["org_id"])
    op.create_index(
        "uq_org_fields_org_name", "org_fields", ["org_id", "name"], unique=True
    )
    op.create_index(
        "ix_org_fields_created_by_user_id", "org_fields", ["created_by_user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_org_fields_created_by_user_id", table_name="org_fields")
    op.drop_index("uq_org_fields_org_name", table_name="org_fields")
    op.drop_index("ix_org_fields_org_id", table_name="org_fields")
    op.drop_table("org_fields")
    op.execute(sa.text("DROP TYPE IF EXISTS org_field_type"))
