"""W-18: публичный раздел /zakon, поиск, sitemap, нормативная база в кабинете."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.models import LegalAct
from app.services.legal_public import normative_for_template, search_acts
from app.services.legal_registry import (
    create_draft_version,
    ensure_legal_registry,
    publish_version,
)
from conftest import login


def test_zakon_catalog_public(app):
    client, _ = app
    r = client.get("/zakon/")
    assert r.status_code == 200
    assert "Законодательство о судебной экспертизе" in r.text
    assert "73-ФЗ" in r.text or "судебно-экспертной" in r.text
    assert "не являются официальным опубликованием" in r.text
    assert 'name="q"' in r.text


def test_zakon_act_page_and_fragments(app):
    client, dbmod = app
    r = client.get("/zakon/73-fz-sudebno-ekspertnaya-deyatelnost")
    assert r.status_code == 200
    assert "судебно-экспертной" in r.text
    assert "первоисточник" in r.text

    r = client.get("/zakon/gpk-ekspertiza")
    assert r.status_code == 200
    assert "ст. 79" in r.text
    assert "Оглавление" in r.text

    r = client.get("/zakon/gost-r-57344-2016")
    assert r.status_code == 200
    assert "стандарт" in r.text.casefold() or "фонд" in r.text.casefold()

    r = client.get("/zakon/no-such-act")
    assert r.status_code == 404


def test_zakon_search_finds_published_text(app):
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        act = db.scalar(
            select(LegalAct).where(LegalAct.slug == "73-fz-sudebno-ekspertnaya-deyatelnost")
        )
        draft = create_draft_version(
            db,
            act_id=act.id,
            body_html=(
                "<p>Статья 1. Предмет регулирования</p>"
                "<p>Эксперт оценивает внушаемость и назначает дополнительную экспертизу.</p>"
            ),
            revision_date=date(2024, 7, 22),
        )
        publish_version(db, draft)
        db.commit()
    finally:
        db.close()

    r = client.get("/zakon/", params={"q": "внушаемость"})
    assert r.status_code == 200
    assert "внушаемость" in r.text.casefold()
    assert "73-fz-sudebno-ekspertnaya-deyatelnost" in r.text or "судебно-экспертной" in r.text

    r = client.get("/zakon/", params={"q": "дополнительная экспертиза"})
    assert r.status_code == 200
    assert "дополнительная экспертиза" in r.text.casefold() or "Результаты" in r.text

    db = dbmod.SessionLocal()
    try:
        hits = search_acts(db, "внушаемость")
        assert hits and hits[0].act.slug == "73-fz-sudebno-ekspertnaya-deyatelnost"
    finally:
        db.close()


def test_sitemap_includes_zakon(app):
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        ensure_legal_registry(db)
        act = db.scalar(
            select(LegalAct).where(LegalAct.slug == "73-fz-sudebno-ekspertnaya-deyatelnost")
        )
        assert act is not None
        draft = create_draft_version(
            db, act_id=act.id, body_html="<p>sitemap body text long enough</p>"
        )
        publish_version(db, draft)
        db.commit()
    finally:
        db.close()

    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert "/zakon/" in r.text
    assert "/zakon/73-fz-sudebno-ekspertnaya-deyatelnost" in r.text
    assert "<lastmod>" in r.text


def test_landing_nav_has_zakon(app):
    client, _ = app
    r = client.get("/")
    assert r.status_code == 200
    assert 'href="/zakon/"' in r.text


def test_document_form_normative_links(app):
    client, dbmod = app
    assert "gpk-ekspertiza" in normative_for_template("Договор_услуги_v2.docx")
    assert "73-fz-sudebno-ekspertnaya-deyatelnost" in normative_for_template(
        "Договор_экспертиза.docx"
    )
    # точное имя из манифеста template_normative.json
    hod = normative_for_template("Ходатайство_о_назначении_экспертизы.docx")
    assert "kas-ekspertiza" in hod
    assert "plenum-vs-28-2010" in hod
    psycho = normative_for_template("Психология_ДРО_с_итогом.docx")
    assert "3185-1-psihiatricheskaya-pomoshch" in psycho
    from app.defaults import empty_requisites
    from app.models import Organization, User, UserRole
    from app.security import hash_password
    from app.services.templates import list_templates

    items = list_templates()
    if not items:
        return
    template_name = next(
        (i["name"] for i in items if "договор" in i["name"].casefold()),
        items[0]["name"],
    )
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="НормОрг", requisites=empty_requisites())
        db.add(org)
        db.flush()
        db.add(
            User(
                org_id=org.id,
                email="norm@example.com",
                password_hash=hash_password("Passw0rd!"),
                role=UserRole.user,
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()
    assert login(client, "norm@example.com", "Passw0rd!").status_code == 303
    r = client.get(f"/cabinet/documents/new/{template_name}")
    assert r.status_code == 200
    assert "Нормативная база" in r.text
    assert "/cabinet/zakon/" in r.text
