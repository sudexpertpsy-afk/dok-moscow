"""W-28: дашборд кабинета."""

from __future__ import annotations

import time

from app.models import Organization, User, UserRole
from app.security import hash_password
from app.services.dashboard import load_dashboard
from conftest import login


def _seed(dbmod):
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="Dash Org", requisites={})
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id,
            email="dash28@example.com",
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
            is_active=True,
        )
        db.add(user)
        db.commit()
        return org.id
    finally:
        db.close()


def test_cabinet_home_is_dashboard(app):
    client, dbmod = app
    _seed(dbmod)
    assert login(client, "dash28@example.com", "Passw0rd!").status_code == 303
    r = client.get("/cabinet/", follow_redirects=False)
    assert r.status_code == 200
    assert "Требуют внимания" in r.text
    assert "Новый комплект" in r.text
    assert "Как пользоваться" in r.text
    assert 'href="/cabinet/help/"' in r.text
    assert "С чего начать" in r.text or "документов за месяц" in r.text


def test_dashboard_query_budget(app):
    client, dbmod = app
    org_id = _seed(dbmod)
    db = dbmod.SessionLocal()
    try:
        t0 = time.perf_counter()
        data = load_dashboard(db, org_id)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 300
        assert data.docs_month == 0
        assert data.empty_org
    finally:
        db.close()
