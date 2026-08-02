"""W-40: аналитика и верификация в Едином окне."""

from __future__ import annotations

from sqlalchemy import select

from app.models import Event
from app.security import build_content_security_policy
from app.services.analytics import (
    AnalyticsValidationError,
    get_analytics_public,
    invalidate_analytics_cache,
    save_analytics_settings,
    validate_fields,
)
from conftest import csrf_from, login


def _enable_all(dbmod, *, metrika="12345678", ga4="G-ABCDEF12", ywm="a" * 16, gsc="G" * 20):
    db = dbmod.SessionLocal()
    try:
        save_analytics_settings(
            db,
            user_id=1,
            form={
                "yandex_metrika_id": metrika,
                "yandex_metrika_enabled": "1",
                "yandex_metrika_webvisor": "1",
                "yandex_metrika_clickmap": "1",
                "yandex_metrika_track_forms": "1",
                "ga4_measurement_id": ga4,
                "ga4_enabled": "1",
                "yandex_webmaster_code": ywm,
                "yandex_webmaster_enabled": "1",
                "google_site_verification": gsc,
                "google_site_verification_enabled": "1",
            },
        )
        db.commit()
    finally:
        db.close()
    invalidate_analytics_cache()


def test_normalize_extracts_meta_tag_content():
    from app.services.analytics import _normalize_google, _normalize_yandex_wm

    tag = '<meta name="yandex-verification" content="1056476fdcee767b" />'
    assert _normalize_yandex_wm(tag) == "1056476fdcee767b"
    # как вставляют без угловых скобок
    assert (
        _normalize_yandex_wm('meta name="yandex-verification" content="1056476fdcee767b" /')
        == "1056476fdcee767b"
    )
    assert _normalize_yandex_wm("1056476fdcee767b") == "1056476fdcee767b"

    gtag = '<meta name="google-site-verification" content="AbC_dEf-0123456789XYZ" />'
    assert _normalize_google(gtag) == "AbC_dEf-0123456789XYZ"


def test_save_accepts_pasted_yandex_meta_tag(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        save_analytics_settings(
            db,
            user_id=1,
            form={
                "yandex_webmaster_enabled": "1",
                "yandex_webmaster_code": '<meta name="yandex-verification" content="1056476fdcee767b" />',
            },
        )
        db.commit()
        from app.services.analytics import ensure_analytics_settings

        row = ensure_analytics_settings(db)
        assert row.yandex_webmaster_code == "1056476fdcee767b"
        assert row.yandex_webmaster_enabled is True
    finally:
        db.close()
    invalidate_analytics_cache()
    assert get_analytics_public().yandex_webmaster_code == "1056476fdcee767b"


def test_validate_rejects_invalid_and_xss():
    with __import__("pytest").raises(AnalyticsValidationError):
        validate_fields(
            yandex_metrika_id="12",
            yandex_metrika_enabled=True,
            ga4_measurement_id="",
            ga4_enabled=False,
            yandex_webmaster_code="",
            yandex_webmaster_enabled=False,
            google_site_verification="",
            google_site_verification_enabled=False,
        )
    with __import__("pytest").raises(AnalyticsValidationError):
        validate_fields(
            yandex_metrika_id="",
            yandex_metrika_enabled=False,
            ga4_measurement_id="UA-123",
            ga4_enabled=True,
            yandex_webmaster_code="",
            yandex_webmaster_enabled=False,
            google_site_verification="",
            google_site_verification_enabled=False,
        )
    with __import__("pytest").raises(AnalyticsValidationError):
        validate_fields(
            yandex_metrika_id='123456"><script>',
            yandex_metrika_enabled=True,
            ga4_measurement_id="",
            ga4_enabled=False,
            yandex_webmaster_code="",
            yandex_webmaster_enabled=False,
            google_site_verification="",
            google_site_verification_enabled=False,
        )


def test_csp_only_enabled_domains():
    base = build_content_security_policy()
    assert "mc.yandex.ru" not in base
    assert "googletagmanager.com" not in base
    assert "mc.webvisor.org" not in base

    m = build_content_security_policy(metrika=True)
    assert "https://mc.yandex.ru" in m
    assert "mc.webvisor.org" not in m
    assert "googletagmanager.com" not in m

    w = build_content_security_policy(metrika=True, webvisor=True)
    assert "mc.webvisor.org" in w

    g = build_content_security_policy(ga4=True)
    assert "googletagmanager.com" in g
    assert "google-analytics.com" in g
    assert "mc.yandex.ru" not in g


def test_snippets_on_public_not_in_cabinet_admin(app):
    client, dbmod = app
    _enable_all(dbmod)

    home = client.get("/")
    assert home.status_code == 200
    assert "mc.yandex.ru/metrika/tag.js" in home.text
    assert "ym(12345678" in home.text
    assert "gtag/js?id=G-ABCDEF12" in home.text
    assert 'name="yandex-verification" content="aaaaaaaaaaaaaaaa"' in home.text
    assert 'name="google-site-verification" content="GGGGGGGGGGGGGGGGGGGG"' in home.text
    assert "mc.yandex.ru" in (home.headers.get("content-security-policy") or "")
    assert "googletagmanager.com" in (home.headers.get("content-security-policy") or "")
    assert "mc.webvisor.org" in (home.headers.get("content-security-policy") or "")

    zakon = client.get("/zakon/")
    assert zakon.status_code == 200
    assert "mc.yandex.ru/metrika/tag.js" in zakon.text
    assert "G-ABCDEF12" in zakon.text

    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    admin = client.get("/admin/cms/")
    assert admin.status_code == 200
    assert "Аналитика и подтверждение сайта" in admin.text
    assert "mc.yandex.ru/metrika/tag.js" not in admin.text
    assert "gtag/js?id=" not in admin.text

    # кабинет — тоже без счётчиков
    from app.defaults import empty_requisites
    from app.models import Organization, User, UserRole
    from app.security import hash_password

    db = dbmod.SessionLocal()
    try:
        org = Organization(name="Cab", requisites=empty_requisites())
        db.add(org)
        db.flush()
        db.add(
            User(
                org_id=org.id,
                email="cab-w40@example.com",
                password_hash=hash_password("Passw0rd!"),
                role=UserRole.user,
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()
    client.cookies.clear()
    assert login(client, "cab-w40@example.com", "Passw0rd!").status_code == 303
    cab = client.get("/cabinet/")
    assert cab.status_code == 200
    assert "mc.yandex.ru/metrika/tag.js" not in cab.text
    assert "gtag/js?id=" not in cab.text


def test_disable_removes_snippet_and_csp(app):
    client, dbmod = app
    _enable_all(dbmod)
    assert "mc.yandex.ru" in (client.get("/").headers.get("content-security-policy") or "")

    db = dbmod.SessionLocal()
    try:
        save_analytics_settings(
            db,
            user_id=1,
            form={
                "yandex_metrika_id": "12345678",
                "yandex_metrika_enabled": "",
                "ga4_measurement_id": "G-ABCDEF12",
                "ga4_enabled": "",
                "yandex_webmaster_code": "a" * 16,
                "yandex_webmaster_enabled": "",
                "google_site_verification": "G" * 20,
                "google_site_verification_enabled": "",
            },
        )
        db.commit()
    finally:
        db.close()
    invalidate_analytics_cache()

    r = client.get("/")
    assert "mc.yandex.ru/metrika/tag.js" not in r.text
    assert "gtag/js" not in r.text
    csp = r.headers.get("content-security-policy") or ""
    assert "mc.yandex.ru" not in csp
    assert "googletagmanager.com" not in csp


def test_admin_save_rejects_invalid_and_logs_event(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    token = csrf_from(client, "/admin/cms/")
    bad = client.post(
        "/admin/cms/analytics",
        data={
            "csrf_token": token,
            "yandex_metrika_enabled": "1",
            "yandex_metrika_id": "<script>1</script>",
        },
        follow_redirects=False,
    )
    assert bad.status_code == 303
    assert "err=" in (bad.headers.get("location") or "")

    token = csrf_from(client, "/admin/cms/")
    ok = client.post(
        "/admin/cms/analytics",
        data={
            "csrf_token": token,
            "yandex_metrika_enabled": "1",
            "yandex_metrika_id": "99887766",
            "yandex_metrika_clickmap": "1",
        },
        follow_redirects=False,
    )
    assert ok.status_code == 303
    assert "ok=" in (ok.headers.get("location") or "")

    db = dbmod.SessionLocal()
    try:
        ev = db.scalar(
            select(Event).where(Event.type == "analytics_settings_changed").order_by(Event.id.desc())
        )
        assert ev is not None
        assert "yandex_metrika_enabled" in (ev.details or {}).get("fields", [])
        # значений ID в журнале нет
        blob = str(ev.details)
        assert "99887766" not in blob
    finally:
        db.close()

    invalidate_analytics_cache()
    snap = get_analytics_public()
    assert snap.yandex_metrika_id == "99887766"
    page = client.get("/")
    assert "ym(99887766" in page.text
    assert "<script>1</script>" not in page.text


def test_xss_id_never_reaches_html(app):
    client, dbmod = app
    # Прямая запись в БД с мусором не должна пройти в публичный snapshot
    from app.services.analytics import ensure_analytics_settings

    db = dbmod.SessionLocal()
    try:
        row = ensure_analytics_settings(db)
        row.yandex_metrika_id = '1"><img src=x onerror=alert(1)>'
        row.yandex_metrika_enabled = True
        db.commit()
    finally:
        db.close()
    invalidate_analytics_cache()
    snap = get_analytics_public()
    assert snap.yandex_metrika_id is None
    html = client.get("/").text
    assert "onerror" not in html
    assert "alert(1)" not in html
