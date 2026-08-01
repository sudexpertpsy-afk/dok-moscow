"""W-11: Т-Касса — Token, вебхук, идемпотентность, активация подписки."""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from app.billing.crypto import decrypt_secret, encrypt_secret
from app.billing.payments import apply_payment_notification, reconcile_payment
from app.billing.tbank import build_token, verify_token
from app.defaults import empty_requisites
from app.models import (
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
    utcnow,
)
from app.routers import billing as billing_router
from app.services.billing import ensure_beta_subscriptions, ensure_tariffs


def test_build_token_matches_tbank_docs_example():
    """Эталон из developer.tbank.ru/eacq/intro/developer/token."""
    params = {
        "TerminalKey": "MerchantTerminalKey",
        "Amount": 19200,
        "OrderId": "00000",
        "Description": "Подарочная карта на 1000 рублей",
        "DATA": {"Email": "a@test.com"},  # вложенное — не в токене
        "Receipt": {"Email": "a@test.ru"},
    }
    token = build_token(params, "11111111111111")
    assert token == "72dd466f8ace0a37a1f740ce5fb78101712bc0665d91a8108c7c8a0ccd426db2"


def test_verify_token_notification_booleans():
    params = {
        "TerminalKey": "1234567890DEMO",
        "OrderId": "000000",
        "Success": True,  # bool → "true"
        "Status": "AUTHORIZED",
        "PaymentId": "0000000",
        "ErrorCode": "0",
        "Amount": 1111,
        "CardId": "000000",
        "Pan": "200000******0000",
        "ExpDate": "1111",
        "RebillId": "000000",
    }
    token = build_token(params, "11111111111")
    assert token == "1c0964277d0213349243065a0d5b838b8e90d2d25f740d0f2767836e710e80c8"
    params["Token"] = token
    assert verify_token(params, "11111111111")
    assert not verify_token(params, "wrong")


def test_fernet_roundtrip(app):
    enc = encrypt_secret("terminal-password-secret")
    assert enc != "terminal-password-secret"
    assert decrypt_secret(enc) == "terminal-password-secret"


def _seed_org_payment(dbmod, *, password: str = "test-pass"):
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        org = Organization(name="ООО Биллинг", requisites=empty_requisites())
        db.add(org)
        db.flush()
        ensure_beta_subscriptions(db)
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org.id))
        assert sub is not None
        # укоротим срок, чтобы было видно продление
        sub.ends_at = utcnow() + timedelta(days=5)
        sub.status = SubscriptionStatus.trial
        settings = db.get(PaymentSettings, 1)
        settings.terminal_key = "TestTerminalKey"
        settings.password_encrypted = encrypt_secret(password)
        pay = Payment(
            id=uuid.uuid4(),
            org_id=org.id,
            subscription_id=sub.id,
            amount_kop=99_000,
            purpose="Подписка Док.Москва, тариф Специалист, период месяц",
            status=PaymentStatus.created,
            source=PaymentSource.card,
            tbank_payment_id="100500",
            raw_events=[],
        )
        db.add(pay)
        db.commit()
        return org.id, sub.id, pay.id, password
    finally:
        db.close()


def _signed_payload(order_id, password, **extra):
    payload = {
        "TerminalKey": "TestTerminalKey",
        "OrderId": str(order_id),
        "Success": True,
        "Status": "CONFIRMED",
        "PaymentId": "100500",
        "ErrorCode": "0",
        "Amount": 99000,
        "RebillId": "rebill-99",
        **extra,
    }
    payload["Token"] = build_token(payload, password)
    return payload


def test_webhook_confirmed_activates_subscription(app):
    client, dbmod = app
    billing_router.webhook_limiter.clear()
    org_id, sub_id, pay_id, password = _seed_org_payment(dbmod)

    db = dbmod.SessionLocal()
    try:
        sub_before = db.get(Subscription, sub_id)
        ends_before = sub_before.ends_at
    finally:
        db.close()

    payload = _signed_payload(pay_id, password)
    r = client.post("/billing/webhook", json=payload)
    assert r.status_code == 200
    assert r.text == "OK"

    db = dbmod.SessionLocal()
    try:
        pay = db.get(Payment, pay_id)
        sub = db.get(Subscription, sub_id)
        assert pay.status == PaymentStatus.confirmed
        assert sub.status == SubscriptionStatus.active
        assert sub.rebill_id == "rebill-99"
        assert sub.is_beta is False
        assert sub.ends_at > ends_before
        assert len(pay.raw_events) >= 1
    finally:
        db.close()


def test_webhook_duplicate_no_double_extend(app):
    client, dbmod = app
    billing_router.webhook_limiter.clear()
    _org_id, sub_id, pay_id, password = _seed_org_payment(dbmod)
    payload = _signed_payload(pay_id, password)

    assert client.post("/billing/webhook", json=payload).text == "OK"
    db = dbmod.SessionLocal()
    try:
        ends1 = db.get(Subscription, sub_id).ends_at
        events1 = len(db.get(Payment, pay_id).raw_events)
    finally:
        db.close()

    assert client.post("/billing/webhook", json=payload).text == "OK"
    db = dbmod.SessionLocal()
    try:
        ends2 = db.get(Subscription, sub_id).ends_at
        pay = db.get(Payment, pay_id)
        assert ends2 == ends1
        assert len(pay.raw_events) == events1 + 1
        assert any(e.get("event") == "notification_duplicate" for e in pay.raw_events)
    finally:
        db.close()


def test_webhook_bad_token_rejected(app):
    client, dbmod = app
    billing_router.webhook_limiter.clear()
    _org_id, _sub_id, pay_id, password = _seed_org_payment(dbmod)
    payload = _signed_payload(pay_id, password)
    payload["Token"] = "0" * 64
    r = client.post("/billing/webhook", json=payload)
    assert r.status_code == 200
    assert r.text == "OK"
    db = dbmod.SessionLocal()
    try:
        pay = db.get(Payment, pay_id)
        assert pay.status == PaymentStatus.created
    finally:
        db.close()


def test_webhook_rejected_status(app):
    client, dbmod = app
    billing_router.webhook_limiter.clear()
    _org_id, sub_id, pay_id, password = _seed_org_payment(dbmod)
    payload = _signed_payload(pay_id, password, Status="REJECTED", Success=False, RebillId=None)
    # пересчитать token после смены полей
    payload.pop("Token", None)
    payload.pop("RebillId", None)
    payload["Token"] = build_token(payload, password)
    assert client.post("/billing/webhook", json=payload).text == "OK"
    db = dbmod.SessionLocal()
    try:
        assert db.get(Payment, pay_id).status == PaymentStatus.rejected
        assert db.get(Subscription, sub_id).status == SubscriptionStatus.trial
    finally:
        db.close()


def test_reconcile_getstate_mocked(app):
    _, dbmod = app
    _org_id, sub_id, pay_id, password = _seed_org_payment(dbmod)
    db = dbmod.SessionLocal()
    try:
        pay = db.get(Payment, pay_id)
        fake = MagicMock()
        fake.get_state.return_value = {
            "Success": True,
            "PaymentId": "100500",
            "Status": "CONFIRMED",
            "Amount": 99000,
            "ErrorCode": "0",
            "RebillId": "R1",
        }
        with patch("app.billing.payments.load_tbank_client", return_value=fake):
            reconcile_payment(db, pay)
            db.commit()
        fake.close.assert_called()
        db.refresh(pay)
        assert pay.status == PaymentStatus.confirmed
        assert db.get(Subscription, sub_id).status == SubscriptionStatus.active
    finally:
        db.close()


def test_init_mocked_returns_payment_url(app):
    from app.billing.payments import create_card_payment

    _, dbmod = app
    org_id, sub_id, _pay_id, password = _seed_org_payment(dbmod)
    db = dbmod.SessionLocal()
    try:
        sub = db.get(Subscription, sub_id)
        tariff = db.scalar(select(Tariff).where(Tariff.code == TariffCode.specialist))
        fake = MagicMock()
        fake.init.return_value = {
            "Success": True,
            "PaymentId": "777",
            "PaymentURL": "https://securepay.tinkoff.ru/pay/777",
            "Status": "NEW",
            "OrderId": "x",
            "ErrorCode": "0",
        }
        with patch("app.billing.payments.load_tbank_client", return_value=fake):
            pay, url = create_card_payment(
                db,
                org_id=org_id,
                subscription=sub,
                tariff=tariff,
                period=SubscriptionPeriod.month,
                amount_kop=99_000,
                email="payer@example.com",
                auto_renew=True,
            )
            db.commit()
        assert url.endswith("/777")
        assert pay.tbank_payment_id == "777"
        assert sub.auto_renew is True
        fake.init.assert_called_once()
        kwargs = fake.init.call_args.kwargs
        assert kwargs["recurrent"] is False or "CustomerKey" in str(fake.init.call_args)
    finally:
        db.close()
