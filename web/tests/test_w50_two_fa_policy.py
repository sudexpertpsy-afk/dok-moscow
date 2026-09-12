"""W-50 B.5: мягкая политика 2FA."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.defaults import empty_requisites
from app.models import (
    Event,
    OrgRole,
    Organization,
    PaymentSettings,
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
from app.services.billing import ensure_payment_settings, ensure_tariffs
from app.services.two_fa_policy import (
    enforce_soft_2fa,
    org_admin_2fa_status,
    soft_2fa_banner_context,
)
from conftest import login


class _FakeRequest:
    def __init__(self, path: str = "/cabinet/", *, cookies: dict | None = None):
        self.url = type("U", (), {"path": path})()
        self.session: dict = {}
        self.cookies = cookies or {}


def _seed_paid_org_admin(db, *, email="admin2fa@example.com", totp=False, paid_days_ago=10):
    ensure_tariffs(db)
    ensure_payment_settings(db)
    row = db.get(PaymentSettings, 1)
    row.two_fa_policy_enabled = True
    row.two_fa_policy_enabled_at = utcnow() - timedelta(days=30)
    row.require_2fa_for_org_admins = False

    org = Organization(name="Paid Org 2FA", requisites=empty_requisites())
    db.add(org)
    db.flush()
    tariff = db.scalar(select(Tariff).where(Tariff.code == TariffCode.specialist))
    starts = utcnow() - timedelta(days=paid_days_ago)
    db.add(
        Subscription(
            org_id=org.id,
            tariff_id=tariff.id,
            period=SubscriptionPeriod.month,
            starts_at=starts,
            ends_at=utcnow() + timedelta(days=30),
            status=SubscriptionStatus.active,
            is_beta=False,
            is_complimentary=False,
        )
    )
    user = User(
        org_id=org.id,
        email=email,
        password_hash=hash_password("SecurePass1!"),
        role=UserRole.user,
        org_role=OrgRole.org_admin,
        is_active=True,
        email_verified=True,
        totp_enabled=totp,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_grace_banner_helper(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        user = _seed_paid_org_admin(db, paid_days_ago=1)
        assert org_admin_2fa_status(db, user) == "grace"
        ctx = soft_2fa_banner_context(_FakeRequest(), db, user)
        assert ctx["two_fa_show_grace_banner"] is True
        assert ctx["two_fa_grace_days"] is not None
        assert ctx["two_fa_grace_days"] <= 7
    finally:
        db.close()


def test_blocked_four_routes_redirect_documents_ok(app):
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        user = _seed_paid_org_admin(db, email="block2fa@example.com", paid_days_ago=20)
        assert org_admin_2fa_status(db, user) == "blocked"
    finally:
        db.close()

    assert login(client, "block2fa@example.com", "SecurePass1!").status_code == 303

    for path in (
        "/cabinet/billing/",
        "/cabinet/staff/",
        "/cabinet/settings/numbering",
        "/cabinet/settings/data",
    ):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303, path
        assert "/cabinet/settings/security" in r.headers["location"], path

    r_docs = client.get("/cabinet/documents/", follow_redirects=False)
    assert r_docs.status_code == 200


def test_policy_off_no_block(app):
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        user = _seed_paid_org_admin(db, email="off2fa@example.com", paid_days_ago=20)
        row = db.get(PaymentSettings, 1)
        row.two_fa_policy_enabled = False
        db.commit()
        assert org_admin_2fa_status(db, user) == "n/a"
    finally:
        db.close()

    assert login(client, "off2fa@example.com", "SecurePass1!").status_code == 303
    r = client.get("/cabinet/billing/", follow_redirects=False)
    assert r.status_code == 200


def test_enforce_helper_records_once(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        user = _seed_paid_org_admin(db, email="evt2fa@example.com", paid_days_ago=20)
        fr = _FakeRequest("/cabinet/billing/")
        r1 = enforce_soft_2fa(fr, user, db)
        assert r1 is not None
        assert fr.session.get("2fa_policy_block_logged")
        n1 = int(
            db.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.type == "security.2fa_policy_block")
            )
            or 0
        )
        assert n1 == 1
        r2 = enforce_soft_2fa(fr, user, db)
        assert r2 is not None
        n2 = int(
            db.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.type == "security.2fa_policy_block")
            )
            or 0
        )
        assert n2 == 1
    finally:
        db.close()
