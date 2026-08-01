"""W-14: чеки 54-ФЗ, письма биллинга, юридические страницы."""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import select

from app.billing.crypto import encrypt_secret
from app.billing.jobs import notify_expiring_subscriptions
from app.billing.payments import apply_payment_notification, create_card_payment
from app.billing.tbank import build_subscription_receipt, build_token, extract_receipt_fields
from app.defaults import empty_requisites
from app.models import (
    Event,
    Organization,
    Payment,
    PaymentSettings,
    PaymentSource,
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
from app.routers import billing as billing_router
from app.services.billing import ensure_beta_subscriptions, ensure_tariffs
from app.services.billing_mail import notify_payment_success


def test_build_subscription_receipt_54fz():
    rec = build_subscription_receipt(
        email="payer@example.com",
        taxation="usn_income",
        amount_kop=99_000,
        description="Подписка Док.Москва, тариф Специалист, период месяц",
        vat="none",
    )
    assert rec["Email"] == "payer@example.com"
    assert rec["Taxation"] == "usn_income"
    assert len(rec["Items"]) == 1
    item = rec["Items"][0]
    assert item["PaymentObject"] == "service"
    assert item["PaymentMethod"] == "full_prepayment"
    assert item["Quantity"] == 1.0
    assert item["Price"] == 99_000
    assert item["Amount"] == 99_000
    assert item["Tax"] == "none"
    assert "Специалист" in item["Name"]


def test_build_subscription_receipt_ffd12():
    rec = build_subscription_receipt(
        email="a@b.c",
        taxation="usn_income",
        amount_kop=100,
        description="Подписка",
        ffd_version="1.2",
        phone="+79001234567",
    )
    assert rec["FfdVersion"] == "1.2"
    assert rec["Phone"] == "+79001234567"
    assert rec["Items"][0]["MeasurementUnit"] == "шт"


def test_extract_receipt_fields_nested_and_top():
    status, url = extract_receipt_fields(
        {
            "Receipt": {"Status": "DONE", "Url": "https://ofd.example/r/1"},
        }
    )
    assert status == "DONE"
    assert url == "https://ofd.example/r/1"
    status2, url2 = extract_receipt_fields({"ReceiptUrl": "https://ofd.example/r/2"})
    assert url2 == "https://ofd.example/r/2"
    assert status2 is None


def _seed(dbmod, *, password: str = "test-pass", ends_in_days: int = 5):
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        org = Organization(
            name="ООО Чеки",
            requisites={
                **empty_requisites(),
                "организация": {**empty_requisites()["организация"], "email": "buh@org.test"},
            },
        )
        db.add(org)
        db.flush()
        ensure_beta_subscriptions(db)
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org.id))
        assert sub is not None
        sub.ends_at = utcnow() + timedelta(days=ends_in_days)
        sub.status = SubscriptionStatus.trial
        sub.auto_renew = False
        settings = db.get(PaymentSettings, 1)
        settings.terminal_key = "TestTerminalKey"
        settings.password_encrypted = encrypt_secret(password)
        settings.taxation = "usn_income"
        settings.vat_rate = "none"
        user = User(
            org_id=org.id,
            email="owner@org.test",
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
            is_active=True,
        )
        db.add(user)
        pay = Payment(
            id=uuid.uuid4(),
            org_id=org.id,
            subscription_id=sub.id,
            amount_kop=99_000,
            purpose="Подписка Док.Москва, тариф Специалист, период месяц",
            status=PaymentStatus.created,
            source=PaymentSource.card,
            tbank_payment_id="100500",
            raw_events=[{"event": "init", "email": "payer@org.test"}],
        )
        db.add(pay)
        db.commit()
        return org.id, sub.id, pay.id, password
    finally:
        db.close()


def test_webhook_saves_receipt_and_sends_email(app):
    client, dbmod = app
    billing_router.webhook_limiter.clear()
    _org_id, _sub_id, pay_id, password = _seed(dbmod)
    payload = {
        "TerminalKey": "TestTerminalKey",
        "OrderId": str(pay_id),
        "Success": True,
        "Status": "CONFIRMED",
        "PaymentId": "100500",
        "ErrorCode": "0",
        "Amount": 99000,
        "Receipt": {"Status": "DONE", "Url": "https://ofd.example/check/99"},
    }
    payload["Token"] = build_token(payload, password)

    with patch("app.billing.payments.notify_payment_success", return_value=True) as mail:
        r = client.post("/billing/webhook", json=payload)
        assert r.status_code == 200
        assert mail.called
        assert mail.call_args.kwargs["to_addr"] == "payer@org.test"

    db = dbmod.SessionLocal()
    try:
        pay = db.get(Payment, pay_id)
        assert pay.status == PaymentStatus.confirmed
        assert pay.receipt_url == "https://ofd.example/check/99"
        assert pay.receipt_status == "DONE"
    finally:
        db.close()


def test_init_passes_receipt(app):
    _, dbmod = app
    org_id, sub_id, _pay_id, _password = _seed(dbmod)
    db = dbmod.SessionLocal()
    try:
        sub = db.get(Subscription, sub_id)
        tariff = db.scalar(select(Tariff).where(Tariff.code == TariffCode.specialist))
        captured: dict = {}

        class FakeClient:
            def init(self, **kwargs):
                captured.update(kwargs)
                return {
                    "Success": True,
                    "PaymentId": "777",
                    "PaymentURL": "https://pay.test/777",
                    "Status": "NEW",
                    "ErrorCode": "0",
                }

            def close(self):
                return None

        with patch("app.billing.payments.load_tbank_client", return_value=FakeClient()):
            pay, url = create_card_payment(
                db,
                org_id=org_id,
                subscription=sub,
                tariff=tariff,
                period=SubscriptionPeriod.month,
                amount_kop=99_000,
                email="payer@example.com",
            )
            db.commit()
        assert url.endswith("/777")
        assert "receipt" in captured
        rec = captured["receipt"]
        assert rec["Taxation"] == "usn_income"
        assert rec["Items"][0]["PaymentObject"] == "service"
        assert any(e.get("email") == "payer@example.com" for e in pay.raw_events)
    finally:
        db.close()


def test_expiry_notice_7_and_1_days(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        org = Organization(name="ООО Срок", requisites=empty_requisites())
        db.add(org)
        db.flush()
        ensure_beta_subscriptions(db)
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org.id))
        sub.auto_renew = False
        sub.status = SubscriptionStatus.active
        sub.ends_at = utcnow().replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(
            days=7
        )
        db.add(
            User(
                org_id=org.id,
                email="exp@org.test",
                password_hash=hash_password("Passw0rd!"),
                role=UserRole.user,
                is_active=True,
            )
        )
        db.commit()
        sub_id = sub.id
    finally:
        db.close()

    with patch("app.billing.jobs.notify_subscription_expiring", return_value=True) as mail:
        db = dbmod.SessionLocal()
        try:
            n = notify_expiring_subscriptions(db)
            assert n == 1
            assert mail.call_args.kwargs["days_left"] == 7
            # повторно не шлём
            assert notify_expiring_subscriptions(db) == 0
            ev = db.scalar(select(Event).where(Event.type == "billing_expiry_notice"))
            assert ev is not None
            assert ev.details["subscription_id"] == sub_id
        finally:
            db.close()


def test_payment_success_mail_body_contains_receipt():
    from app.config import Settings

    pay = Payment(
        id=uuid.uuid4(),
        org_id=1,
        amount_kop=249_000,
        purpose="Подписка Док.Москва, тариф Организация, период месяц",
        status=PaymentStatus.confirmed,
        source=PaymentSource.card,
        receipt_url="https://ofd.example/x",
        raw_events=[],
    )
    org = Organization(name="Тест", requisites=empty_requisites())
    settings = Settings(smtp_host="")  # письмо уйдёт в лог
    with patch("app.services.billing_mail.send_email", return_value=True) as send:
        notify_payment_success(
            to_addr="a@b.c",
            org=org,
            payment=pay,
            ends_at=utcnow() + timedelta(days=30),
            settings=settings,
        )
        body = send.call_args.kwargs["body"]
        assert "https://ofd.example/x" in body
        assert "Организация" in body or "Подписка" in body


def test_legal_pages(app):
    client, _ = app
    for path, needle in (
        ("/offer", "Публичная оферта"),
        ("/requisites", "Реквизиты"),
        ("/tariffs", "Специалист"),
        ("/privacy", "ТБанк"),
    ):
        r = client.get(path)
        assert r.status_code == 200, path
        assert needle in r.text
    sm = client.get("/sitemap.xml")
    assert "/offer" in sm.text
    assert "/tariffs" in sm.text
    home = client.get("/")
    assert "990" in home.text
    assert "/offer" in home.text
