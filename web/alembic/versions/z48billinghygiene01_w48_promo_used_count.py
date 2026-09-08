"""W-48: used_count промокодов = только confirmed (пересчёт)

Revision ID: z48billinghygiene01
Revises: y47a1b2c3d4e
Create Date: 2026-09-08 22:40:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "z48billinghygiene01"
down_revision: str | None = "y47a1b2c3d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # payment_status — VARCHAR; значение expired добавляется только в коде.
    # used_count: reserve на Init больше не инкрементирует — выравниваем с CONFIRMED.
    # Явно 90PROBA/BETA50 и остальные промокоды одной формулой.
    op.execute(
        """
        UPDATE promo_codes
        SET used_count = (
            SELECT COUNT(*)::int
            FROM payments
            WHERE payments.promo_code_id = promo_codes.id
              AND payments.status = 'confirmed'
        )
        """
    )


def downgrade() -> None:
    # Обратный пересчёт по Init невосстановим однозначно — no-op.
    pass
