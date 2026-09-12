"""W-50 B.2/B.3: идемпотентный инвайт без org до accept + purge пустых org."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import func, select

from app.defaults import empty_requisites
from app.models import (
    Document,
    DocumentFormat,
    Event,
    Invite,
    Lead,
    LeadStatus,
    OrgRole,
    Organization,
    User,
    utcnow,
)
from app.services.org_purge import can_purge_org, purge_org
from app.services.safe_paths import org_files_root
from conftest import csrf_from, login


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


def _admin(client):
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303


def _create_org_invite(client, lead_id: int, *, org_name: str):
    token = csrf_from(client, "/admin/leads")
    with patch("app.services.leads.send_email", return_value=True):
        return client.post(
            f"/admin/leads/{lead_id}/create-org",
            data={
                "csrf_token": token,
                "org_name": org_name,
                "tariff_code": "organization",
                "months": "3",
                "send_email_now": "1",
            },
            follow_redirects=False,
        )


def test_two_invites_same_email_resent_not_second_org(app):
    client, dbmod = app
    assert _apply(client, "idem@example.com").status_code == 201
    _admin(client)

    assert _create_org_invite(client, 1, org_name="Орг idem").status_code == 303
    db = dbmod.SessionLocal()
    try:
        lead = db.get(Lead, 1)
        inv1 = db.get(Invite, lead.invite_id)
        token1 = inv1.token
        inv_id = inv1.id
        assert inv1.org_id is None
        assert lead.org_id is None
        orgs_before = int(db.scalar(select(func.count()).select_from(Organization)) or 0)
    finally:
        db.close()

    assert _create_org_invite(client, 1, org_name="Орг idem v2").status_code == 303

    db = dbmod.SessionLocal()
    try:
        lead = db.get(Lead, 1)
        inv2 = db.get(Invite, lead.invite_id)
        assert inv2.id == inv_id
        assert inv2.token != token1
        assert inv2.pending_org_name == "Орг idem v2"
        assert lead.org_id is None
        orgs_after = int(db.scalar(select(func.count()).select_from(Organization)) or 0)
        assert orgs_after == orgs_before
        resent = db.scalar(select(Event).where(Event.type == "invite.resent"))
        assert resent is not None
        assert client.get(f"/invite/{token1}").status_code == 404
    finally:
        db.close()


def test_accept_creates_org_user_atomically(app):
    client, dbmod = app
    assert _apply(client, "accept50@example.com").status_code == 201
    _admin(client)
    assert _create_org_invite(client, 1, org_name="Орг accept50").status_code == 303

    db = dbmod.SessionLocal()
    try:
        lead = db.get(Lead, 1)
        assert lead.org_id is None
        inv = db.get(Invite, lead.invite_id)
        inv_token = inv.token
        assert inv.org_id is None
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
        lead = db.get(Lead, 1)
        assert lead.status == LeadStatus.registered
        assert lead.org_id is not None
        org = db.get(Organization, lead.org_id)
        assert org is not None
        assert org.name == "Орг accept50"
        user = db.scalar(select(User).where(User.email == "accept50@example.com"))
        assert user is not None
        assert user.org_id == org.id
        assert user.org_role == OrgRole.org_admin
        inv = db.get(Invite, lead.invite_id)
        assert inv.org_id == org.id
        assert inv.used_at is not None
    finally:
        db.close()


def test_can_purge_rejects_with_documents(app):
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="С документами", requisites=empty_requisites())
        db.add(org)
        db.flush()
        db.add(
            Document(
                org_id=org.id,
                template="t",
                file_path=f"{org.id}/x.docx",
                format=DocumentFormat.docx,
            )
        )
        org.created_at = utcnow() - timedelta(days=10)
        db.commit()
        org_id = org.id
        ok, reason = can_purge_org(db, org)
        assert ok is False
        assert "документ" in reason.lower()
    finally:
        db.close()

    _admin(client)
    r = client.get(f"/admin/organizations/{org_id}")
    assert r.status_code == 200
    assert "Удаление недоступно" in r.text
    assert "документ" in r.text.lower()


def test_purge_succeeds_when_empty_and_old(app):
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="Пустая старая", requisites=empty_requisites())
        db.add(org)
        db.flush()
        org.created_at = utcnow() - timedelta(days=10)
        db.commit()
        org_id = org.id
    finally:
        db.close()

    files_dir = org_files_root(org_id)
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / "marker.txt").write_text("x", encoding="utf-8")

    db = dbmod.SessionLocal()
    try:
        org = db.get(Organization, org_id)
        ok, reason = can_purge_org(db, org)
        assert ok is True, reason
        admin = db.scalar(select(User).where(User.email == "admin@dok.moscow"))
        purge_org(db, org, actor_id=admin.id, reason="empty")
    finally:
        db.close()

    assert not files_dir.exists()
    db = dbmod.SessionLocal()
    try:
        assert db.get(Organization, org_id) is None
        ev = db.scalar(select(Event).where(Event.type == "org.purged"))
        assert ev is not None
        assert (ev.details or {}).get("org_id") == org_id
        assert (ev.details or {}).get("reason") == "empty"
    finally:
        db.close()

    db = dbmod.SessionLocal()
    try:
        young = Organization(name="Молодая", requisites=empty_requisites())
        db.add(young)
        db.flush()
        young.created_at = utcnow() - timedelta(days=2)
        db.commit()
        ok, reason = can_purge_org(db, young)
        assert ok is False
        assert "7" in reason
    finally:
        db.close()
