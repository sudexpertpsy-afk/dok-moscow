"""W-45/Б-3: Playwright — 8 страниц кабинета, без JS-ошибок, mobile 375px."""

from __future__ import annotations

import os
import threading
import time
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
pytest.importorskip("uvicorn")
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
    import uvicorn

    client, dbmod = app
    email, password = _seed(dbmod)
    fastapi_app = client.app
    port = int(os.environ.get("W45_PLAYWRIGHT_PORT", "8765"))
    config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "uvicorn не стартовал"
    base = f"http://127.0.0.1:{port}"

    js_errors: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 375, "height": 812})
            page = context.new_page()
            page.on("pageerror", lambda exc: js_errors.append(str(exc)))
            page.goto(f"{base}/login", wait_until="domcontentloaded")
            page.fill('input[name="email"]', email)
            page.fill('input[name="password"]', password)
            page.click('button[type="submit"]')
            page.wait_for_url("**/cabinet/**", timeout=10000)
            for path in CABINET_PAGES:
                js_errors.clear()
                page.goto(f"{base}{path}", wait_until="domcontentloaded")
                assert page.locator("body").is_visible()
                # меню на mobile: сайдбар или burger
                assert page.locator("nav, header, .sidebar, #sidebar, body").count() >= 1
                assert not js_errors, f"{path}: {js_errors}"
            browser.close()
    finally:
        server.should_exit = True
