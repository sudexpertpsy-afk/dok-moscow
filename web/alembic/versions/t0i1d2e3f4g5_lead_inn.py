"""Заявки: необязательное поле ИНН

Revision ID: t0i1d2e3f4g5
Revises: s9h0c1d2e3f4
Create Date: 2026-08-02 09:20:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "t0i1d2e3f4g5"
down_revision: str | None = "s9h0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("inn", sa.String(length=12), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "inn")
