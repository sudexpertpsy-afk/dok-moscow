"""Тариф не повышается до CONFIRMED (W-47 hotfix)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.billing.crypto import encrypt_secret
from app.billing.payments import apply_payment_notification
from app.defaults import empty_requisites
from app.models import (
    OrgRole,
    Organization,
    Payment,
    PaymentMode,
    PaymentSettings,
    PaymentStatus,
    Subscription,
    SubscriptionPeriod,
    SubscriptionStatus,
    Tariff,
    TariffCode,
    User,
    UserRole,
    utcnow,
)
from app.security import hash_password
from app.services.billing import ensure_payment_settings, ensure_tariffs, get_tariff_limits
from conftest import csrf_from, login


def _guest_org(dbmod, email: str = "guestpay@example.com"):
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        ensure_payment_settings(db)
        guest = db.scalar(select(Tariff).where(Tariff.code == TariffCode.guest))
        assert guest is not None
        org = Organization(name="Кабинет guestpay", requisites=empty_requisites())
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id,
            email=email,
            password_hash=hash_password("Passw0rd1!"),
            role=UserRole.user,
            org_role=OrgRole.org_admin,
            is_active=True,
            email_verified=True,
        )
        db.add(user)
        db.add(
            Subscription(
                org_id=org.id,
                tariff_id=guest.id,
                period=SubscriptionPeriod.month,
                starts_at=utcnow(),
                ends_at=utcnow() + timedelta(days=365 * 100),
                status=SubscriptionStatus.active,
                auto_renew=False,
                is_beta=False,
                is_complimentary=False,
            )
        )
        row = db.get(PaymentSettings, 1)
        row.terminal_key = "TestTerminalKeyDemo"
        row.password_encrypted = encrypt_secret("terminal-password")
        row.mode = PaymentMode.test
        db.commit()
        return org.id, email
    finally:
        db.close()


def test_pay_init_keeps_guest_until_confirmed(app):
    client, dbmod = app
    org_id, email = _guest_org(dbmod)
    assert login(client, email, "Passw0rd1!").status_code == 303

    before = get_tariff_limits(dbmod.SessionLocal(), org_id)
    assert before.tariff_code == TariffCode.guest

    token = csrf_from(client, "/cabinet/billing/")
    fake = MagicMock()
    fake.init.return_value = {
        "Success": True,
        "PaymentId": "999",
        "PaymentURL": "https://securepay.tinkoff.ru/html/payForm/1.0/?PaymentId=999",
        "Status": "NEW",
        "ErrorCode": "0",
    }
    with patch("app.billing.payments.load_tbank_client", return_value=fake):
        r = client.post(
            "/cabinet/billing/pay",
            data={
                "csrf_token": token,
                "tariff_code": TariffCode.specialist.value,
                "period": SubscriptionPeriod.month.value,
                "receipt_email": email,
            },
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert "PaymentId=999" in r.headers["location"]

    db = dbmod.SessionLocal()
    try:
        sub = db.scalar(
            select(Subscription)
            .options(joinedload(Subscription.tariff))
            .where(Subscription.org_id == org_id)
        )
        assert sub.tariff.code == TariffCode.guest
        lim = get_tariff_limits(db, org_id)
        assert lim.tariff_code == TariffCode.guest
        assert lim.limit_documents_month is not None

        pay = db.scalar(select(Payment).where(Payment.org_id == org_id))
        assert pay is not None
        assert pay.status == PaymentStatus.created
        init_ev = next(ev for ev in pay.raw_events if ev.get("event") == "init")
        assert init_ev["tariff_code"] == "specialist"
        assert init_ev["period"] == "month"

        apply_payment_notification(
            db,
            {
                "OrderId": str(pay.id),
                "PaymentId": "999",
                "Status": "CONFIRMED",
                "Success": True,
                "Amount": pay.amount_kop,
            },
            skip_token=True,
        )
        db.commit()

        sub2 = db.scalar(
            select(Subscription)
            .options(joinedload(Subscription.tariff))
            .where(Subscription.org_id == org_id)
        )
        assert sub2.tariff.code == TariffCode.specialist
        # не тащим guest-срок на 100 лет
        end = sub2.ends_at
        if end.tzinfo is None:
            from datetime import timezone

            end = end.replace(tzinfo=timezone.utc)
        assert (end - utcnow()).days < 40
        lim2 = get_tariff_limits(db, org_id)
        assert lim2.tariff_code == TariffCode.specialist
        assert lim2.limit_documents_month is None
    finally:
        db.close()
