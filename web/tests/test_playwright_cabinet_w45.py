"""W-45/Б-3: Playwright — 8 страниц кабинета, без JS-ошибок, mobile 375px."""

from __future__ import annotations

import os
from datetime import timedelta

import pytest
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

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright  # noqa: E402

CABINET_PAGES = (
    "/cabinet/",
    "/cabinet/documents/",
    "/cabinet/package",
    "/cabinet/counterparties",
    "/cabinet/billing/",
    "/cabinet/settings",
    "/cabinet/help/",
    "/cabinet/zakon/",
)


def _seed(dbmod) -> tuple[str, str]:
    email = "pw-orgadmin@example.com"
    password = "Passw0rd!"
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        org = Organization(name="PW Org", requisites=empty_requisites())
        db.add(org)
        db.flush()
        db.add(
            User(
                org_id=org.id,
                email=email,
                password_hash=hash_password(password),
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
        return email, password
    finally:
        db.close()


@pytest.mark.skipif(
    os.environ.get("W45_PLAYWRIGHT") != "1",
    reason="установите W45_PLAYWRIGHT=1 и браузеры Playwright",
)
def test_cabinet_pages_no_console_errors_mobile(app):
    client, dbmod = app
    email, password = _seed(dbmod)
    # TestClient поднимает ASGI — для Playwright нужен живой URL
    base = os.environ.get("W45_PLAYWRIGHT_BASE")
    if not base:
        pytest.skip("W45_PLAYWRIGHT_BASE не задан (например http://127.0.0.1:8000)")

    console_errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 375, "height": 812})
        page = context.new_page()
        page.on(
            "console",
            lambda msg: console_errors.append(f"{msg.type}: {msg.text}")
            if msg.type == "error"
            else None,
        )
        page.goto(f"{base.rstrip('/')}/login", wait_until="domcontentloaded")
        page.fill('input[name="email"]', email)
        page.fill('input[name="password"]', password)
        page.click('button[type="submit"]')
        page.wait_for_load_state("domcontentloaded")
        for path in CABINET_PAGES:
            console_errors.clear()
            page.goto(f"{base.rstrip('/')}{path}", wait_until="domcontentloaded")
            # меню/формы видимы на mobile
            assert page.locator("body").is_visible()
            assert not console_errors, f"{path}: {console_errors}"
        browser.close()
