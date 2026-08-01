"""W-10: модель биллинга — тарифы, подписки, лимиты из БД."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

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
from app.services.billing import (
    DEFAULT_TARIFFS,
    get_tariff_limits,
    transition_subscription,
)


def _org(dbmod, name: str = "Орг биллинг") -> int:
    db = dbmod.SessionLocal()
    try:
        org = Organization(name=name, requisites=empty_requisites())
        db.add(org)
        db.commit()
        db.refresh(org)
        return org.id
    finally:
        db.close()


def test_tariffs_seeded_from_db_not_hardcoded_runtime(app):
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        tariffs = {t.code: t for t in db.scalars(select(Tariff)).all()}
        assert set(tariffs) == {TariffCode.guest, TariffCode.specialist, TariffCode.organization}
        guest = tariffs[TariffCode.guest]
        assert guest.limit_documents_month == 3
        assert guest.watermark is True
        assert guest.price_month_kop == 0
        spec = tariffs[TariffCode.specialist]
        assert spec.price_month_kop == 99_000
        assert spec.price_year_kop == 990_000
        assert spec.limit_users == 1
        assert spec.watermark is False
        org_t = tariffs[TariffCode.organization]
        assert org_t.limit_users == 5
        assert org_t.price_month_kop == 249_000
        # лимиты читаются из строк БД
        guest.limit_documents_month = 7
        db.commit()
    finally:
        db.close()

    org_id = _org(dbmod, "Лимиты из БД")
    db = dbmod.SessionLocal()
    try:
        # без подписки → guest с обновлённым лимитом из БД
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
        if sub:
            db.delete(sub)
            db.commit()
        limits = get_tariff_limits(db, org_id)
        assert limits.tariff_code == TariffCode.guest
        assert limits.limit_documents_month == 7
        assert limits.watermark is True
    finally:
        db.close()


def test_beta_subscription_for_new_org_on_bootstrap(app):
    _, dbmod = app
    org_id = _org(dbmod, "Бета орг")
    # bootstrap уже отработал на старте; новая орг без подписки — создаём вручную через ensure
    from app.services.billing import ensure_beta_subscriptions

    db = dbmod.SessionLocal()
    try:
        n = ensure_beta_subscriptions(db)
        db.commit()
        assert n >= 1
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
        assert sub is not None
        assert sub.status == SubscriptionStatus.trial
        assert sub.is_beta is True
        assert sub.tariff.code == TariffCode.organization
        assert sub.is_current()
        limits = get_tariff_limits(db, org_id)
        assert limits.tariff_code == TariffCode.organization
        assert limits.watermark is False
        assert limits.limit_users == 5
    finally:
        db.close()


def test_subscription_status_transitions(app):
    _, dbmod = app
    org_id = _org(dbmod, "Переходы")
    db = dbmod.SessionLocal()
    try:
        from app.services.billing import ensure_beta_subscriptions

        ensure_beta_subscriptions(db)
        db.commit()
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
        assert sub is not None
        transition_subscription(sub, to=SubscriptionStatus.active)
        assert sub.status == SubscriptionStatus.active
        transition_subscription(sub, to=SubscriptionStatus.expired)
        assert sub.status == SubscriptionStatus.expired
        assert sub.is_current() is False
        with pytest.raises(ValueError):
            transition_subscription(sub, to=SubscriptionStatus.cancelled)
        transition_subscription(sub, to=SubscriptionStatus.active)
        transition_subscription(sub, to=SubscriptionStatus.cancelled)
        assert sub.auto_renew is False
    finally:
        db.close()


def test_payment_uuid_order_id_and_events(app):
    _, dbmod = app
    org_id = _org(dbmod, "Платёж")
    db = dbmod.SessionLocal()
    try:
        from app.services.billing import ensure_beta_subscriptions

        ensure_beta_subscriptions(db)
        db.commit()
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
        pay = Payment(
            org_id=org_id,
            subscription_id=sub.id,
            amount_kop=99_000,
            purpose="Подписка Специалист, месяц",
            status=PaymentStatus.created,
            source=PaymentSource.card,
            raw_events=[],
        )
        db.add(pay)
        db.commit()
        db.refresh(pay)
        assert pay.id is not None
        order_id = str(pay.id)
        assert len(order_id) == 36
        pay.append_event({"Status": "CONFIRMED", "OrderId": order_id})
        pay.status = PaymentStatus.confirmed
        db.commit()
        db.refresh(pay)
        assert pay.status == PaymentStatus.confirmed
        assert len(pay.raw_events) == 1
        assert pay.raw_events[0]["Status"] == "CONFIRMED"
    finally:
        db.close()


def test_payment_settings_singleton(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        row = db.get(PaymentSettings, 1)
        assert row is not None
        assert row.mode.value == "test"
        assert row.password_encrypted is None
    finally:
        db.close()


def test_expired_subscription_falls_back_limits(app):
    _, dbmod = app
    org_id = _org(dbmod, "Истекла")
    db = dbmod.SessionLocal()
    try:
        from app.services.billing import ensure_tariffs

        ensure_tariffs(db)
        specialist = db.scalar(select(Tariff).where(Tariff.code == TariffCode.specialist))
        past = utcnow() - timedelta(days=2)
        sub = Subscription(
            org_id=org_id,
            tariff_id=specialist.id,
            period=SubscriptionPeriod.month,
            starts_at=past - timedelta(days=30),
            ends_at=past,
            status=SubscriptionStatus.expired,
            is_beta=False,
        )
        db.add(sub)
        db.commit()
        limits = get_tariff_limits(db, org_id)
        # текущей нет — возвращаем последнюю (expired), is_current=False
        assert limits.tariff_code == TariffCode.specialist
        assert limits.is_current is False
    finally:
        db.close()


def test_default_tariffs_prices_in_kopecks():
    by_code = {r["code"]: r for r in DEFAULT_TARIFFS}
    assert by_code[TariffCode.specialist]["price_month_kop"] == 990 * 100
    assert by_code[TariffCode.organization]["price_year_kop"] == 24_900 * 100
