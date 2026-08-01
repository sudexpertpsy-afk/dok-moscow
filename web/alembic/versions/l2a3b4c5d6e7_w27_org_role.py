"""W-27: роль пользователя в организации (org_role)

Revision ID: l2a3b4c5d6e7
Revises: k1f2a3b4c5d6
Create Date: 2026-08-02 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "l2a3b4c5d6e7"
down_revision: str | None = "k1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "org_role",
            sa.Enum("org_admin", "org_member", name="org_role", native_enum=False),
            nullable=True,
        ),
    )
    conn = op.get_bind()
    # Старейший пользователь каждой организации → org_admin, остальные → org_member.
    conn.execute(
        sa.text(
            """
            UPDATE users
            SET org_role = 'org_member'
            WHERE org_id IS NOT NULL
            """
        )
    )
    # SQLite / PostgreSQL: пометить админом первого по (created_at, id) в каждой org
    orgs = conn.execute(sa.text("SELECT DISTINCT org_id FROM users WHERE org_id IS NOT NULL")).fetchall()
    for (org_id,) in orgs:
        row = conn.execute(
            sa.text(
                """
                SELECT id FROM users
                WHERE org_id = :oid
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """
            ),
            {"oid": org_id},
        ).fetchone()
        if row:
            conn.execute(
                sa.text("UPDATE users SET org_role = 'org_admin' WHERE id = :uid"),
                {"uid": row[0]},
            )


def downgrade() -> None:
    op.drop_column("users", "org_role")
