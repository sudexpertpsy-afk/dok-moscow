"""W-38: административное управление подпиской организации."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pyotp
from sqlalchemy import select

from app.defaults import empty_requisites
from app.models import (
    Event,
    Organization,
    Payment,
    PaymentSource,
    PaymentStatus,
    SubscriptionStatus,
    TariffCode,
    User,
    utcnow,
)
from app.routers.admin_billing import _payments_summary
from app.services.admin_subscription import add_months
from app.services.billing import ensure_beta_subscriptions, ensure_tariffs, get_current_subscription
from app.totp_2fa import enable_totp, generate_totp_secret
from conftest import csrf_from, login


def _org(dbmod, name: str = "Орг W38") -> int:
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        org = Organization(name=name, requisites=empty_requisites())
        db.add(org)
        db.flush()
        ensure_beta_subscriptions(db)
        db.commit()
        return org.id
    finally:
        db.close()


def _enable_admin_totp(dbmod) -> str:
    db = dbmod.SessionLocal()
    try:
        admin = db.scalar(select(User).where(User.email == "admin@dok.moscow"))
        assert admin is not None
        secret = generate_totp_secret()
        enable_totp(admin, secret=secret, backup_codes=["AAAA-BBBB-CCCC"])
        db.commit()
        return secret
    finally:
        db.close()


def _post_sub(client, org_id: int, **data):
    token = csrf_from(client, f"/admin/organizations/{org_id}")
    payload = {
        "csrf_token": token,
        "confirm": "YES",
        "action": "apply",
        "tariff_code": "specialist",
        "term_mode": "relative",
        "months": "1",
        "reason": "partner",
        "reason_comment": "",
        **data,
    }
    return client.post(
        f"/admin/organizations/{org_id}/subscription",
        data=payload,
        follow_redirects=False,
    )


def test_extend_active_stacks_from_ends_at(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    oid = _org(dbmod)
    db = dbmod.SessionLocal()
    try:
        sub = get_current_subscription(db, oid)
        assert sub is not None
        # сделать активной с известным концом
        base_end = utcnow() + timedelta(days=10)
        sub.status = SubscriptionStatus.active
        sub.is_beta = False
        sub.ends_at = base_end
        db.commit()
        before = sub.ends_at
    finally:
        db.close()

    r = _post_sub(client, oid, months="3", reason="partner")
    assert r.status_code == 303

    db = dbmod.SessionLocal()
    try:
        sub = get_current_subscription(db, oid)
        assert sub is not None
        assert sub.is_current()
        assert sub.tariff.code == TariffCode.specialist
        expected = add_months(before, 3)
        assert abs((sub.ends_at - expected).total_seconds()) < 2
        assert sub.is_complimentary is True
        pays = db.scalars(select(Payment).where(Payment.org_id == oid)).all()
        assert pays == []
    finally:
        db.close()


def test_activate_expired_from_today(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    oid = _org(dbmod, "Орг Expired")
    db = dbmod.SessionLocal()
    try:
        sub = get_current_subscription(db, oid)
        sub.status = SubscriptionStatus.expired
        sub.ends_at = utcnow() - timedelta(days=5)
        db.commit()
    finally:
        db.close()

    before = utcnow()
    r = _post_sub(client, oid, months="1", reason="compensation")
    assert r.status_code == 303
    db = dbmod.SessionLocal()
    try:
        sub = get_current_subscription(db, oid)
        assert sub.status == SubscriptionStatus.active
        end = sub.ends_at
        if end.tzinfo is None:
            from datetime import timezone

            end = end.replace(tzinfo=timezone.utc)
        assert end > before + timedelta(days=27)
        assert sub.is_complimentary is True
    finally:
        db.close()


def test_downgrade_and_terminate_with_totp(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    oid = _org(dbmod, "Орг Down")
    r = _post_sub(client, oid, tariff_code="guest", months="1", reason="other", reason_comment="тест")
    assert r.status_code == 303
    db = dbmod.SessionLocal()
    try:
        sub = get_current_subscription(db, oid)
        assert sub.tariff.code == TariffCode.guest
    finally:
        db.close()

    secret = _enable_admin_totp(dbmod)

    # без totp — отказ
    r = _post_sub(client, oid, action="terminate")
    assert r.status_code == 303
    assert "err=" in r.headers.get("location", "")

    r = _post_sub(client, oid, action="terminate", totp_code=pyotp.TOTP(secret).now())
    assert r.status_code == 303
    assert "ok=" in r.headers.get("location", "")
    db = dbmod.SessionLocal()
    try:
        sub = get_current_subscription(db, oid)
        assert sub.status == SubscriptionStatus.cancelled
        assert not sub.is_current()
    finally:
        db.close()


def test_invoice_creates_payment_gift_does_not(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    oid = _org(dbmod, "Орг Invoice")

    r = _post_sub(
        client,
        oid,
        months="1",
        reason="invoice",
        reason_comment="пп 42 от 02.08.2026",
    )
    assert r.status_code == 303
    db = dbmod.SessionLocal()
    try:
        pays = db.scalars(select(Payment).where(Payment.org_id == oid)).all()
        assert len(pays) == 1
        assert pays[0].source == PaymentSource.manual
        assert pays[0].status == PaymentStatus.confirmed
        assert pays[0].manual_basis == "пп 42 от 02.08.2026"
        assert pays[0].amount_kop > 0
        sub = get_current_subscription(db, oid)
        assert sub.is_complimentary is False
    finally:
        db.close()

    oid2 = _org(dbmod, "Орг Gift")
    r = _post_sub(client, oid2, months="1", reason="partner")
    assert r.status_code == 303
    db = dbmod.SessionLocal()
    try:
        assert db.scalars(select(Payment).where(Payment.org_id == oid2)).all() == []
        sub = get_current_subscription(db, oid2)
        assert sub.is_complimentary is True
    finally:
        db.close()


def test_mrr_excludes_gift_and_counts_gift_subs(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    oid = _org(dbmod, "Орг MRR gift")
    # снять бета, выдать подарок specialist
    db = dbmod.SessionLocal()
    try:
        sub = get_current_subscription(db, oid)
        sub.is_beta = False
        sub.status = SubscriptionStatus.expired
        sub.ends_at = utcnow() - timedelta(days=1)
        db.commit()
    finally:
        db.close()

    summary_before = None
    db = dbmod.SessionLocal()
    try:
        summary_before = _payments_summary(db)
    finally:
        db.close()

    _post_sub(client, oid, months="1", reason="partner")

    db = dbmod.SessionLocal()
    try:
        after = _payments_summary(db)
        # подарок не увеличивает MRR
        assert after["mrr_kop"] == summary_before["mrr_kop"]
        assert after["gift_subs"] >= 1
        sub = get_current_subscription(db, oid)
        assert sub.is_complimentary
    finally:
        db.close()


def test_notify_flag_sends_mail(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    oid = _org(dbmod, "Орг Mail")
    with patch("app.services.admin_subscription.notify_admin_subscription_change") as mail:
        mail.return_value = True
        r = _post_sub(client, oid, months="1", reason="partner", notify="1")
        assert r.status_code == 303
        assert mail.called
        kwargs = mail.call_args.kwargs
        assert kwargs["terminated"] is False
        assert "Специалист" in kwargs["tariff_name"] or kwargs["tariff_name"]


def test_org_list_shows_subscription_column(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    _org(dbmod, "Орг List")
    r = client.get("/admin/organizations")
    assert r.status_code == 200
    assert "Подписка" in r.text
    assert "Подписка…" in r.text
    assert "Управление подпиской" in r.text


def test_history_event_recorded(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    oid = _org(dbmod, "Орг Hist")
    _post_sub(client, oid, months="1", reason="partner")
    db = dbmod.SessionLocal()
    try:
        ev = db.scalars(
            select(Event).where(
                Event.org_id == oid,
                Event.type == "billing_admin_subscription_apply",
            )
        ).all()
        assert len(ev) >= 1
        assert ev[0].details.get("reason") == "partner"
    finally:
        db.close()
    page = client.get(f"/admin/organizations/{oid}")
    assert page.status_code == 200
    assert "billing_admin_subscription_apply" in page.text
