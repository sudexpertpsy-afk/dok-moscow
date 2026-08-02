"""W-38: is_complimentary на подписках

Revision ID: r8g9b0c1d2e3
Revises: q7f8a9b0c1d2
Create Date: 2026-08-02 08:40:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r8g9b0c1d2e3"
down_revision: str | None = "q7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column(
            "is_complimentary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.alter_column("subscriptions", "is_complimentary", server_default=None)


def downgrade() -> None:
    op.drop_column("subscriptions", "is_complimentary")
