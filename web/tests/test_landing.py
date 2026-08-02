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
    assert "Комплект документов экспертизы" in r.text
    assert 'name="csrf_token"' in r.text
    assert "application/ld+json" in r.text
    assert "/privacy" in r.text
    assert "/static/samples/dogovor-fl.pdf" in r.text
    assert "/static/img/hero-app.webp" in r.text
    assert "/static/img/sample-dogovor.webp" in r.text
    assert 'id="how"' in r.text
    assert 'id="features"' in r.text
    assert 'id="faq"' in r.text
    assert "Оставить заявку" in r.text
    assert "Запросить ранний доступ" not in r.text
    assert 'name="inn"' in r.text
    assert "Популярный" in r.text


def test_landing_tariffs_comparison(app):
    client, _ = app
    r = client.get("/tariffs")
    assert r.status_code == 200
    assert "Сравнение функций" in r.text
    assert "ЕГРЮЛ-проверка" in r.text
    assert "lp-tariff-card" in r.text
    assert "Оставить заявку" in r.text
    assert "Запросить доступ" not in r.text


def test_landing_assets_present(app):
    client, _ = app
    for path in (
        "/static/img/hero-app.webp",
        "/static/img/how-step1.webp",
        "/static/img/how-step2.webp",
        "/static/img/how-step3.webp",
        "/static/img/sample-dogovor.webp",
        "/static/img/sample-schet.webp",
        "/static/img/sample-akt.webp",
    ):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.content[:4] == b"RIFF", path


def test_w37_scope_non_landing_templates_untouched():
    """W-37: diff вне landing/* шаблонов и публичной статики лендинга = 0."""
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    out = subprocess.check_output(
        ["git", "-c", "core.quotepath=false", "diff", "--name-only", "main...HEAD"],
        cwd=repo,
        text=True,
    )
    allowed_prefixes = (
        "web/app/templates/landing/",
        "web/app/static/landing.css",
        "web/app/static/img/",
        "web/tests/test_landing.py",
        "scripts/make_samples.py",
        "docs/",
    )
    allowed_exact = {
        "web/app/static/landing.css",
        "web/tests/test_landing.py",
        "scripts/make_samples.py",
    }

    def _norm(path: str) -> str:
        path = path.strip().strip('"')
        # git quotepath octal escapes → utf-8
        if "\\" in path:
            try:
                path = path.encode("utf-8").decode("unicode_escape")
            except UnicodeDecodeError:
                pass
        return path

    paths: set[str] = {_norm(line) for line in out.splitlines() if line.strip()}
    for path in sorted(paths):
        if path in allowed_exact or any(path.startswith(p) for p in allowed_prefixes):
            continue
        raise AssertionError(f"W-37 вне scope: {path}")


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
                "inn": "7707817216",
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
        assert lead.inn == "7707817216"
        assert "пилот" in (lead.comment or "")
    finally:
        db.close()


def test_apply_inn_optional_and_validated(app):
    client, dbmod = app
    token = csrf_from(client, "/")
    with patch("app.routers.landing.notify_admin_new_lead", return_value=True):
        r = client.post(
            "/apply",
            data={
                "email": "noinn@example.com",
                "profile": "Другое",
                "inn": "",
                "comment": "",
                "csrf_token": token,
                "website": "",
            },
            follow_redirects=False,
        )
    assert r.status_code == 201

    token = csrf_from(client, "/")
    r = client.post(
        "/apply",
        data={
            "email": "badinn@example.com",
            "profile": "Другое",
            "inn": "123",
            "comment": "",
            "csrf_token": token,
            "website": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "ИНН" in r.text

    db = dbmod.SessionLocal()
    try:
        assert db.scalar(select(Lead).where(Lead.email == "noinn@example.com")) is not None
        assert db.scalar(select(Lead).where(Lead.email == "badinn@example.com")) is None
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
