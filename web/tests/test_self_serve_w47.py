"""W-47/W-50: self-serve signup → confirm → billing."""

from __future__ import annotations

import uuid
from unittest.mock import patch

from sqlalchemy import func, select

from app.models import (
    Lead,
    LeadStatus,
    Organization,
    Payment,
    PaymentSource,
    PaymentStatus,
    Signup,
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
    with patch("app.services.leads.send_email", return_value=True):
        return client.post("/signup", data=data, follow_redirects=False)


def _confirm(client, dbmod, email: str):
    db = dbmod.SessionLocal()
    try:
        row = db.scalar(select(Signup).where(Signup.email == email))
        assert row is not None
        tok = row.token
    finally:
        db.close()
    with patch("app.services.leads.notify_admin_self_serve_signup", return_value=True):
        return client.get(f"/confirm-signup/{tok}", follow_redirects=False)


def test_signup_pending_no_org_until_confirm(app):
    client, dbmod = app
    r = _signup(client, email="new@example.com", tariff="organization", period="year")
    assert r.status_code == 200
    assert "почт" in r.text.lower() or "ссылк" in r.text.lower()

    db = dbmod.SessionLocal()
    try:
        assert db.scalar(select(Signup).where(Signup.email == "new@example.com")) is not None
        assert db.scalar(select(User).where(User.email == "new@example.com")) is None
        assert int(db.scalar(select(func.count()).select_from(Organization)) or 0) == 0
        assert db.scalar(select(Lead).where(Lead.email == "new@example.com")) is None
    finally:
        db.close()

    r2 = _confirm(client, dbmod, "new@example.com")
    assert r2.status_code == 303
    assert "/cabinet/billing/" in r2.headers["location"]
    assert "tariff=organization" in r2.headers["location"]
    assert "period=year" in r2.headers["location"]
    assert "welcome=1" in r2.headers["location"]

    db = dbmod.SessionLocal()
    try:
        assert db.scalar(select(Signup).where(Signup.email == "new@example.com")) is None
        user = db.scalar(select(User).where(User.email == "new@example.com"))
        assert user is not None
        assert user.org_id is not None
        assert user.email_verified is True
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


def test_signup_repeat_pending_updates_token(app):
    client, dbmod = app
    _signup(client, email="dup@example.com")
    db = dbmod.SessionLocal()
    try:
        tok1 = db.scalar(select(Signup).where(Signup.email == "dup@example.com")).token
    finally:
        db.close()

    r = _signup(client, email="dup@example.com", org_name="Новое имя")
    assert r.status_code == 200
    assert "ещё раз" in r.text.lower() or "отправ" in r.text.lower()

    db = dbmod.SessionLocal()
    try:
        rows = list(db.scalars(select(Signup).where(Signup.email == "dup@example.com")).all())
        assert len(rows) == 1
        assert rows[0].token != tok1
        assert rows[0].org_name == "Новое имя"
        assert db.scalar(select(User).where(User.email == "dup@example.com")) is None
    finally:
        db.close()


def test_signup_after_confirm_redirects_login(app):
    client, dbmod = app
    _signup(client, email="exists@example.com")
    assert _confirm(client, dbmod, "exists@example.com").status_code == 303
    client.get("/logout")
    r = _signup(client, email="exists@example.com")
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/login?")
    assert "msg=exists" in loc


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
    client, dbmod = app
    _signup(client, email="bill@example.com", tariff="specialist", period="year")
    assert _confirm(client, dbmod, "bill@example.com").status_code == 303
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
    """Неподтверждённый e-mail (legacy/ручная учётка): сброс пароля недоступен, оплата — да."""
    client, dbmod = app
    from app.defaults import empty_requisites
    from app.models import OrgRole, UserRole
    from app.security import hash_password
    from app.services.billing import ensure_tariffs

    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        org = Organization(name="UV Org", requisites=empty_requisites())
        db.add(org)
        db.flush()
        guest = db.scalar(select(Tariff).where(Tariff.code == TariffCode.guest))
        from datetime import timedelta

        from app.models import SubscriptionPeriod, SubscriptionStatus, utcnow

        db.add(
            Subscription(
                org_id=org.id,
                tariff_id=guest.id,
                period=SubscriptionPeriod.month,
                starts_at=utcnow(),
                ends_at=utcnow() + timedelta(days=365),
                status=SubscriptionStatus.active,
            )
        )
        user = User(
            org_id=org.id,
            email="uv@example.com",
            password_hash=hash_password("SecurePass1!"),
            role=UserRole.user,
            org_role=OrgRole.org_admin,
            is_active=True,
            email_verified=False,
        )
        db.add(user)
        db.commit()
    finally:
        db.close()

    from conftest import login

    assert login(client, "uv@example.com", "SecurePass1!").status_code == 303

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
    from datetime import timedelta

    from app.defaults import empty_requisites
    from app.models import OrgRole, SubscriptionPeriod, SubscriptionStatus, UserRole, utcnow
    from app.security import hash_password
    from app.services.billing import ensure_tariffs
    from conftest import login

    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        org = Organization(name="Staff UV", requisites=empty_requisites())
        db.add(org)
        db.flush()
        org_tariff = db.scalar(select(Tariff).where(Tariff.code == TariffCode.organization))
        db.add(
            Subscription(
                org_id=org.id,
                tariff_id=org_tariff.id,
                period=SubscriptionPeriod.month,
                starts_at=utcnow(),
                ends_at=utcnow() + timedelta(days=365),
                status=SubscriptionStatus.active,
                is_beta=False,
                is_complimentary=False,
            )
        )
        db.add(
            User(
                org_id=org.id,
                email="adminuv@example.com",
                password_hash=hash_password("SecurePass1!"),
                role=UserRole.user,
                org_role=OrgRole.org_admin,
                is_active=True,
                email_verified=False,
            )
        )
        db.commit()
    finally:
        db.close()

    assert login(client, "adminuv@example.com", "SecurePass1!").status_code == 303
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
    assert _confirm(client, dbmod, "paid@example.com").status_code == 303
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
