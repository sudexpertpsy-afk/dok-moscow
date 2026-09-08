"""W-47: self-serve signup → billing."""

from __future__ import annotations

import uuid
from unittest.mock import patch

from sqlalchemy import select

from app.models import (
    Lead,
    LeadStatus,
    Organization,
    Payment,
    PaymentSource,
    PaymentStatus,
    Subscription,
    Tariff,
    TariffCode,
    User,
)
from conftest import csrf_from


def _signup(
    client,
    *,
    email,
    password="SecurePass1!",
    tariff="specialist",
    period="month",
    org_name="",
    accept=True,
    website="",
):
    token = csrf_from(client, "/signup")
    data = {
        "csrf_token": token,
        "email": email,
        "password": password,
        "tariff": tariff,
        "period": period,
        "org_name": org_name,
        "website": website,
    }
    if accept:
        data["accept_terms"] = "1"
    with (
        patch("app.services.leads.notify_admin_self_serve_signup", return_value=True),
        patch("app.services.email_verify.send_email", return_value=True),
    ):
        return client.post("/signup", data=data, follow_redirects=False)


def test_signup_creates_org_user_lead_guest(app):
    client, dbmod = app
    r = _signup(client, email="new@example.com", tariff="organization", period="year")
    assert r.status_code == 303
    assert "/cabinet/billing/" in r.headers["location"]
    assert "tariff=organization" in r.headers["location"]
    assert "period=year" in r.headers["location"]
    assert "welcome=1" in r.headers["location"]

    db = dbmod.SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "new@example.com"))
        assert user is not None
        assert user.org_id is not None
        assert user.email_verified is False
        assert user.password_hash
        org = db.get(Organization, user.org_id)
        assert org is not None
        lead = db.scalar(select(Lead).where(Lead.email == "new@example.com"))
        assert lead is not None
        assert lead.status == LeadStatus.signed_up
        assert lead.org_id == org.id
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org.id))
        assert sub is not None
        assert sub.tariff.code == TariffCode.guest
        assert sub.is_complimentary is False
        assert sub.is_beta is False
    finally:
        db.close()


def test_signup_duplicate_email_redirects_login(app):
    client, dbmod = app
    _signup(client, email="dup@example.com")
    r = _signup(client, email="dup@example.com")
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/login?")
    assert "next=" in loc
    assert "billing" in loc

    db = dbmod.SessionLocal()
    try:
        users = list(db.scalars(select(User).where(User.email == "dup@example.com")).all())
        assert len(users) == 1
    finally:
        db.close()


def test_signup_requires_terms(app):
    client, _ = app
    r = _signup(client, email="noterms@example.com", accept=False)
    assert r.status_code == 400
    assert "оферту" in r.text.lower() or "политику" in r.text.lower()


def test_signup_rate_limit(app):
    client, _ = app
    from app.routers.signup import signup_limiter

    with patch("app.routers.signup.client_ip", return_value="9.9.9.9"):
        signup_limiter.reset("ip:9.9.9.9")
        for i in range(5):
            _signup(client, email=f"rl{i}@example.com")
        r = _signup(client, email="rl5@example.com")
        assert r.status_code == 429


def test_billing_preselect_and_welcome(app):
    client, _ = app
    _signup(client, email="bill@example.com", tariff="specialist", period="year")
    r = client.get("/cabinet/billing/?tariff=specialist&period=year&welcome=1")
    assert r.status_code == 200
    assert "Кабинет создан" in r.text
    assert 'value="specialist"' in r.text


def test_landing_no_password_fields(app):
    client, _ = app
    r = client.get("/")
    assert r.status_code == 200
    assert 'type="password"' not in r.text
    assert 'action="/signup"' not in r.text
    assert "/signup?tariff=" in r.text
    r2 = client.get("/tariffs")
    assert r2.status_code == 200
    assert 'type="password"' not in r2.text


def test_unverified_blocks_password_reset_not_pay(app):
    client, dbmod = app
    _signup(client, email="uv@example.com")
    db = dbmod.SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "uv@example.com"))
        assert user.email_verified is False
    finally:
        db.close()

    token = csrf_from(client, "/forgot-password")
    with patch("app.routers.auth.send_email") as mail:
        r = client.post(
            "/forgot-password",
            data={"csrf_token": token, "email": "uv@example.com"},
            follow_redirects=False,
        )
        assert r.status_code == 200
        mail.assert_not_called()

    r = client.get("/cabinet/billing/")
    assert r.status_code == 200
    assert 'action="/cabinet/billing/pay"' in r.text


def test_unverified_blocks_staff_invite(app):
    client, dbmod = app
    _signup(client, email="adminuv@example.com", tariff="organization")
    from app.services.billing import ensure_tariffs

    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        user = db.scalar(select(User).where(User.email == "adminuv@example.com"))
        sub = db.scalar(select(Subscription).where(Subscription.org_id == user.org_id))
        org_tariff = db.scalar(select(Tariff).where(Tariff.code == TariffCode.organization))
        sub.tariff_id = org_tariff.id
        db.commit()
    finally:
        db.close()

    token = csrf_from(client, "/cabinet/staff/")
    r = client.post(
        "/cabinet/staff/invite",
        data={"csrf_token": token, "email": "colleague@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error=" in r.headers["location"]


def test_webhook_marks_lead_paid(app):
    client, dbmod = app
    from app.billing.payments import _activate_subscription_for_payment

    _signup(client, email="paid@example.com")
    db = dbmod.SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "paid@example.com"))
        sub = db.scalar(select(Subscription).where(Subscription.org_id == user.org_id))
        pay = Payment(
            id=uuid.uuid4(),
            org_id=user.org_id,
            subscription_id=sub.id,
            amount_kop=25000,
            status=PaymentStatus.authorized,
            source=PaymentSource.card,
            purpose="test",
        )
        db.add(pay)
        db.commit()
        db.refresh(pay)
        _activate_subscription_for_payment(db, pay, {})
        db.commit()
        lead = db.scalar(select(Lead).where(Lead.email == "paid@example.com"))
        assert lead.status == LeadStatus.paid
    finally:
        db.close()
