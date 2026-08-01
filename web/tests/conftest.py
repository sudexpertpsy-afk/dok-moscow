"""Тестовые фикстуры веб-приложения."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

# web/ в PYTHONPATH
WEB_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB_ROOT))

os.environ["SECRET_KEY"] = "test-secret-key-w01"
os.environ["DB_URL"] = "sqlite+pysqlite:////tmp/dok_moscow_test.db"
os.environ["BOOTSTRAP_ADMIN_EMAIL"] = "admin@dok.moscow"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "AdminPass123!"
os.environ["FILES_ROOT"] = "/tmp/dok_files_test"

from app.config import get_settings  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Invite, Organization, User, UserRole  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.routers import landing as landing_router  # noqa: E402
from app.security import hash_password  # noqa: E402


@pytest.fixture()
def app(tmp_path):
    get_settings.cache_clear()
    db_path = tmp_path / "test.db"
    os.environ["DB_URL"] = f"sqlite+pysqlite:///{db_path}"
    os.environ["SECRET_KEY"] = "test-secret-key-w01"
    os.environ["BOOTSTRAP_ADMIN_EMAIL"] = "admin@dok.moscow"
    os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "AdminPass123!"
    os.environ["FILES_ROOT"] = str(tmp_path / "files")
    get_settings.cache_clear()

    # пересоздать engine на новый файл
    from app import db as dbmod

    dbmod.engine.dispose()
    dbmod.reset_engine(os.environ["DB_URL"])
    # sync module-level engine used by main.create_all — recreate app after reset
    Base.metadata.drop_all(bind=dbmod.engine)
    Base.metadata.create_all(bind=dbmod.engine)

    application = create_app()
    # create_all again via startup; TestClient triggers lifespan/startup
    auth_router.login_limiter.clear()
    auth_router.reset_limiter.clear()
    landing_router.lead_limiter.clear()
    with TestClient(application) as client:
        # гарантируем bootstrap
        db = dbmod.SessionLocal()
        try:
            admin = db.scalar(select(User).where(User.email == "admin@dok.moscow"))
            if admin is None:
                db.add(
                    User(
                        org_id=None,
                        email="admin@dok.moscow",
                        password_hash=hash_password("AdminPass123!"),
                        role=UserRole.service_admin,
                        is_active=True,
                    )
                )
                db.commit()
        finally:
            db.close()
        yield client, dbmod
    auth_router.login_limiter.clear()
    auth_router.reset_limiter.clear()
    landing_router.lead_limiter.clear()


def csrf_from(client: TestClient, path: str = "/login") -> str:
    r = client.get(path)
    assert r.status_code == 200
    # из HTML hidden field
    marker = 'name="csrf_token" value="'
    assert marker in r.text
    return r.text.split(marker, 1)[1].split('"', 1)[0]


def login(client: TestClient, email: str, password: str):
    token = csrf_from(client, "/login")
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": token},
        follow_redirects=False,
    )
