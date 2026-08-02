"""Пользовательский порядок пунктов бокового меню."""

from __future__ import annotations

from sqlalchemy import select

from app.models import Organization, User, UserRole
from app.nav_context import admin_nav, cabinet_nav
from app.security import hash_password
from app.services.nav_order import apply_nav_order, save_nav_order
from conftest import csrf_from, login


def _org_user(dbmod, email: str = "nav@example.com"):
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="Орг Nav", requisites={})
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id,
            email=email,
            password_hash=hash_password("UserPass123!"),
            role=UserRole.user,
            is_active=True,
        )
        db.add(user)
        db.commit()
        return user.id, email
    finally:
        db.close()


def test_apply_nav_order_puts_preferred_first():
    items = [
        ("documents", "Документы", "/cabinet/documents/"),
        ("calendar", "Календарь", "/cabinet/calendar/"),
        ("settings", "Настройки", "/cabinet/settings/"),
    ]
    ordered = apply_nav_order(items, ["calendar", "settings", "documents"])
    assert [k for k, *_ in ordered] == ["calendar", "settings", "documents"]


def test_apply_nav_order_keeps_unknown_new_keys_at_end():
    items = [
        ("documents", "Документы", "/d"),
        ("calendar", "Календарь", "/c"),
        ("party_check", "Проверка", "/p"),
    ]
    ordered = apply_nav_order(items, ["calendar"])
    assert [k for k, *_ in ordered] == ["calendar", "documents", "party_check"]


def test_cabinet_menu_respects_saved_order(app):
    client, dbmod = app
    user_id, email = _org_user(dbmod)
    db = dbmod.SessionLocal()
    try:
        save_nav_order(
            db,
            user_id,
            "cabinet",
            ["calendar", "documents", "counterparties", "journal", "settings", "package", "billing"],
        )
        from app.deps import CurrentUser
        from app.models import User as U

        u = db.get(U, user_id)
        cu = CurrentUser(
            id=u.id,
            email=u.email,
            org_id=u.org_id,
            role=u.role,
            is_active=u.is_active,
            nav_order=u.nav_order,
        )
        nav = cabinet_nav(db, cu)
        keys = [k for k, *_ in nav]
        assert keys[0] == "calendar"
        assert "documents" in keys
    finally:
        db.close()

    assert login(client, email, "UserPass123!").status_code == 303
    r = client.get("/cabinet/calendar/")
    assert r.status_code == 200
    # календарь раньше документов в разметке меню
    cal = r.text.find('data-nav-key="calendar"')
    docs = r.text.find('data-nav-key="documents"')
    assert cal != -1 and docs != -1
    assert cal < docs
    assert "data-nav-sortable" in r.text
    assert "nav-sortable.js" in r.text
    assert 'class="nav-handle"' in r.text
    assert "<button" not in r.text.split("data-nav-sortable", 1)[1].split("</nav>", 1)[0]


def test_api_saves_nav_order(app):
    client, dbmod = app
    _user_id, email = _org_user(dbmod, "navapi@example.com")
    assert login(client, email, "UserPass123!").status_code == 303
    token = csrf_from(client, "/cabinet/calendar/")
    r = client.post(
        "/api/nav-order",
        headers={"X-CSRF-Token": token, "Content-Type": "application/json"},
        json={
            "area": "cabinet",
            "order": ["settings", "calendar", "documents"],
        },
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["order"][0] == "settings"

    db = dbmod.SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == email))
        assert user.nav_order["cabinet"][0] == "settings"
    finally:
        db.close()

    r = client.get("/cabinet/settings/security")
    assert r.text.find('data-nav-key="settings"') < r.text.find('data-nav-key="calendar"')


def test_admin_nav_order(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    token = csrf_from(client, "/admin/")
    r = client.post(
        "/api/nav-order",
        headers={"X-CSRF-Token": token, "Content-Type": "application/json"},
        json={"area": "admin", "order": ["admin_users", "admin_home", "admin_orgs"]},
    )
    assert r.status_code == 200

    db = dbmod.SessionLocal()
    try:
        admin = db.scalar(select(User).where(User.email == "admin@dok.moscow"))
        from app.deps import CurrentUser

        cu = CurrentUser(
            id=admin.id,
            email=admin.email,
            org_id=admin.org_id,
            role=admin.role,
            is_active=admin.is_active,
            nav_order=admin.nav_order,
        )
        keys = [k for k, *_ in admin_nav(cu)]
        assert keys[0] == "admin_users"
    finally:
        db.close()

    r = client.get("/admin/users")
    assert r.status_code == 200
    assert r.text.find('data-nav-key="admin_users"') < r.text.find('data-nav-key="admin_home"')


def test_api_rejects_cabinet_order_for_admin_without_org(app):
    client, _dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    token = csrf_from(client, "/admin/")
    r = client.post(
        "/api/nav-order",
        headers={"X-CSRF-Token": token, "Content-Type": "application/json"},
        json={"area": "cabinet", "order": ["documents"]},
    )
    assert r.status_code == 403
