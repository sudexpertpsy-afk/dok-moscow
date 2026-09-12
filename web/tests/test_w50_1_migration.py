"""W-50.1 §1: dup-check активных инвайтов + backfill status."""

from __future__ import annotations

from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

from app.models import utcnow
from app.services.invite_migration import (
    assert_no_active_invite_email_dups,
    compute_invite_status_row,
    find_active_invite_email_dups,
)


def _memory_invites_conn():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                CREATE TABLE invites (
                  id INTEGER PRIMARY KEY,
                  email TEXT NOT NULL,
                  is_active INTEGER NOT NULL DEFAULT 1,
                  status TEXT NOT NULL DEFAULT 'active',
                  used_at TEXT,
                  expires_at TEXT,
                  org_id INTEGER
                )
                """
            )
        )
        conn.execute(
            sa.text(
                """
                CREATE TABLE users (
                  id INTEGER PRIMARY KEY,
                  org_id INTEGER,
                  email TEXT NOT NULL
                )
                """
            )
        )
    return engine


def test_dup_check_raises_then_passes_after_deactivate():
    engine = _memory_invites_conn()
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO invites (id, email, is_active) VALUES "
                "(1, 'Dup@Example.com', 1), (2, 'dup@example.com', 1)"
            )
        )
        dups = find_active_invite_email_dups(conn)
        assert len(dups) == 1
        assert dups[0][0] == "dup@example.com"
        assert dups[0][1] == 2
        with pytest.raises(RuntimeError, match="активные дубли"):
            assert_no_active_invite_email_dups(conn)

        conn.execute(sa.text("UPDATE invites SET is_active = 0 WHERE id = 2"))
        assert find_active_invite_email_dups(conn) == []
        assert_no_active_invite_email_dups(conn)  # не бросает


def test_compute_invite_status_backfill_logic():
    now = utcnow()
    assert (
        compute_invite_status_row(
            used_at=now,
            expires_at=now + timedelta(days=1),
            org_id=1,
            email="a@b.c",
            org_user_emails=set(),
            now=now,
        )
        == "accepted"
    )
    assert (
        compute_invite_status_row(
            used_at=None,
            expires_at=now + timedelta(days=1),
            org_id=5,
            email="User@Ex.com",
            org_user_emails={"user@ex.com"},
            now=now,
        )
        == "accepted"
    )
    assert (
        compute_invite_status_row(
            used_at=None,
            expires_at=now - timedelta(hours=1),
            org_id=None,
            email="x@y.z",
            org_user_emails=set(),
            now=now,
        )
        == "expired"
    )
    assert (
        compute_invite_status_row(
            used_at=None,
            expires_at=now + timedelta(days=2),
            org_id=None,
            email="x@y.z",
            org_user_emails=set(),
            now=now,
        )
        == "active"
    )


def test_migration_has_no_name_seed_and_has_dup_guard():
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "alembic/versions/a50bc01_w50_org_invite_purge.py"
    text = src.read_text(encoding="utf-8")
    assert "assert_no_active_invite_email_dups" in text
    assert "purge_mode" in text
    assert 'sa.Column(\n            "status"' in text or '"status"' in text
    assert "LIKE :pat" not in text
    assert "drop_column" in text and "status" in text
    assert "migrate_rehearsal" not in text  # скрипт отдельно
    assert Path(__file__).resolve().parents[2].joinpath("deploy/migrate_rehearsal.sh").is_file()
