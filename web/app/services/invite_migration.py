"""W-50.1: хелперы миграции инвайтов (dup-check + backfill status).

Импортируются из alembic a50bc01 и из unit-тестов.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.engine import Connection

ACTIVE_WHERE = "is_active IS TRUE"

# SQL backfill status (после колонки status со default active):
# accepted — в org есть пользователь с тем же e-mail;
# expired — срок вышел;
# иначе active. Затем is_active = (status = 'active').
STATUS_BACKFILL_SQL = """
UPDATE invites SET status = CASE
  WHEN used_at IS NOT NULL THEN 'accepted'
  WHEN org_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM users u
    WHERE u.org_id = invites.org_id
      AND lower(u.email) = lower(invites.email)
  ) THEN 'accepted'
  WHEN expires_at <= CURRENT_TIMESTAMP THEN 'expired'
  ELSE 'active'
END
"""

IS_ACTIVE_SYNC_SQL = """
UPDATE invites SET is_active = (status = 'active')
"""


def find_active_invite_email_dups(conn: Connection) -> Sequence[sa.Row]:
    """Активные дубли e-mail (то же условие, что у partial unique index)."""
    return conn.execute(
        sa.text(
            f"""
            SELECT lower(email), count(*) FROM invites
            WHERE {ACTIVE_WHERE}
            GROUP BY 1 HAVING count(*) > 1
            """
        )
    ).fetchall()


def assert_no_active_invite_email_dups(conn: Connection) -> None:
    dups = find_active_invite_email_dups(conn)
    if dups:
        raise RuntimeError(f"a50bc01: активные дубли инвайтов, разрешите вручную: {dups}")


def backfill_invite_status(conn: Connection) -> None:
    """Проставить status + синхронизировать is_active."""
    conn.execute(sa.text(STATUS_BACKFILL_SQL))
    conn.execute(sa.text(IS_ACTIVE_SYNC_SQL))


def compute_invite_status_row(
    *,
    used_at,
    expires_at,
    org_id: int | None,
    email: str,
    org_user_emails: set[str],
    now,
) -> str:
    """Чистая логика backfill для unit-тестов (без SQL)."""
    if used_at is not None:
        return "accepted"
    if org_id is not None and email.strip().lower() in org_user_emails:
        return "accepted"
    if expires_at is not None and expires_at <= now:
        return "expired"
    return "active"
