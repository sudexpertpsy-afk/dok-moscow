"""T1: create_all только при DB_AUTO_CREATE; схема иначе через Alembic / фикстуры."""

from __future__ import annotations

import os

from app.config import get_settings
from app.db import Base


def test_db_auto_create_default_false():
    get_settings.cache_clear()
    # В тестах conftest не задаёт DB_AUTO_CREATE → False
    assert get_settings().db_auto_create is False


def _prepare_sqlite_env(tmp_path, monkeypatch, *, auto_create: str) -> None:
    db_path = tmp_path / "t1.db"
    monkeypatch.setenv("DB_URL", f"sqlite+pysqlite:///{db_path}")
    monkeypatch.setenv("DB_AUTO_CREATE", auto_create)
    monkeypatch.setenv("SECRET_KEY", "test-secret-t1")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@dok.moscow")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "AdminPass123!")
    monkeypatch.setenv("FILES_ROOT", str(tmp_path / "files"))
    monkeypatch.setenv("JOBS_INLINE", "1")
    monkeypatch.setenv("BILLING_WORKER", "0")
    get_settings.cache_clear()

    from app import db as dbmod

    dbmod.engine.dispose()
    dbmod.reset_engine(os.environ["DB_URL"])


def test_lifespan_skips_create_all_when_disabled(tmp_path, monkeypatch):
    _prepare_sqlite_env(tmp_path, monkeypatch, auto_create="0")

    from app import db as dbmod

    # Схема заранее (как на проде после alembic / как в conftest)
    Base.metadata.create_all(bind=dbmod.engine)

    calls: list[int] = []
    real = Base.metadata.create_all

    def spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(Base.metadata, "create_all", spy)

    from app.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as _:
        pass

    assert calls == []
    get_settings.cache_clear()


def test_lifespan_calls_create_all_when_enabled(tmp_path, monkeypatch):
    _prepare_sqlite_env(tmp_path, monkeypatch, auto_create="1")

    calls: list[int] = []
    real = Base.metadata.create_all

    def spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(Base.metadata, "create_all", spy)

    from app.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as _:
        pass

    assert calls == [1]
    get_settings.cache_clear()
