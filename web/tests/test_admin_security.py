"""Безопасность учётки администратора сервиса (/admin/security/)."""

from __future__ import annotations

import re

from sqlalchemy import select

from app.models import Event, User
from app.navigation import NAV_REGISTRY, NavRole
from app.security import verify_password
from conftest import login


def _csrf(html: str) -> str:
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m
    return m.group(1)


def test_admin_security_nav_registered():
    item = next(i for i in NAV_REGISTRY if i.key == "admin_security")
    assert item.url == "/admin/security/"
    assert NavRole.service_admin in item.roles
    assert item.area == "admin"


def test_admin_security_page_and_password_change(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303

    r = client.get("/admin/security/")
    assert r.status_code == 200
    assert "Безопасность владельца" in r.text
    assert "Сменить пароль" in r.text or "Пароль" in r.text
    assert "Двухфакторная" in r.text
    assert 'action="/admin/security/password"' in r.text
    assert 'action="/admin/security/2fa/start"' in r.text
    # retention — только для org-кабинета
    assert "срок_дней_файлов" not in r.text

    # ссылки из единого окна / обзора
    home = client.get("/admin/")
    assert home.status_code == 200
    assert 'href="/admin/security/"' in home.text
    cms = client.get("/admin/cms/")
    assert cms.status_code == 200
    assert "/admin/security/" in cms.text

    csrf = _csrf(r.text)
    new_password = "N3w-Secure-Passw0rd!"
    r = client.post(
        "/admin/security/password",
        data={
            "csrf_token": csrf,
            "current_password": "AdminPass123!",
            "password": new_password,
            "password2": new_password,
        },
        follow_redirects=False,
    )
    assert r.status_code == 200, r.text[:500]
    assert "Пароль сохранён" in r.text

    db = dbmod.SessionLocal()
    try:
        admin = db.scalar(select(User).where(User.email == "admin@dok.moscow"))
        assert admin is not None
        assert verify_password(new_password, admin.password_hash)
        ev = db.scalars(
            select(Event).where(Event.type == "password_set", Event.user_id == admin.id)
        ).all()
        assert ev
        assert any((e.details or {}).get("surface") == "admin" for e in ev)
    finally:
        db.close()

    # вход новым паролем
    client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)
    assert login(client, "admin@dok.moscow", new_password).status_code == 303


def test_org_user_cannot_open_admin_security(app):
    client, dbmod = app
    from app.defaults import empty_requisites
    from app.models import Organization, UserRole
    from app.security import hash_password

    db = dbmod.SessionLocal()
    try:
        org = Organization(name="ООО X", requisites=empty_requisites())
        db.add(org)
        db.flush()
        db.add(
            User(
                org_id=org.id,
                email="mem-sec@example.com",
                password_hash=hash_password("Passw0rd!"),
                role=UserRole.user,
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    assert login(client, "mem-sec@example.com", "Passw0rd!").status_code == 303
    r = client.get("/admin/security/", follow_redirects=False)
    assert r.status_code in (303, 403)
