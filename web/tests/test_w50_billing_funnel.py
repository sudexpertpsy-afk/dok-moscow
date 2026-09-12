"""W-50 C.6: payment_funnel_30d с исключением is_internal."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.defaults import empty_requisites
from app.models import (
    Organization,
    Payment,
    PaymentSource,
    PaymentStatus,
    Subscription,
    SubscriptionPeriod,
    SubscriptionStatus,
    Tariff,
    TariffCode,
    utcnow,
)
from app.services.admin_metrics import payment_funnel_30d
from app.services.billing import ensure_tariffs


def _seed_pay(db, *, org: Organization, status: PaymentStatus, hours_ago: int = 1):
    guest = db.scalar(select(Tariff).where(Tariff.code == TariffCode.guest))
    sub = db.scalar(select(Subscription).where(Subscription.org_id == org.id))
    if sub is None:
        sub = Subscription(
            org_id=org.id,
            tariff_id=guest.id,
            period=SubscriptionPeriod.month,
            starts_at=utcnow(),
            ends_at=utcnow() + timedelta(days=30),
            status=SubscriptionStatus.active,
            auto_renew=False,
        )
        db.add(sub)
        db.flush()
    pay = Payment(
        org_id=org.id,
        subscription_id=sub.id,
        amount_kop=1000,
        purpose="funnel test",
        status=status,
        source=PaymentSource.card,
        raw_events=[],
    )
    db.add(pay)
    db.flush()
    pay.created_at = utcnow() - timedelta(hours=hours_ago)
    return pay


def test_payment_funnel_excludes_internal(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        pub = Organization(name="Public funnel org", requisites=empty_requisites())
        internal = Organization(
            name="УСЭ internal",
            requisites=empty_requisites(),
            is_internal=True,
        )
        db.add_all([pub, internal])
        db.flush()

        _seed_pay(db, org=pub, status=PaymentStatus.created)
        _seed_pay(db, org=pub, status=PaymentStatus.confirmed)
        _seed_pay(db, org=pub, status=PaymentStatus.expired)
        _seed_pay(db, org=pub, status=PaymentStatus.rejected)
        _seed_pay(db, org=internal, status=PaymentStatus.expired)
        _seed_pay(db, org=internal, status=PaymentStatus.confirmed)
        db.commit()

        funnel = payment_funnel_30d(db, include_internal=False)
        assert funnel["created"] == 1
        assert funnel["confirmed"] == 1
        assert funnel["expired"] == 1
        assert funnel["cancelled"] == 1
        assert funnel["expired_share"] == 25.0

        funnel_all = payment_funnel_30d(db, include_internal=True)
        assert funnel_all["confirmed"] == 2
        assert funnel_all["expired"] == 2
        assert funnel_all["created"] == 1
        assert funnel_all["cancelled"] == 1
        # 2 expired / (1+2+2+1) = 2/6 ≈ 33.3
        assert funnel_all["expired_share"] == 33.3
    finally:
        db.close()
