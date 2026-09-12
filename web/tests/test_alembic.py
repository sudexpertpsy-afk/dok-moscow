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
        "calendar_events",
        "legal_acts",
        "act_versions",
        "act_fragments",
        "act_watch_log",
        "legal_search_docs",
        "party_checks",
        "jobs",
        "rate_limit_hits",
        "alembic_version",
    }
    assert expected.issubset(tables)

    with eng.connect() as conn:
        ver = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert ver == "a50bc01"

    # W-50.1: a50bc01 в обе стороны (downgrade -1 → upgrade head)
    org_cols = {c["name"] for c in inspect(eng).get_columns("organizations")}
    assert "is_internal" in org_cols
    inv_cols = {c["name"] for c in inspect(eng).get_columns("invites")}
    assert "is_active" in inv_cols and "status" in inv_cols
    assert "signups" in tables
    pay_cols = {c["name"] for c in inspect(eng).get_columns("payment_settings")}
    assert "purge_mode" in pay_cols

    down1 = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "-1"],
        cwd=str(WEB_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert down1.returncode == 0, down1.stdout + down1.stderr
    eng.dispose()
    eng = create_engine(PG_URL)
    with eng.connect() as conn:
        ver = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert ver == "b49numbering01"
    org_cols = {c["name"] for c in inspect(eng).get_columns("organizations")}
    assert "is_internal" not in org_cols
    assert "signups" not in set(inspect(eng).get_table_names())

    up_a50 = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(WEB_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert up_a50.returncode == 0, up_a50.stdout + up_a50.stderr
    eng.dispose()
    eng = create_engine(PG_URL)
    org_cols = {c["name"] for c in inspect(eng).get_columns("organizations")}
    assert "is_internal" in org_cols

    # W-49: downgrade b49numbering01 обязан работать (откат на v1.1.9).
    down = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "a49support01"],
        cwd=str(WEB_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert down.returncode == 0, down.stdout + down.stderr
    eng.dispose()
    eng = create_engine(PG_URL)
    cols = {c["name"] for c in inspect(eng).get_columns("counters")}
    assert "number_template" not in cols
    org_cols = {c["name"] for c in inspect(eng).get_columns("organizations")}
    assert "onboarding" not in org_cols

    up2 = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(WEB_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert up2.returncode == 0, up2.stdout + up2.stderr
    eng.dispose()
    eng = create_engine(PG_URL)
    cols = {c["name"] for c in inspect(eng).get_columns("counters")}
    assert "number_template" in cols
    org_cols = {c["name"] for c in inspect(eng).get_columns("organizations")}
    assert "onboarding" in org_cols
    assert "is_internal" in org_cols
    with eng.connect() as conn:
        ver = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert ver == "a50bc01"
