"""W-06: журнал, поиск, настройки."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.defaults import empty_requisites
from app.models import (
    Counter,
    Counterparty,
    CounterpartySource,
    CounterpartyType,
    Document,
    DocumentFormat,
    Organization,
    User,
    UserRole,
)
from app.security import hash_password
from app.services.journal import list_journal, search_all
from app.services.settings_svc import ensure_requisites, update_section
from conftest import csrf_from, login


def _seed(dbmod, email="w06@example.com"):
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="ЖурналОрг", requisites=empty_requisites())
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id,
            email=email,
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
        )
        db.add(user)
        cp = Counterparty(
            org_id=org.id,
            type=CounterpartyType.fl,
            fio="Иванов Иван Иванович",
            passport_series="4500",
            passport_number="111222",
            address="г. Москва, ул. Тайная, 1",
            source=CounterpartySource.manual,
        )
        db.add(cp)
        db.flush()
        doc = Document(
            org_id=org.id,
            counterparty_id=cp.id,
            template="Договор_услуги_v2.docx",
            number="Д-42",
            file_path=f"{org.id}/2026-07/demo.docx",
            format=DocumentFormat.docx,
            context={"фио_клиента": "Иванов Иван Иванович"},
            created_by=user.id,
            created_at=datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
        )
        db.add(doc)
        db.add(Counter(org_id=org.id, key="dogovor", prefix="Д-", value=42, suffix=""))
        db.commit()
        return org.id, user.id, cp.id, doc.id
    finally:
        db.close()


def test_journal_filters_and_pagination(app):
    client, dbmod = app
    org_id, _, cp_id, doc_id = _seed(dbmod)
    assert login(client, "w06@example.com", "Passw0rd!").status_code == 303

    r = client.get("/cabinet/journal")
    assert r.status_code == 200
    assert "Договор_услуги_v2.docx" in r.text
    assert "Д-42" in r.text

    r = client.get("/cabinet/journal?template=Договор_услуги_v2.docx")
    assert r.status_code == 200
    assert "Д-42" in r.text

    r = client.get(f"/cabinet/journal?counterparty_id={cp_id}")
    assert r.status_code == 200
    assert "Иванов" in r.text

    r = client.get("/cabinet/journal?from=2026-07-01&to=2026-07-31")
    assert r.status_code == 200
    assert "Д-42" in r.text

    r = client.get("/cabinet/journal?from=2025-01-01&to=2025-01-31")
    assert r.status_code == 200
    assert "Д-42" not in r.text

    db = dbmod.SessionLocal()
    try:
        rows, total = list_journal(db, org_id, template="Договор_услуги_v2.docx")
        assert total == 1
        assert rows[0].id == doc_id
    finally:
        db.close()


def test_search_ivanov(app):
    client, dbmod = app
    _seed(dbmod, email="search@example.com")
    assert login(client, "search@example.com", "Passw0rd!").status_code == 303
    r = client.get("/cabinet/search?q=Иванов")
    assert r.status_code == 200
    assert "Иванов" in r.text
    assert "Договор_услуги_v2.docx" in r.text
    # ПДн маскируются в выдаче контрагентов
    assert "111222" not in r.text


def test_settings_requisites_affect_org(app):
    client, dbmod = app
    org_id, _, _, _ = _seed(dbmod, email="set@example.com")
    assert login(client, "set@example.com", "Passw0rd!").status_code == 303

    csrf = csrf_from(client, "/cabinet/settings/")
    r = client.post(
        "/cabinet/settings/",
        data={
            "csrf_token": csrf,
            "короткое_название": "ООО УСЭ",
            "инн": "7707083893",
            "email": "office@use.test",
            "полное_название": "",
            "кпп": "",
            "огрн": "",
            "юр_адрес": "Москва",
            "почтовый_адрес": "",
            "телефон": "",
            "лицензия": "",
            "окпо": "",
            "город": "г. Москва",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "сохранены" in r.text.lower() or "Реквизиты" in r.text

    db = dbmod.SessionLocal()
    try:
        org = db.get(Organization, org_id)
        ensure_requisites(org)
        assert org.requisites["организация"]["короткое_название"] == "ООО УСЭ"
        assert org.requisites["организация"]["инн"] == "7707083893"
    finally:
        db.close()


def test_settings_price_and_counters(app):
    client, dbmod = app
    org_id, _, _, _ = _seed(dbmod, email="price@example.com")
    assert login(client, "price@example.com", "Passw0rd!").status_code == 303

    csrf = csrf_from(client, "/cabinet/settings/price")
    r = client.post(
        "/cabinet/settings/price",
        data={
            "csrf_token": csrf,
            "сппэ": "55000",
            "кспэ": "50000",
            "рецензия": "30000",
            "обучение_спэ": "30000",
            "обучение_полиграф": "30000",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200

    db = dbmod.SessionLocal()
    try:
        org = db.get(Organization, org_id)
        assert int(org.requisites["прайс"]["сппэ"]) == 55000
    finally:
        db.close()

    csrf = csrf_from(client, "/cabinet/settings/counters")
    r = client.post(
        "/cabinet/settings/counters",
        data={
            "csrf_token": csrf,
            "key": "dogovor",
            "prefix": "Д-",
            "value": "100",
            "suffix": "",
            "confirm": "1",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "100" in r.text or "101" in r.text

    db = dbmod.SessionLocal()
    try:
        c = db.get(Counter, {"org_id": org_id, "key": "dogovor"})
        assert c is not None
        assert c.value == 100
    finally:
        db.close()


def test_search_isolation(app):
    client, dbmod = app
    _seed(dbmod, email="a06@example.com")
    _seed(dbmod, email="b06@example.com")
    assert login(client, "b06@example.com", "Passw0rd!").status_code == 303
    # у b тоже есть Иванов в своей org — ок
    r = client.get("/cabinet/search?q=Иванов")
    assert r.status_code == 200
    # сервисный уровень: поиск только своей org
    db = dbmod.SessionLocal()
    try:
        org_b = db.scalar(select(Organization).where(Organization.name == "ЖурналОрг").order_by(Organization.id.desc()))
        # both named same — get user b org
        user_b = db.scalar(select(User).where(User.email == "b06@example.com"))
        res = search_all(db, user_b.org_id, "Иванов")
        assert all(
            db.get(Counterparty, c["id"]).org_id == user_b.org_id for c in res["counterparties"]
        )
    finally:
        db.close()
