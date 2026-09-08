"""Pricing: Specialist 250 / Org 1100; year −10%; years_2 period; retire BETA50

Revision ID: x47pricing2501100
Revises: w46e1a2b3c4d
Create Date: 2026-09-08 07:20:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "x47pricing2501100"
down_revision: str | None = "w46e1a2b3c4d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          ALTER TYPE subscription_period ADD VALUE 'years_2';
        EXCEPTION
          WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    # месяц → год = ×12 −10%
    op.execute(
        """
        UPDATE tariffs SET
          price_month_kop = 25000,
          price_year_kop = 270000
        WHERE code = 'specialist';
        """
    )
    op.execute(
        """
        UPDATE tariffs SET
          price_month_kop = 110000,
          price_year_kop = 1188000
        WHERE code = 'organization';
        """
    )
    op.execute(
        """
        UPDATE promo_codes SET is_active = false
        WHERE upper(code) IN ('BETA50', 'PROBA123');
        """
    )


def downgrade() -> None:
    # enum value years_2 не удаляем (Postgres)
    op.execute(
        """
        UPDATE tariffs SET
          price_month_kop = 99000,
          price_year_kop = 990000
        WHERE code = 'specialist';
        """
    )
    op.execute(
        """
        UPDATE tariffs SET
          price_month_kop = 249000,
          price_year_kop = 2490000
        WHERE code = 'organization';
        """
    )
