"""W-50.1 §2: легаси-инвайт с org_id / purged org / pending_org."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from conftest import csrf_from, login
from sqlalchemy import func, select

from app.defaults import empty_requisites
from app.models import (
    Invite,
    Lead,
    LeadStatus,
    Organization,
    OrgRole,
    User,
    UserRole,
    utcnow,
)


def _apply(client, email: str):
    token = csrf_from(client, "/")
    with patch("app.routers.landing.notify_admin_new_lead", return_value=True):
        return client.post(
            "/apply",
            data={
                "email": email,
                "profile": "Другое",
                "comment": "",
                "csrf_token": token,
                "website": "",
            },
            follow_redirects=False,
        )


def test_legacy_invite_joins_existing_org(app):
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="Легаси орг", requisites=empty_requisites())
        db.add(org)
        db.flush()
        # уже есть один пользователь
        db.add(
            User(
                org_id=org.id,
                email="owner@legacy.example",
                password_hash="x",
                role=UserRole.user,
                org_role=OrgRole.org_admin,
                is_active=True,
            )
        )
        inv = Invite(
            org_id=org.id,
            email="join@legacy.example",
            token="legacy-join-token",
            expires_at=utcnow() + timedelta(days=2),
            is_active=True,
            status="active",
        )
        db.add(inv)
        db.commit()
        org_id = org.id
        orgs_before = int(db.scalar(select(func.count()).select_from(Organization)) or 0)
    finally:
        db.close()

    csrf = csrf_from(client, "/invite/legacy-join-token")
    r = client.post(
        "/invite/legacy-join-token",
        data={
            "csrf_token": csrf,
            "password": "UserPass123!",
            "password2": "UserPass123!",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    db = dbmod.SessionLocal()
    try:
        orgs_after = int(db.scalar(select(func.count()).select_from(Organization)) or 0)
        assert orgs_after == orgs_before
        user = db.scalar(select(User).where(User.email == "join@legacy.example"))
        assert user is not None
        assert user.org_id == org_id
        assert user.org_role == OrgRole.org_member
        inv = db.scalar(select(Invite).where(Invite.token == "legacy-join-token"))
        assert inv is not None
        assert inv.status == "accepted"
        assert inv.is_active is False
        assert inv.used_at is not None
    finally:
        db.close()


def test_legacy_invite_purged_org_expires(app):
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="Будет удалена", requisites=empty_requisites())
        db.add(org)
        db.flush()
        inv = Invite(
            org_id=org.id,
            email="gone@legacy.example",
            token="legacy-gone-token",
            expires_at=utcnow() + timedelta(days=2),
            is_active=True,
            status="active",
        )
        db.add(inv)
        db.commit()
        org_id = org.id
        # Имитация dangling org_id после purge (без CASCADE на инвайт)
        from sqlalchemy import text

        db.execute(text("PRAGMA foreign_keys=OFF"))
        db.execute(text("DELETE FROM organizations WHERE id = :id").bindparams(id=org_id))
        db.execute(text("PRAGMA foreign_keys=ON"))
        db.commit()
        assert db.get(Organization, org_id) is None
        inv2 = db.scalar(select(Invite).where(Invite.token == "legacy-gone-token"))
        assert inv2 is not None
        assert inv2.org_id == org_id
    finally:
        db.close()

    r = client.get("/invite/legacy-gone-token")
    assert r.status_code == 404
    assert "устарело" in r.text.lower()

    db = dbmod.SessionLocal()
    try:
        inv = db.scalar(select(Invite).where(Invite.token == "legacy-gone-token"))
        assert inv is not None
        assert inv.status == "expired"
        assert inv.is_active is False
    finally:
        db.close()


def test_pending_org_invite_still_creates_org(app):
    client, dbmod = app
    assert _apply(client, "pending50@example.com").status_code == 201
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    token = csrf_from(client, "/admin/leads")
    with patch("app.services.leads.send_email", return_value=True):
        r = client.post(
            "/admin/leads/1/create-org",
            data={
                "csrf_token": token,
                "org_name": "Орг pending50",
                "tariff_code": "organization",
                "months": "3",
                "send_email_now": "1",
            },
            follow_redirects=False,
        )
    assert r.status_code == 303

    db = dbmod.SessionLocal()
    try:
        lead = db.get(Lead, 1)
        inv = db.get(Invite, lead.invite_id)
        inv_token = inv.token
        assert inv.org_id is None
        orgs_before = int(db.scalar(select(func.count()).select_from(Organization)) or 0)
    finally:
        db.close()

    client.post("/logout", data={"csrf_token": csrf_from(client, "/admin/")})
    csrf = csrf_from(client, f"/invite/{inv_token}")
    r = client.post(
        f"/invite/{inv_token}",
        data={
            "csrf_token": csrf,
            "password": "UserPass123!",
            "password2": "UserPass123!",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    db = dbmod.SessionLocal()
    try:
        orgs_after = int(db.scalar(select(func.count()).select_from(Organization)) or 0)
        assert orgs_after == orgs_before + 1
        lead = db.get(Lead, 1)
        assert lead.status == LeadStatus.registered
        inv = db.get(Invite, lead.invite_id)
        assert inv.status == "accepted"
        assert inv.is_active is False
    finally:
        db.close()
