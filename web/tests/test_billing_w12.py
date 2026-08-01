"""W-12: кабинет «Тариф и оплата», лимиты генерации."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.defaults import empty_requisites
from app.models import (
    Document,
    DocumentFormat,
    Organization,
    Subscription,
    SubscriptionStatus,
    Tariff,
    TariffCode,
    User,
    UserRole,
    utcnow,
)
from app.security import hash_password
from app.services.billing import ensure_beta_subscriptions, ensure_tariffs
from app.services.limits import assert_can_generate, usage_snapshot
from conftest import csrf_from, login


def _org_user(dbmod, email: str = "bill@example.com"):
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        org = Organization(name="ООО Лимиты", requisites=empty_requisites())
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id,
            email=email,
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
            is_active=True,
        )
        db.add(user)
        ensure_beta_subscriptions(db)
        db.commit()
        return org.id, email
    finally:
        db.close()


def test_billing_page_shows_tariff(app):
    client, dbmod = app
    _org_id, email = _org_user(dbmod)
    assert login(client, email, "Passw0rd!").status_code == 303
    r = client.get("/cabinet/billing/")
    assert r.status_code == 200
    assert "Тариф и оплата" in r.text
    assert "Специалист" in r.text
    assert "История платежей" in r.text


def test_expired_blocks_generation(app):
    client, dbmod = app
    org_id, email = _org_user(dbmod, "exp@example.com")
    db = dbmod.SessionLocal()
    try:
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
        sub.ends_at = utcnow() - timedelta(days=1)
        sub.status = SubscriptionStatus.expired
        db.commit()
        snap = usage_snapshot(db, org_id)
        assert snap.can_generate is False
        try:
            assert_can_generate(db, org_id)
            assert False, "expected redirect"
        except Exception as exc:
            assert exc.status_code == 303
            assert "/cabinet/billing/" in exc.headers["Location"]
    finally:
        db.close()


def test_guest_document_limit(app):
    _, dbmod = app
    org_id, _email = _org_user(dbmod, "guestlim@example.com")
    db = dbmod.SessionLocal()
    try:
        guest = db.scalar(select(Tariff).where(Tariff.code == TariffCode.guest))
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
        sub.tariff_id = guest.id
        sub.status = SubscriptionStatus.active
        sub.ends_at = utcnow() + timedelta(days=30)
        sub.is_beta = False
        for i in range(3):
            db.add(
                Document(
                    org_id=org_id,
                    template="t.docx",
                    file_path=f"x/{i}.docx",
                    format=DocumentFormat.docx,
                    context={},
                )
            )
        db.commit()
        snap = usage_snapshot(db, org_id)
        assert snap.documents_this_month == 3
        assert snap.can_generate is False
        assert snap.limits.watermark is True
    finally:
        db.close()


def test_pay_without_terminal_shows_error(app):
    client, dbmod = app
    _org_id, email = _org_user(dbmod, "pay@example.com")
    assert login(client, email, "Passw0rd!").status_code == 303
    token = csrf_from(client, "/cabinet/billing/")
    r = client.post(
        "/cabinet/billing/pay",
        data={
            "csrf_token": token,
            "tariff_code": "specialist",
            "period": "month",
            "receipt_email": email,
        },
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "не настроена" in r.text.lower() or "Платёжная" in r.text
