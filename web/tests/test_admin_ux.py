"""Расширенная админка и быстрые проверки UX."""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import select

from app.defaults import empty_requisites
from app.models import Invite, Lead, Organization, User, UserRole
from app.security import hash_password
from conftest import csrf_from, login


def test_admin_sections_and_org_detail(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303

    for path in (
        "/admin/",
        "/admin/organizations",
        "/admin/users",
        "/admin/leads",
        "/admin/invites",
        "/admin/status",
        "/admin/legal/",
    ):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "Администратор сервиса" in r.text

    token = csrf_from(client, "/admin/organizations")
    r = client.post(
        "/admin/organizations",
        data={"name": "Орг Деталь", "csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/admin/organizations/")
    r = client.get(loc)
    assert r.status_code == 200
    assert "Орг Деталь" in r.text
    assert "Пользователи" in r.text


def test_admin_revoke_invite_and_toggle_user(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303

    db = dbmod.SessionLocal()
    try:
        org = Organization(name="ToggleOrg", requisites=empty_requisites())
        db.add(org)
        db.flush()
        u = User(
            org_id=org.id,
            email="toggle@example.com",
            password_hash=hash_password("TogglePass1"),
            role=UserRole.user,
            is_active=True,
        )
        db.add(u)
        db.commit()
        uid = u.id
    finally:
        db.close()

    db = dbmod.SessionLocal()
    try:
        org = db.scalar(select(Organization).where(Organization.name == "ToggleOrg"))
        org_id = org.id
    finally:
        db.close()

    token = csrf_from(client, "/admin/invites")
    r = client.post(
        "/admin/invites",
        data={"org_id": org_id, "email": "new@example.com", "csrf_token": token},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "Приглашение создано" in r.text

    db = dbmod.SessionLocal()
    try:
        inv = db.scalar(select(Invite).where(Invite.email == "new@example.com"))
        inv_id = inv.id
    finally:
        db.close()

    token = csrf_from(client, "/admin/invites")
    r = client.post(
        f"/admin/invites/{inv_id}/revoke",
        data={"csrf_token": token},
        follow_redirects=True,
    )
    assert r.status_code == 200
    db = dbmod.SessionLocal()
    try:
        inv = db.get(Invite, inv_id)
        assert inv.is_expired()
    finally:
        db.close()

    token = csrf_from(client, "/admin/users")
    r = client.post(
        f"/admin/users/{uid}/toggle",
        data={"csrf_token": token},
        follow_redirects=True,
    )
    assert r.status_code == 200
    db = dbmod.SessionLocal()
    try:
        assert db.get(User, uid).is_active is False
    finally:
        db.close()


def test_invite_from_lead(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="LeadOrg", requisites=empty_requisites())
        db.add(org)
        db.add(Lead(email="lead2@example.com", profile="Другое", comment="хочу пилот"))
        db.commit()
        org_id = org.id
        lead_id = db.scalar(select(Lead).where(Lead.email == "lead2@example.com")).id
    finally:
        db.close()

    token = csrf_from(client, "/admin/leads")
    with patch("app.services.leads.send_email", return_value=True):
        r = client.post(
            f"/admin/leads/{lead_id}/invite",
            data={"org_id": org_id, "csrf_token": token, "send_email_now": "1"},
            follow_redirects=True,
        )
    assert r.status_code == 200
    assert "lead2@example.com" in r.text
    assert "приглашение отправлено" in r.text.lower() or "Инвайт" in r.text


def test_htmx_self_hosted_and_boost(app):
    client, _ = app
    r = client.get("/static/htmx.min.js")
    assert r.status_code == 200
    assert len(r.content) > 1000
    r = client.get("/login")
    assert 'src="/static/htmx.min.js"' in r.text
    assert 'hx-boost="true"' in r.text
    assert "unpkg.com/htmx" not in r.text
