"""Проверка alembic upgrade head на PostgreSQL."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

WEB_ROOT = Path(__file__).resolve().parents[1]
PG_URL = os.environ.get(
    "TEST_PG_URL",
    "postgresql+psycopg://dok:dok_dev_pass@127.0.0.1:5432/dok_alembic_check",
)


def _pg_available(url: str) -> bool:
    try:
        eng = create_engine(url.rsplit("/", 1)[0] + "/postgres")
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    not _pg_available(PG_URL),
    reason="PostgreSQL недоступен для проверки Alembic",
)
def test_alembic_upgrade_head_clean_db():
    # создать чистую БД
    admin = create_engine(
        "postgresql+psycopg://dok:dok_dev_pass@127.0.0.1:5432/postgres",
        isolation_level="AUTOCOMMIT",
    )
    db_name = PG_URL.rsplit("/", 1)[-1]
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        conn.execute(text(f'CREATE DATABASE "{db_name}" OWNER dok'))

    env = os.environ.copy()
    env["DB_URL"] = PG_URL
    env["PYTHONPATH"] = str(WEB_ROOT)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(WEB_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    eng = create_engine(PG_URL)
    tables = set(inspect(eng).get_table_names())
    expected = {
        "organizations",
        "users",
        "invites",
        "counterparties",
        "contracts",
        "documents",
        "counters",
        "events",
        "leads",
        "password_reset_tokens",
        "tariffs",
        "subscriptions",
        "payments",
        "payment_settings",
        "alembic_version",
    }
    assert expected.issubset(tables)

    with eng.connect() as conn:
        ver = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert ver
