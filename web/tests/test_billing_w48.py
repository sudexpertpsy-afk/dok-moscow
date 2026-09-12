"""W-48: гигиена биллинга — промо reserve/commit, один заказ, expired, копейки."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from app.billing.crypto import encrypt_secret
from app.billing.jobs import reconcile_stale_payments
from app.billing.payments import (
    apply_payment_notification,
    create_card_payment,
    expire_abandoned_payments,
)
from app.defaults import empty_requisites
from app.models import (
    OrgRole,
    Organization,
    Payment,
    PaymentMode,
    PaymentSettings,
    PaymentSource,
    PaymentStatus,
    PromoCode,
    PromoCodeType,
    Subscription,
    SubscriptionPeriod,
    SubscriptionStatus,
    Tariff,
    TariffCode,
    User,
    UserRole,
    utcnow,
)
from app.routers.admin_billing import _payments_summary
from app.security import hash_password
from app.services.billing import ensure_payment_settings, ensure_tariffs
from app.services.cms import (
    format_price_rub,
    recalculate_promo_used_counts,
    tariff_amount_kop,
    validate_promo_code,
)
from conftest import csrf_from, login


def _seed_org(dbmod, email: str = "w48@example.com"):
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        ensure_payment_settings(db)
        guest = db.scalar(select(Tariff).where(Tariff.code == TariffCode.guest))
        org = Organization(name="W48 org", requisites=empty_requisites())
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
            )
        )
        row = db.get(PaymentSettings, 1)
        row.terminal_key = "TestTerminalKey"
        row.password_encrypted = encrypt_secret("terminal-password")
        row.mode = PaymentMode.test
        db.commit()
        return org.id, email
    finally:
        db.close()


def _fake_tbank(payment_id: str = "tb-w48"):
    fake = MagicMock()
    fake.init.return_value = {
        "Success": True,
        "PaymentId": payment_id,
        "PaymentURL": f"https://pay.tbank.ru/{payment_id}",
        "Status": "NEW",
        "OrderId": "x",
        "ErrorCode": "0",
    }
    fake.close = MagicMock()
    return fake


def test_promo_reserve_commit_release(app):
    _, dbmod = app
    org_id, _ = _seed_org(dbmod, "promo48@example.com")
    db = dbmod.SessionLocal()
    try:
        tariff = db.scalar(select(Tariff).where(Tariff.code == TariffCode.specialist))
        promo = PromoCode(
            code="W48SAVE",
            type=PromoCodeType.percent,
            value=95,
            tariff_codes=[],
            periods=[],
            max_uses=1,
            is_active=True,
            used_count=0,
        )
        db.add(promo)
        db.commit()
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
        with patch("app.billing.payments.load_tbank_client", return_value=_fake_tbank("p1")):
            pay, _url = create_card_payment(
                db,
                org_id=org_id,
                subscription=sub,
                tariff=tariff,
                period=SubscriptionPeriod.month,
                amount_kop=tariff_amount_kop(tariff, SubscriptionPeriod.month),
                email="promo48@example.com",
                promo_code="W48SAVE",
            )
        db.commit()
        db.refresh(promo)
        assert pay.promo_code_id == promo.id
        assert promo.used_count == 0

        blocked = validate_promo_code(
            db,
            code="W48SAVE",
            tariff=tariff,
            period=SubscriptionPeriod.month,
            base_amount_kop=tariff.price_month_kop,
        )
        assert blocked.ok is False

        pay.status = PaymentStatus.rejected
        db.commit()
        freed = validate_promo_code(
            db,
            code="W48SAVE",
            tariff=tariff,
            period=SubscriptionPeriod.month,
            base_amount_kop=tariff.price_month_kop,
        )
        assert freed.ok is True

        with patch("app.billing.payments.load_tbank_client", return_value=_fake_tbank("p2")):
            pay2, _ = create_card_payment(
                db,
                org_id=org_id,
                subscription=sub,
                tariff=tariff,
                period=SubscriptionPeriod.month,
                amount_kop=tariff_amount_kop(tariff, SubscriptionPeriod.month),
                email="promo48@example.com",
                promo_code="W48SAVE",
            )
        apply_payment_notification(
            db,
            {
                "OrderId": str(pay2.id),
                "Success": True,
                "Status": "CONFIRMED",
                "PaymentId": "p2",
                "Amount": pay2.amount_kop,
                "ErrorCode": "0",
            },
            skip_token=True,
        )
        db.commit()
        db.refresh(promo)
        assert promo.used_count == 1
    finally:
        db.close()


def test_one_open_order_reuses_payment_url(app):
    _, dbmod = app
    org_id, _ = _seed_org(dbmod, "reuse48@example.com")
    db = dbmod.SessionLocal()
    try:
        tariff = db.scalar(select(Tariff).where(Tariff.code == TariffCode.specialist))
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
        amount = tariff_amount_kop(tariff, SubscriptionPeriod.month)
        client1 = _fake_tbank("same")
        with patch("app.billing.payments.load_tbank_client", return_value=client1):
            pay1, url1 = create_card_payment(
                db,
                org_id=org_id,
                subscription=sub,
                tariff=tariff,
                period=SubscriptionPeriod.month,
                amount_kop=amount,
                email="reuse48@example.com",
            )
            pay2, url2 = create_card_payment(
                db,
                org_id=org_id,
                subscription=sub,
                tariff=tariff,
                period=SubscriptionPeriod.month,
                amount_kop=amount,
                email="reuse48@example.com",
            )
        db.commit()
        assert pay1.id == pay2.id
        assert url1 == url2
        assert client1.init.call_count == 1

        pay1.created_at = utcnow() - timedelta(minutes=21)
        db.commit()
        client2 = _fake_tbank("new")
        with patch("app.billing.payments.load_tbank_client", return_value=client2):
            pay3, url3 = create_card_payment(
                db,
                org_id=org_id,
                subscription=sub,
                tariff=tariff,
                period=SubscriptionPeriod.month,
                amount_kop=amount,
                email="reuse48@example.com",
            )
        db.commit()
        assert pay3.id != pay1.id
        assert url3.endswith("/new")
        db.refresh(pay1)
        assert pay1.status == PaymentStatus.expired
        assert client2.init.call_count == 1
    finally:
        db.close()


def test_open_order_reuses_within_20_minutes(app):
    """W-50 C.2: окно reuse = 20 минут."""
    _, dbmod = app
    org_id, _ = _seed_org(dbmod, "reuse20@example.com")
    db = dbmod.SessionLocal()
    try:
        tariff = db.scalar(select(Tariff).where(Tariff.code == TariffCode.specialist))
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
        amount = tariff_amount_kop(tariff, SubscriptionPeriod.month)
        client1 = _fake_tbank("same20")
        with patch("app.billing.payments.load_tbank_client", return_value=client1):
            pay1, url1 = create_card_payment(
                db,
                org_id=org_id,
                subscription=sub,
                tariff=tariff,
                period=SubscriptionPeriod.month,
                amount_kop=amount,
                email="reuse20@example.com",
            )
        pay1.created_at = utcnow() - timedelta(minutes=19)
        db.commit()
        with patch("app.billing.payments.load_tbank_client", return_value=client1):
            pay2, url2 = create_card_payment(
                db,
                org_id=org_id,
                subscription=sub,
                tariff=tariff,
                period=SubscriptionPeriod.month,
                amount_kop=amount,
                email="reuse20@example.com",
            )
        db.commit()
        assert pay1.id == pay2.id
        assert url1 == url2
        assert client1.init.call_count == 1
    finally:
        db.close()


def test_expire_abandoned_and_summary_metrics(app):
    _, dbmod = app
    org_id, _ = _seed_org(dbmod, "expire48@example.com")
    db = dbmod.SessionLocal()
    try:
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
        old = Payment(
            org_id=org_id,
            subscription_id=sub.id,
            amount_kop=1250,
            purpose="old init",
            status=PaymentStatus.created,
            source=PaymentSource.card,
            raw_events=[{"event": "init", "tariff_code": "specialist", "period": "month"}],
        )
        db.add(old)
        db.flush()
        old.created_at = utcnow() - timedelta(hours=25)
        conf = Payment(
            org_id=org_id,
            subscription_id=sub.id,
            amount_kop=1250,
            purpose="paid",
            status=PaymentStatus.confirmed,
            source=PaymentSource.card,
            raw_events=[],
        )
        db.add(conf)
        db.commit()

        n = expire_abandoned_payments(db)
        db.commit()
        assert n >= 1
        db.refresh(old)
        assert old.status == PaymentStatus.expired

        summary = _payments_summary(db)
        assert summary["month_count"] >= 1  # только confirmed в агрегате выручки
        assert summary["month_abandoned_init"] >= 1
        assert 0.0 <= summary["abandon_init_rate"] <= 1.0
        assert "mrr_kop" in summary

        # reconcile тоже гоняет expire
        old2 = Payment(
            org_id=org_id,
            subscription_id=sub.id,
            amount_kop=100,
            purpose="stale",
            status=PaymentStatus.created,
            source=PaymentSource.card,
            raw_events=[],
        )
        db.add(old2)
        db.flush()
        old2.created_at = utcnow() - timedelta(hours=30)
        db.commit()
        with patch("app.billing.jobs.flag_incomplete_receipts"):
            reconcile_stale_payments(db)
        db.refresh(old2)
        assert old2.status == PaymentStatus.expired
    finally:
        db.close()


def test_preview_matches_init_and_receipt_kopecks(app):
    client, dbmod = app
    org_id, email = _seed_org(dbmod, "kop48@example.com")
    db = dbmod.SessionLocal()
    try:
        tariff = db.scalar(select(Tariff).where(Tariff.code == TariffCode.specialist))
        base = tariff_amount_kop(tariff, SubscriptionPeriod.month)
        promo = PromoCode(
            code="KOP95",
            type=PromoCodeType.percent,
            value=95,
            tariff_codes=[],
            periods=[],
            is_active=True,
        )
        db.add(promo)
        db.commit()
        result = validate_promo_code(
            db,
            code="KOP95",
            tariff=tariff,
            period=SubscriptionPeriod.month,
            base_amount_kop=base,
        )
        assert result.final_amount_kop == 1250  # 25000 * 5%
        assert format_price_rub(result.final_amount_kop) == "12.50 ₽"
        assert format_price_rub(result.discount_kop) == "237.50 ₽"
    finally:
        db.close()

    login(client, email, "Passw0rd1!")
    prev = client.get(
        "/cabinet/billing/promo-preview",
        params={
            "tariff_code": "specialist",
            "period": "month",
            "promo_code": "KOP95",
        },
    )
    assert prev.status_code == 200
    assert "12.50 ₽" in prev.text
    assert "237.50 ₽" in prev.text

    db = dbmod.SessionLocal()
    try:
        tariff = db.scalar(select(Tariff).where(Tariff.code == TariffCode.specialist))
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
        with patch("app.billing.payments.load_tbank_client", return_value=_fake_tbank("kop")):
            pay, _ = create_card_payment(
                db,
                org_id=org_id,
                subscription=sub,
                tariff=tariff,
                period=SubscriptionPeriod.month,
                amount_kop=tariff_amount_kop(tariff, SubscriptionPeriod.month),
                email=email,
                promo_code="KOP95",
            )
        db.commit()
        assert pay.amount_kop == 1250
        init_ev = next(e for e in pay.raw_events if e.get("event") == "init")
        assert init_ev["receipt"]["Amount"] == 1250
        assert init_ev["receipt"]["Discount"] == 23750
    finally:
        db.close()

    # HTTP pay использует ту же сумму
    token = csrf_from(client)
    with patch("app.billing.payments.load_tbank_client", return_value=_fake_tbank("http")):
        # сброс открытого заказа — expire вручную
        db = dbmod.SessionLocal()
        try:
            for p in db.scalars(
                select(Payment).where(
                    Payment.org_id == org_id,
                    Payment.status == PaymentStatus.created,
                )
            ).all():
                p.status = PaymentStatus.expired
            db.commit()
        finally:
            db.close()
        r = client.post(
            "/cabinet/billing/pay",
            data={
                "tariff_code": "specialist",
                "period": "month",
                "promo_code": "KOP95",
                "receipt_email": email,
                "csrf_token": token,
            },
            follow_redirects=False,
        )
    assert r.status_code in (303, 302)
    assert "pay.tbank.ru" in (r.headers.get("location") or "")


def test_recalculate_promo_used_counts(app):
    _, dbmod = app
    org_id, _ = _seed_org(dbmod, "recalc48@example.com")
    db = dbmod.SessionLocal()
    try:
        promo = PromoCode(
            code="90PROBA",
            type=PromoCodeType.percent,
            value=95,
            used_count=99,
            is_active=True,
        )
        db.add(promo)
        db.flush()
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
        db.add(
            Payment(
                org_id=org_id,
                subscription_id=sub.id,
                amount_kop=1250,
                purpose="x",
                status=PaymentStatus.confirmed,
                source=PaymentSource.card,
                promo_code_id=promo.id,
                raw_events=[],
            )
        )
        db.add(
            Payment(
                org_id=org_id,
                subscription_id=sub.id,
                amount_kop=1250,
                purpose="y",
                status=PaymentStatus.created,
                source=PaymentSource.card,
                promo_code_id=promo.id,
                raw_events=[],
            )
        )
        db.commit()
        updated = recalculate_promo_used_counts(db, codes=["90PROBA", "BETA50"])
        db.commit()
        assert updated["90PROBA"] == 1
        db.refresh(promo)
        assert promo.used_count == 1
    finally:
        db.close()
