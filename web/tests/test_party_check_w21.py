"""W-21: раздел проверки контрагента."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import select

from app.defaults import empty_requisites
from app.models import (
    Counterparty,
    CounterpartySource,
    CounterpartyType,
    Organization,
    PartyCheck,
    PaymentSettings,
    Subscription,
    SubscriptionStatus,
    Tariff,
    TariffCode,
    User,
    UserRole,
    utcnow,
)
from app.security import hash_password
from app.services.billing import ensure_beta_subscriptions, ensure_payment_settings, ensure_tariffs
from app.services.dadata import PartyCard, clear_dadata_cache, parse_party_suggestion
from conftest import csrf_from, login


YANDEX_RAW = {
    "value": 'ООО "ЯНДЕКС"',
    "data": {
        "inn": "7736207543",
        "kpp": "770401001",
        "ogrn": "1027700229193",
        "type": "LEGAL",
        "name": {
            "full_with_opf": 'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "ЯНДЕКС"',
            "short_with_opf": 'ООО "ЯНДЕКС"',
        },
        "state": {
            "status": "ACTIVE",
            "registration_date": 978307200000,
        },
        "address": {"unrestricted_value": "г Москва, ул Льва Толстого, д 16"},
        "management": {"name": "Садовский А.В.", "post": "ГЕНЕРАЛЬНЫЙ ДИРЕКТОР"},
        "capital": {"value": 100000, "type": "руб."},
        "okved": "62.01",
        "okveds": [
            {"code": "62.01", "name": "Разработка ПО", "main": True},
            {"code": "62.02", "name": "Консультирование", "main": False},
        ],
        "finance": {"employee_count": 10000},
        "authorities": {
            "fts_registration": {"code": "7704", "name": "ИФНС № 4"},
        },
        "branch_type": "MAIN",
        "branch_count": 3,
    },
}


def _seed(dbmod, email="party@example.com", *, tariff: TariffCode = TariffCode.specialist):
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        ensure_payment_settings(db)
        org = Organization(name="ПроверкаОрг", requisites=empty_requisites())
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id,
            email=email,
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
            is_active=True,
        )
        db.add(user)
        ensure_beta_subscriptions(db)
        tariff_row = db.scalar(select(Tariff).where(Tariff.code == tariff))
        sub = db.scalar(select(Subscription).where(Subscription.org_id == org.id))
        sub.tariff_id = tariff_row.id
        sub.status = SubscriptionStatus.active
        sub.ends_at = utcnow() + timedelta(days=30)
        sub.is_beta = False
        db.commit()
        return org.id, email
    finally:
        db.close()


def _enable_dadata():
    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    object.__setattr__(s, "dadata_key", "test-key")
    clear_dadata_cache()
    return s


def test_parse_party_card_fields():
    card = parse_party_suggestion(YANDEX_RAW)
    assert card is not None
    assert card.inn == "7736207543"
    assert card.status == "ACTIVE"
    assert card.status_label == "действует"
    assert card.status_tone == "ok"
    rows = dict(card.display_rows())
    assert "ИНН" in rows
    assert rows["ИНН"] == "7736207543"
    assert "Краткое наименование" in rows
    assert "Руководитель" in rows
    assert "Уставный капитал" in rows
    assert "ОКВЭД основной" in rows
    assert "Численность" in rows


def test_guest_sees_paywall(app):
    client, dbmod = app
    _seed(dbmod, email="guest-pc@example.com", tariff=TariffCode.guest)
    assert login(client, "guest-pc@example.com", "Passw0rd!").status_code == 303
    r = client.get("/cabinet/party-check/")
    assert r.status_code == 200
    assert "Выбрать тариф" in r.text
    assert "Специалист" in r.text


def test_search_empty_query(app):
    client, dbmod = app
    _seed(dbmod, email="empty-pc@example.com")
    assert login(client, "empty-pc@example.com", "Passw0rd!").status_code == 303
    csrf = csrf_from(client, "/cabinet/party-check/")
    r = client.post(
        "/cabinet/party-check/search",
        data={"csrf_token": csrf, "query": ""},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "Введите" in r.text


def test_search_by_inn_opens_card(app):
    client, dbmod = app
    _seed(dbmod, email="inn-pc@example.com")
    assert login(client, "inn-pc@example.com", "Passw0rd!").status_code == 303
    _enable_dadata()

    with patch("app.services.dadata._request", return_value=[YANDEX_RAW]):
        csrf = csrf_from(client, "/cabinet/party-check/")
        r = client.post(
            "/cabinet/party-check/search",
            data={"csrf_token": csrf, "query": "7736207543"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/cabinet/party-check/card" in r.headers["location"]

        card_page = client.get(r.headers["location"])
        assert card_page.status_code == 200
        assert "7736207543" in card_page.text
        assert "действует" in card_page.text
        assert "Добавить в контрагенты" in card_page.text
        assert "Скачать карточку (PDF)" in card_page.text

    db = dbmod.SessionLocal()
    try:
        rows = db.scalars(select(PartyCheck)).all()
        assert len(rows) == 1
        assert rows[0].inn == "7736207543"
        assert rows[0].status == "ACTIVE"
        assert rows[0].snapshot
    finally:
        db.close()


def test_search_by_name_lists_candidates(app):
    client, dbmod = app
    _seed(dbmod, email="name-pc@example.com")
    assert login(client, "name-pc@example.com", "Passw0rd!").status_code == 303
    _enable_dadata()

    with patch("app.services.dadata._request", return_value=[YANDEX_RAW]):
        csrf = csrf_from(client, "/cabinet/party-check/")
        r = client.post(
            "/cabinet/party-check/search",
            data={"csrf_token": csrf, "query": "Яндекс"},
            follow_redirects=False,
        )
        assert r.status_code == 200
        assert "Найденные организации" in r.text
        assert "7736207543" in r.text


def test_daily_limit(app):
    client, dbmod = app
    org_id, email = _seed(dbmod, email="limit-pc@example.com")
    assert login(client, email, "Passw0rd!").status_code == 303
    _enable_dadata()

    db = dbmod.SessionLocal()
    try:
        settings = db.get(PaymentSettings, 1)
        settings.party_check_daily_limit = 1
        db.commit()
    finally:
        db.close()

    with patch("app.services.dadata._request", return_value=[YANDEX_RAW]):
        csrf = csrf_from(client, "/cabinet/party-check/")
        r1 = client.post(
            "/cabinet/party-check/search",
            data={"csrf_token": csrf, "query": "7736207543"},
            follow_redirects=False,
        )
        assert r1.status_code == 303

        csrf = csrf_from(client, "/cabinet/party-check/")
        r2 = client.post(
            "/cabinet/party-check/search",
            data={"csrf_token": csrf, "query": "7707083893"},
            follow_redirects=False,
        )
        assert r2.status_code == 400
        assert "лимит" in r2.text.lower()


def test_add_and_update_counterparty_with_diff(app):
    client, dbmod = app
    org_id, email = _seed(dbmod, email="save-pc@example.com")
    assert login(client, email, "Passw0rd!").status_code == 303
    _enable_dadata()

    with patch("app.services.dadata._request", return_value=[YANDEX_RAW]):
        csrf = csrf_from(client, "/cabinet/party-check/")
        client.post(
            "/cabinet/party-check/search",
            data={"csrf_token": csrf, "query": "7736207543"},
            follow_redirects=False,
        )
        csrf = csrf_from(client, "/cabinet/party-check/card?inn=7736207543")
        r = client.post(
            "/cabinet/party-check/save",
            data={"csrf_token": csrf, "inn": "7736207543"},
            follow_redirects=False,
        )
        assert r.status_code == 200
        assert "добавлен" in r.text.lower() or "Добавить" not in r.text or "Обновить" in r.text

    db = dbmod.SessionLocal()
    try:
        cp = db.scalar(
            select(Counterparty).where(
                Counterparty.org_id == org_id, Counterparty.inn == "7736207543"
            )
        )
        assert cp is not None
        assert cp.egrul_status == "ACTIVE"
        assert cp.egrul_checked_at is not None
        cp.name = "Старое название"
        db.commit()
    finally:
        db.close()

    clear_dadata_cache()
    with patch("app.services.dadata._request", return_value=[YANDEX_RAW]):
        csrf = csrf_from(client, "/cabinet/party-check/")
        client.post(
            "/cabinet/party-check/search",
            data={"csrf_token": csrf, "query": "7736207543"},
            follow_redirects=False,
        )
        csrf = csrf_from(client, "/cabinet/party-check/card?inn=7736207543")
        r = client.post(
            "/cabinet/party-check/save",
            data={"csrf_token": csrf, "inn": "7736207543"},
            follow_redirects=False,
        )
        assert r.status_code == 200
        assert "Обновить" in r.text or "обновлена" in r.text.lower()
        assert "Изменения" in r.text or "Старое название" in r.text


def test_journal_and_pdf(app):
    client, dbmod = app
    _seed(dbmod, email="journal-pc@example.com")
    assert login(client, "journal-pc@example.com", "Passw0rd!").status_code == 303
    _enable_dadata()

    with patch("app.services.dadata._request", return_value=[YANDEX_RAW]):
        csrf = csrf_from(client, "/cabinet/party-check/")
        client.post(
            "/cabinet/party-check/search",
            data={"csrf_token": csrf, "query": "7736207543"},
            follow_redirects=False,
        )

        journal = client.get("/cabinet/party-check/journal")
        assert journal.status_code == 200
        assert "7736207543" in journal.text
        assert "действует" in journal.text

        pdf = client.get("/cabinet/party-check/pdf?inn=7736207543")
        assert pdf.status_code == 200
        assert pdf.headers["content-type"].startswith("application/pdf")
        assert pdf.content[:4] == b"%PDF"
        assert b"7736207543" in pdf.content or b"INN" in pdf.content


def test_create_package_prefills_step1(app):
    client, dbmod = app
    _seed(dbmod, email="pkg-pc@example.com")
    assert login(client, "pkg-pc@example.com", "Passw0rd!").status_code == 303
    _enable_dadata()

    with patch("app.services.dadata._request", return_value=[YANDEX_RAW]):
        csrf = csrf_from(client, "/cabinet/party-check/")
        client.post(
            "/cabinet/party-check/search",
            data={"csrf_token": csrf, "query": "7736207543"},
            follow_redirects=False,
        )
        csrf = csrf_from(client, "/cabinet/party-check/card?inn=7736207543")
        r = client.post(
            "/cabinet/party-check/package",
            data={"csrf_token": csrf, "inn": "7736207543"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/cabinet/package/" in r.headers["location"]

        step1 = client.get("/cabinet/package/")
        assert step1.status_code == 200
        assert "7736207543" in step1.text


def test_admin_party_check_limit_setting(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    db = dbmod.SessionLocal()
    try:
        ensure_payment_settings(db)
        db.commit()
    finally:
        db.close()
    token = csrf_from(client, "/admin/payment-settings")
    r = client.post(
        "/admin/payment-settings",
        data={
            "csrf_token": token,
            "terminal_key": "",
            "password": "",
            "mode": "test",
            "taxation": "usn_income",
            "vat_rate": "none",
            "default_receipt_email": "",
            "party_check_daily_limit": "42",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    db = dbmod.SessionLocal()
    try:
        row = db.get(PaymentSettings, 1)
        assert row.party_check_daily_limit == 42
    finally:
        db.close()


def test_stale_egrul_warning_in_package(app):
    client, dbmod = app
    org_id, email = _seed(dbmod, email="stale-pc@example.com")
    assert login(client, email, "Passw0rd!").status_code == 303

    db = dbmod.SessionLocal()
    try:
        cp = Counterparty(
            org_id=org_id,
            type=CounterpartyType.ul,
            name='ООО "ЯНДЕКС"',
            inn="7736207543",
            kpp="770401001",
            ogrn="1027700229193",
            source=CounterpartySource.manual,
            egrul_status="ACTIVE",
            egrul_checked_at=utcnow() - timedelta(days=45),
        )
        db.add(cp)
        db.commit()
        cp_id = cp.id
    finally:
        db.close()

    liquidated = dict(YANDEX_RAW)
    liquidated = {
        **YANDEX_RAW,
        "data": {**YANDEX_RAW["data"], "state": {"status": "LIQUIDATED", "registration_date": 1}},
    }
    _enable_dadata()
    with patch("app.services.dadata._request", return_value=[liquidated]):
        r = client.get(f"/cabinet/package/?counterparty_id={cp_id}")
        assert r.status_code == 200
        assert "alert-warn" in r.text
        assert "ЕГРЮЛ" in r.text
