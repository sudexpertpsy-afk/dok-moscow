"""Снижение отказов на главной: демо, модалка образцов, API, praktika."""

from __future__ import annotations

from unittest.mock import patch

from app.hosting import path_surface
from app.services.landing_demo import beta_promo_copy, load_demo_examples


def test_landing_bounce_markup(app):
    client, _ = app
    r = client.get("/")
    assert r.status_code == 200
    text = r.text
    assert 'id="lp-demo"' in text
    assert "Попробовать демо" in text
    assert 'data-sample-doc="dogovor-fl"' in text
    assert 'id="lp-sample-modal"' in text
    assert 'id="lp-calc"' in text
    assert 'id="lp-sticky-apply"' in text
    assert "Полезное для эксперта" in text
    assert "/praktika/" in text
    assert "landing.js" in text or "landing." in text
    # прямых target=_blank на samples в карточках быть не должно (модалка)
    assert 'href="/static/samples/dogovor-fl.pdf" target="_blank"' not in text
    assert "Заявка без оплаты" in text
    assert "/signup?tariff=" in text
    assert "Создать кабинет" in text
    assert "Первые 20 подписчиков беты" not in text
    assert "50% навсегда" not in text

def test_sample_pdf_still_served(app):
    client, _ = app
    r = client.get("/static/samples/dogovor-fl.pdf")
    assert r.status_code == 200
    assert r.content[:4] == b"%PDF"


def test_demo_api_public_surface():
    assert path_surface("/api/demo/egrul") == "public"
    assert path_surface("/praktika/") == "public"


def test_demo_api_bad_inn(app):
    client, _ = app
    r = client.get("/api/demo/egrul?inn=123")
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_demo_api_fallback_without_dadata(app):
    client, _ = app
    with patch("app.routers.demo_api.find_party_public", return_value=None):
        r = client.get("/api/demo/egrul?inn=7707083893")
    assert r.status_code == 404
    assert r.json().get("fallback") is True


def test_demo_api_success_mocked(app):
    from app.services.dadata import PartyCard

    card = PartyCard(
        inn="7707083893",
        name_short="ПАО Сбербанк",
        name_full="ПАО Сбербанк",
        ogrn="1027700132195",
        address="Москва",
        management_post="Председатель",
        management_name="Тест",
        status="ACTIVE",
        status_label="действует",
    )
    client, _ = app
    with patch("app.routers.demo_api.find_party_public", return_value=card):
        r = client.get("/api/demo/egrul?inn=7707083893")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["party"]["inn"] == "7707083893"


def test_demo_examples_loaded():
    examples = load_demo_examples()
    assert len(examples) >= 3
    assert all(ex.get("inn") for ex in examples)


def test_apply_form_title_without_beta_promo(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        copy = beta_promo_copy(db)
        assert copy["show_remaining"] is False
        assert copy["title"] == "Оставить заявку"
        assert "50%" not in copy["title"]
    finally:
        db.close()


def test_praktika_page(app):
    client, _ = app
    r = client.get("/praktika/")
    assert r.status_code == 200
    # Есть markdown-статьи — индекс, иначе маркетинговая заглушка.
    assert ("Практика" in r.text) and (
        "rekvizity-zaklyucheniya" in r.text
        or "Сделано практикой" in r.text
        or "/praktika/" in r.text
    )
    assert "/obraztsy" in r.text or "/zakon/" in r.text


def test_praktika_article_detail(app):
    client, _ = app
    r = client.get("/praktika/rekvizity-zaklyucheniya")
    assert r.status_code == 200
    assert "Практика" in r.text


def test_w44_public_segment_pages(app):
    client, _ = app
    for path in (
        "/dlya-ekspertov",
        "/dlya-organizatsiy",
        "/dlya-uchebnykh-tsentrov",
        "/bezopasnost",
        "/novoe",
        "/obraztsy/",
    ):
        r = client.get(path)
        assert r.status_code == 200, path


def test_w44_public_surface_hosting():
    from app.hosting import path_surface

    for path in (
        "/dlya-ekspertov",
        "/dlya-organizatsiy",
        "/dlya-uchebnykh-tsentrov",
        "/bezopasnost",
        "/novoe",
    ):
        assert path_surface(path) == "public", path


def test_zakon_has_cross_cta(app):
    client, _ = app
    r = client.get("/zakon/")
    assert r.status_code == 200
    assert "Собрать комплект документов за 5 минут" in r.text


def test_csp_allows_self_frame():
    from app.security import build_content_security_policy

    csp = build_content_security_policy(metrika=True, webvisor=True)
    assert "frame-src" in csp
    assert "'self'" in csp


def test_csp_allows_tbank_form_action():
    """303 на PaymentURL после POST оплаты не должен резаться CSP form-action."""
    from app.security import build_content_security_policy

    csp = build_content_security_policy()
    assert "form-action" in csp
    assert "https://pay.tbank.ru" in csp
    assert "https://securepay.tinkoff.ru" in csp
