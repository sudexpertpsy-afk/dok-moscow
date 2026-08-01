"""W-08: лендинг, заявки, SEO, политика ПДн."""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import select

from app.models import Lead
from conftest import csrf_from, login


def test_landing_home(app):
    client, _ = app
    r = client.get("/")
    assert r.status_code == 200
    assert "Док.Москва" in r.text
    assert "Кабинет экспертной организации" in r.text
    assert 'name="csrf_token"' in r.text
    assert "application/ld+json" in r.text
    assert "/privacy" in r.text
    assert "/static/samples/dogovor-fl.pdf" in r.text


def test_privacy_and_contacts(app):
    client, _ = app
    r = client.get("/privacy")
    assert r.status_code == 200
    assert "Политика обработки" in r.text
    assert "7707817216" in r.text
    r = client.get("/contacts")
    assert r.status_code == 200
    assert "hello@dok.moscow" in r.text
    assert "post@use.moscow" in r.text
    assert "414-20-63" in r.text
    r = client.get("/requisites")
    assert r.status_code == 200
    assert "ООО «УСЭ»" in r.text
    assert "5137746012619" in r.text


def test_robots_and_sitemap(app):
    client, _ = app
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert "Sitemap:" in r.text
    assert "Disallow: /invite" in r.text
    assert "Disallow: /apply" in r.text
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert "application/xml" in r.headers.get("content-type", "")
    assert "/privacy" in r.text


def test_favicon_and_html_404(app):
    client, _ = app
    assert client.get("/static/favicon.svg").status_code == 200
    assert client.get("/static/favicon.ico").status_code == 200
    r = client.get("/нет-такой-страницы", headers={"Accept": "text/html"})
    assert r.status_code == 404
    assert "Страница не найдена" in r.text
    assert "На главную" in r.text
    assert "application/json" not in r.headers.get("content-type", "")
    r_json = client.get("/нет-такой-страницы", headers={"Accept": "application/json"})
    assert r_json.status_code == 404
    assert r_json.json()["detail"] == "Not Found"


def test_wcag_skip_and_focus_styles(app):
    client, _ = app
    r = client.get("/")
    assert 'href="#main"' in r.text
    assert 'id="main"' in r.text
    assert 'tabindex="-1"' in r.text
    css = client.get("/static/app.css").text
    assert "focus-visible" in css
    assert ".skip" in css
    r = client.get("/login")
    assert 'class="skip"' in r.text
    assert 'href="#main"' in r.text


def test_sample_pdf_served(app):
    client, _ = app
    from io import BytesIO

    from pypdf import PdfReader

    for path, min_pages in (
        ("/static/samples/dogovor-fl.pdf", 3),
        ("/static/samples/schet.pdf", 1),
        ("/static/samples/akt.pdf", 1),
    ):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.content[:4] == b"%PDF", path
        # Образцы — из реальных шаблонов (не одностраничные заглушки ReportLab).
        pages = len(PdfReader(BytesIO(r.content)).pages)
        assert pages >= min_pages, f"{path}: ожидалось ≥{min_pages} стр., получено {pages}"
        # Договор-витрина должен быть многостраничным.
        if "dogovor" in path:
            assert pages >= 5


def test_apply_requires_csrf(app):
    client, _ = app
    r = client.post(
        "/apply",
        data={
            "email": "lead@example.com",
            "profile": "Экспертная организация (СРО)",
            "comment": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_apply_saves_lead_and_notifies(app):
    client, dbmod = app
    token = csrf_from(client, "/")
    with patch("app.routers.landing.notify_admin_new_lead", return_value=True) as notify:
        r = client.post(
            "/apply",
            data={
                "email": "Lead@Example.COM",
                "profile": "Независимый эксперт / ИП",
                "comment": "Нужен пилот на 3 эксперта",
                "csrf_token": token,
                "website": "",
            },
            follow_redirects=False,
        )
    assert r.status_code == 201
    assert "Заявка принята" in r.text
    assert notify.called

    db = dbmod.SessionLocal()
    try:
        lead = db.scalar(select(Lead).where(Lead.email == "lead@example.com"))
        assert lead is not None
        assert lead.profile == "Независимый эксперт / ИП"
        assert "пилот" in (lead.comment or "")
    finally:
        db.close()


def test_honeypot_ignored(app):
    client, dbmod = app
    token = csrf_from(client, "/")
    r = client.post(
        "/apply",
        data={
            "email": "bot@example.com",
            "profile": "Другое",
            "comment": "spam",
            "csrf_token": token,
            "website": "http://spam.test",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    db = dbmod.SessionLocal()
    try:
        assert db.scalar(select(Lead).where(Lead.email == "bot@example.com")) is None
    finally:
        db.close()


def test_admin_sees_leads(app):
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        db.add(Lead(email="seen@example.com", profile="Другое", comment=None))
        db.commit()
    finally:
        db.close()

    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    r = client.get("/admin/leads")
    assert r.status_code == 200
    assert "Заявки с лендинга" in r.text
    assert "seen@example.com" in r.text


def test_notify_without_smtp_logs(app, caplog):
    from app.config import get_settings
    from app.services.leads import notify_admin_new_lead

    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        lead = Lead(email="x@y.z", profile="Другое")
        db.add(lead)
        db.commit()
        db.refresh(lead)
        with caplog.at_level("INFO", logger="dok.mail"):
            assert notify_admin_new_lead(get_settings(), lead) is False
        assert "Письмо без SMTP" in caplog.text
    finally:
        db.close()
