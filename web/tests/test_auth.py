"""W-01: аутентификация, инвайты, CSRF, изоляция org_id."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models import Invite, Organization, User, UserRole, utcnow
from app.security import hash_password, new_invite_token
from conftest import csrf_from, login


def test_security_headers(app):
    client, _ = app
    r = client.get("/login")
    assert r.status_code == 200
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert "content-security-policy" in {k.lower() for k in r.headers.keys()}
    assert "Док.Москва" in r.text


def test_login_csrf_required(app):
    client, _ = app
    r = client.post(
        "/login",
        data={"email": "admin@dok.moscow", "password": "AdminPass123!"},
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_admin_login_and_invite_flow(app):
    client, dbmod = app
    r = login(client, "admin@dok.moscow", "AdminPass123!")
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/"

    token = csrf_from(client, "/admin/")
    r = client.post(
        "/admin/organizations",
        data={"name": "УСЭ Тест", "csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 303

    db = dbmod.SessionLocal()
    try:
        org = db.scalar(select(Organization).where(Organization.name == "УСЭ Тест"))
        assert org is not None
        org_id = org.id
    finally:
        db.close()

    token = csrf_from(client, "/admin/")
    r = client.post(
        "/admin/invites",
        data={"org_id": org_id, "email": "user@example.com", "csrf_token": token},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "Приглашение создано" in r.text
    assert "/invite/" in r.text

    db = dbmod.SessionLocal()
    try:
        invite = db.scalar(select(Invite).where(Invite.email == "user@example.com"))
        assert invite is not None
        invite_token = invite.token
    finally:
        db.close()

    # выход админа
    token = csrf_from(client, "/admin/")
    client.post("/logout", data={"csrf_token": token}, follow_redirects=False)

    r = client.get(f"/invite/{invite_token}")
    assert r.status_code == 200
    assert "user@example.com" in r.text

    csrf = csrf_from(client, f"/invite/{invite_token}")
    r = client.post(
        f"/invite/{invite_token}",
        data={"password": "InvitePass123!", "password2": "InvitePass123!", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cabinet/"

    r = client.get("/cabinet/")
    assert r.status_code == 200
    assert "Документы" in r.text
    assert "УСЭ Тест" in r.text
    assert "Контрагенты" in r.text


def test_org_isolation(app):
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        org_a = Organization(name="Орг А", requisites={})
        org_b = Organization(name="Орг Б", requisites={})
        db.add_all([org_a, org_b])
        db.flush()
        user_a = User(
            org_id=org_a.id,
            email="a@example.com",
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
        )
        user_b = User(
            org_id=org_b.id,
            email="b@example.com",
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
        )
        db.add_all([user_a, user_b])
        db.commit()
        a_org, b_org = org_a.id, org_b.id
    finally:
        db.close()

    assert login(client, "a@example.com", "Passw0rd!").status_code == 303
    ok = client.get(f"/cabinet/org/{a_org}")
    assert ok.status_code == 200
    forbidden = client.get(f"/cabinet/org/{b_org}")
    assert forbidden.status_code == 404


def test_login_rate_limit(app):
    client, _ = app
    for _ in range(5):
        token = csrf_from(client, "/login")
        r = client.post(
            "/login",
            data={"email": "nobody@example.com", "password": "wrong", "csrf_token": token},
            follow_redirects=False,
        )
        assert r.status_code == 401

    token = csrf_from(client, "/login")
    r = client.post(
        "/login",
        data={"email": "nobody@example.com", "password": "wrong", "csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 429


def test_expired_invite(app):
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="Орг", requisites={})
        db.add(org)
        db.flush()
        token = new_invite_token()
        invite = Invite(
            org_id=org.id,
            email="late@example.com",
            token=token,
            expires_at=utcnow() - timedelta(hours=1),
        )
        db.add(invite)
        db.commit()
    finally:
        db.close()

    r = client.get(f"/invite/{token}")
    assert r.status_code == 404
