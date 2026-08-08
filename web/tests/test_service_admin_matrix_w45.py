"""W-45/Б-2: HTTP-матрица service_admin vs org_admin."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.defaults import empty_requisites
from app.models import (
    Organization,
    OrgRole,
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
from app.services.billing import ensure_tariffs
from conftest import login

# GET-страницы админки из NAV + ключевые подстраницы
ADMIN_GET_OK = (
    "/admin/",
    "/admin/organizations",
    "/admin/users",
    "/admin/leads",
    "/admin/invites",
    "/admin/templates",
    "/admin/templates/fields",
    "/admin/cms/",
    "/admin/cms/tariffs",
    "/admin/cms/content",
    "/admin/cms/promos",
    "/admin/cms/announcements",
    "/admin/security/",
    "/admin/legal/",
    "/admin/legal/new",
    "/admin/payments",
    "/admin/payment-settings",
    "/admin/status",
    "/admin/server/",
    "/admin/server/status-fragment",
    "/admin/server/redeploy-fragment",
)

# Кабинет: service_admin без org → редирект в /admin/
CABINET_GET_REDIRECT_ADMIN = (
    "/cabinet/",
    "/cabinet/billing/",
    "/cabinet/documents/",
    "/cabinet/package",
    "/cabinet/counterparties",
    "/cabinet/journal/",
    "/cabinet/calendar/",
    "/cabinet/help/",
    "/cabinet/settings",
    "/cabinet/staff/",
    "/cabinet/templates/",
    "/cabinet/zakon/",
    "/cabinet/party-check/",
)


def _seed_org_admin(dbmod, *, email: str = "orgadmin-w45@example.com") -> str:
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        org = Organization(name="W45 Matrix Org", requisites=empty_requisites())
        db.add(org)
        db.flush()
        db.add(
            User(
                org_id=org.id,
                email=email,
                password_hash=hash_password("Passw0rd!"),
                role=UserRole.user,
                org_role=OrgRole.org_admin,
                is_active=True,
            )
        )
        tariff = db.scalar(select(Tariff).where(Tariff.code == TariffCode.organization))
        assert tariff is not None
        db.add(
            Subscription(
                org_id=org.id,
                tariff_id=tariff.id,
                period=SubscriptionPeriod.month,
                starts_at=utcnow() - timedelta(days=1),
                ends_at=utcnow() + timedelta(days=30),
                status=SubscriptionStatus.active,
                auto_renew=False,
                is_beta=False,
            )
        )
        db.commit()
        return email
    finally:
        db.close()


def test_service_admin_admin_pages_ok(app):
    client, _ = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    failures: list[str] = []
    for path in ADMIN_GET_OK:
        r = client.get(path, follow_redirects=False)
        if r.status_code not in (200, 303):
            failures.append(f"{path} → {r.status_code}")
    assert not failures, "service_admin неожиданные ответы:\n" + "\n".join(failures)


def test_service_admin_cabinet_redirects_without_org(app):
    client, _ = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    failures: list[str] = []
    for path in CABINET_GET_REDIRECT_ADMIN:
        r = client.get(path, follow_redirects=False)
        # Starlette/HTTPException Location → 303; RedirectResponse иногда 307
        if r.status_code not in (303, 307):
            failures.append(f"{path} → {r.status_code}")
            continue
        loc = r.headers.get("location") or ""
        if "/admin" not in loc:
            failures.append(f"{path} → Location={loc}")
    assert not failures, "ожидали редирект в /admin/:\n" + "\n".join(failures)


def test_org_admin_forbidden_on_admin(app):
    client, dbmod = app
    email = _seed_org_admin(dbmod)
    assert login(client, email, "Passw0rd!").status_code == 303
    failures: list[str] = []
    for path in ADMIN_GET_OK:
        r = client.get(path, follow_redirects=False)
        if r.status_code != 403:
            failures.append(f"{path} → {r.status_code}")
    assert not failures, "org_admin должен получать 403 на /admin/*:\n" + "\n".join(failures)
