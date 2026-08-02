"""W-39: воронка заявок — полный цикл, дедуп, отклонение, повторный инвайт."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import select

from app.models import (
    Event,
    Invite,
    Lead,
    LeadStatus,
    Organization,
    Subscription,
    SubscriptionStatus,
    User,
    utcnow,
)
from app.services.billing import ensure_payment_settings, ensure_tariffs
from app.services.leads import create_lead, find_open_invite
from conftest import csrf_from, login


def _apply(client, email: str, *, comment: str = "", profile: str = "Другое"):
    token = csrf_from(client, "/")
    with patch("app.routers.landing.notify_admin_new_lead", return_value=True):
        return client.post(
            "/apply",
            data={
                "email": email,
                "profile": profile,
                "comment": comment,
                "csrf_token": token,
                "website": "",
            },
            follow_redirects=False,
        )


def _admin(client):
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303


def test_full_funnel_create_org_invite_accept(app):
    client, dbmod = app
    assert _apply(client, "funnel@example.com", comment="нужен доступ").status_code == 201

    _admin(client)
    r = client.get("/admin/leads")
    assert r.status_code == 200
    assert "funnel@example.com" in r.text
    assert "Создать организацию и пригласить" in r.text
    assert 'class="nav-badge"' in r.text or "nav-badge" in r.text

    token = csrf_from(client, "/admin/leads")
    with patch("app.services.leads.send_email", return_value=True) as mail:
        r = client.post(
            "/admin/leads/1/create-org",
            data={
                "csrf_token": token,
                "org_name": "Организация funnel@example.com",
                "tariff_code": "organization",
                "months": "3",
                "send_email_now": "1",
            },
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert mail.called

    db = dbmod.SessionLocal()
    try:
        lead = db.get(Lead, 1)
        assert lead is not None
        assert lead.status == LeadStatus.invited
        assert lead.org_id is not None
        org = db.get(Organization, lead.org_id)
        assert org is not None
        assert org.source_lead_id == lead.id
        sub = db.scalar(
            select(Subscription)
            .where(Subscription.org_id == org.id)
            .order_by(Subscription.id.desc())
        )
        assert sub is not None
        assert sub.is_beta is True
        assert sub.status == SubscriptionStatus.active
        invite = db.get(Invite, lead.invite_id)
        assert invite is not None
        assert invite.lead_id == lead.id
        assert invite.used_at is None
        inv_token = invite.token
        ev = db.scalar(select(Event).where(Event.type == "lead_invited"))
        assert ev is not None
    finally:
        db.close()

    client.post("/logout", data={"csrf_token": csrf_from(client, "/admin/")})
    # принять инвайт
    page = client.get(f"/invite/{inv_token}")
    assert page.status_code == 200
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
    assert r.headers["location"] == "/cabinet/"

    db = dbmod.SessionLocal()
    try:
        lead = db.get(Lead, 1)
        assert lead.status == LeadStatus.registered
        user = db.scalar(select(User).where(User.email == "funnel@example.com"))
        assert user is not None
        assert user.org_id == lead.org_id
        ev = db.scalar(select(Event).where(Event.type == "lead_registered"))
        assert ev is not None
    finally:
        db.close()


def test_dedup_repeat_application(app):
    client, dbmod = app
    assert _apply(client, "dup@example.com", comment="первый").status_code == 201
    assert _apply(client, "Dup@Example.com", comment="второй").status_code == 201

    db = dbmod.SessionLocal()
    try:
        rows = db.scalars(select(Lead).where(Lead.email == "dup@example.com")).all()
        assert len(rows) == 1
        assert rows[0].contact_count == 2
        assert "первый" in (rows[0].comment or "")
        assert "второй" in (rows[0].comment or "")
        bumped = db.scalar(select(Event).where(Event.type == "lead_bumped"))
        assert bumped is not None
    finally:
        db.close()


def test_dedup_existing_user_mark(app):
    client, dbmod = app
    from app.defaults import empty_requisites
    from app.security import hash_password

    db = dbmod.SessionLocal()
    try:
        org = Organization(name="Уже есть", requisites=empty_requisites())
        db.add(org)
        db.flush()
        db.add(
            User(
                org_id=org.id,
                email="exists@example.com",
                password_hash=hash_password("UserPass123!"),
                is_active=True,
            )
        )
        db.add(Lead(email="exists@example.com", profile="Другое", status=LeadStatus.new))
        db.commit()
    finally:
        db.close()

    _admin(client)
    r = client.get("/admin/leads")
    assert r.status_code == 200
    assert "Уже зарегистрирован" in r.text
    assert "Уже есть" in r.text


def test_dedup_open_invite_mark(app):
    client, dbmod = app
    from app.defaults import empty_requisites

    db = dbmod.SessionLocal()
    try:
        org = Organization(name="Орг инвайт", requisites=empty_requisites())
        db.add(org)
        db.flush()
        db.add(
            Invite(
                org_id=org.id,
                email="openinv@example.com",
                token="tok-open-w39",
                expires_at=utcnow() + timedelta(days=2),
            )
        )
        db.add(Lead(email="openinv@example.com", profile="Другое", status=LeadStatus.new))
        db.commit()
    finally:
        db.close()

    _admin(client)
    r = client.get("/admin/leads")
    assert "Инвайт уже отправлен" in r.text
    assert "Отправить инвайт повторно" in r.text


def test_reject_with_and_without_email(app):
    client, dbmod = app
    assert _apply(client, "reject1@example.com").status_code == 201
    assert _apply(client, "reject2@example.com").status_code == 201
    _admin(client)

    db = dbmod.SessionLocal()
    try:
        ids = {
            L.email: L.id
            for L in db.scalars(select(Lead).where(Lead.email.like("reject%"))).all()
        }
    finally:
        db.close()

    token = csrf_from(client, "/admin/leads")
    with patch("app.services.leads.send_email", return_value=True) as mail:
        r = client.post(
            f"/admin/leads/{ids['reject1@example.com']}/reject",
            data={"csrf_token": token, "send_mail": "1"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert mail.called
        token = csrf_from(client, "/admin/leads")
        mail.reset_mock()
        r = client.post(
            f"/admin/leads/{ids['reject2@example.com']}/reject",
            data={"csrf_token": token},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert not mail.called

    db = dbmod.SessionLocal()
    try:
        assert db.get(Lead, ids["reject1@example.com"]).status == LeadStatus.rejected
        assert db.get(Lead, ids["reject2@example.com"]).status == LeadStatus.rejected
    finally:
        db.close()


def test_resend_kills_old_token(app):
    client, dbmod = app
    assert _apply(client, "resend@example.com").status_code == 201
    _admin(client)
    token = csrf_from(client, "/admin/leads")
    with patch("app.services.leads.send_email", return_value=True):
        client.post(
            "/admin/leads/1/create-org",
            data={
                "csrf_token": token,
                "org_name": "Орг resend",
                "tariff_code": "organization",
                "months": "3",
                "send_email_now": "1",
            },
            follow_redirects=False,
        )

    db = dbmod.SessionLocal()
    try:
        lead = db.get(Lead, 1)
        old = db.get(Invite, lead.invite_id)
        old_token = old.token
        old_id = old.id
    finally:
        db.close()

    token = csrf_from(client, "/admin/leads")
    with patch("app.services.leads.send_email", return_value=True):
        r = client.post(
            "/admin/leads/1/resend",
            data={"csrf_token": token},
            follow_redirects=False,
        )
    assert r.status_code == 303

    db = dbmod.SessionLocal()
    try:
        old = db.get(Invite, old_id)
        assert old.is_expired()
        lead = db.get(Lead, 1)
        new_inv = db.get(Invite, lead.invite_id)
        assert new_inv is not None
        assert new_inv.token != old_token
        assert not new_inv.is_expired()
        # старый токен не принимается
        assert find_open_invite(db, "resend@example.com").id == new_inv.id
    finally:
        db.close()

    page = client.get(f"/invite/{old_token}")
    assert page.status_code == 404


def test_notify_once_with_deep_link(app, caplog):
    from app.config import get_settings
    from app.services.leads import notify_admin_new_lead

    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        lead, is_new = create_lead(db, email="once@x.z", profile="Другое", comment=None)
        assert is_new
        with caplog.at_level("INFO", logger="dok.mail"):
            assert notify_admin_new_lead(get_settings(), lead, db=db) is False
        assert "admin/leads#lead-" in caplog.text
        db.refresh(lead)
        assert lead.admin_notified_at is not None
        assert notify_admin_new_lead(get_settings(), lead, db=db) is False
    finally:
        db.close()


def test_spam_no_email(app):
    client, dbmod = app
    assert _apply(client, "spam@example.com").status_code == 201
    _admin(client)
    token = csrf_from(client, "/admin/leads")
    with patch("app.services.leads.send_email", return_value=True) as mail:
        r = client.post(
            "/admin/leads/1/spam",
            data={"csrf_token": token},
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert not mail.called
    db = dbmod.SessionLocal()
    try:
        assert db.get(Lead, 1).status == LeadStatus.spam
    finally:
        db.close()


def test_beta_defaults_in_payment_settings(app):
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        ensure_payment_settings(db)
        db.commit()
    finally:
        db.close()

    _admin(client)
    r = client.get("/admin/payment-settings")
    assert r.status_code == 200
    assert "Бета-доступ по умолчанию" in r.text
    token = csrf_from(client, "/admin/payment-settings")
    r = client.post(
        "/admin/payment-settings",
        data={
            "csrf_token": token,
            "terminal_key": "",
            "password": "",
            "mode": "test",
            "taxation": "usn_income",
            "vat_rate": "none",
            "default_receipt_email": "",
            "party_check_daily_limit": "100",
            "yandex_login_enabled": "1",
            "beta_default_tariff": "specialist",
            "beta_default_months": "6",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    db = dbmod.SessionLocal()
    try:
        row = ensure_payment_settings(db)
        assert row.beta_default_tariff == "specialist"
        assert row.beta_default_months == 6
    finally:
        db.close()


def test_org_detail_shows_source_lead(app):
    client, dbmod = app
    assert _apply(client, "srclead@example.com").status_code == 201
    _admin(client)
    token = csrf_from(client, "/admin/leads")
    with patch("app.services.leads.send_email", return_value=True):
        client.post(
            "/admin/leads/1/create-org",
            data={
                "csrf_token": token,
                "org_name": "Из заявки",
                "tariff_code": "organization",
                "months": "3",
                "send_email_now": "1",
            },
            follow_redirects=False,
        )
    db = dbmod.SessionLocal()
    try:
        org_id = db.get(Lead, 1).org_id
    finally:
        db.close()
    r = client.get(f"/admin/organizations/{org_id}")
    assert r.status_code == 200
    assert "Из заявки" in r.text
    assert "srclead@example.com" in r.text
    assert "/admin/leads#lead-1" in r.text
