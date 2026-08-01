"""W-05: картотека и DaData (мок)."""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import select

from app.defaults import empty_requisites
from app.models import Counterparty, CounterpartySource, CounterpartyType, Organization, User, UserRole
from app.security import hash_password
from app.services.dadata import SuggestItem, clear_dadata_cache, party_to_counterparty_fields, suggest
from app.privacy import counterparty_list_item
from conftest import csrf_from, login


def _seed(dbmod, email="cp@example.com"):
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="ДаДатаОрг", requisites=empty_requisites())
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id,
            email=email,
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
        )
        db.add(user)
        db.commit()
        return org.id, user.id
    finally:
        db.close()


def test_manual_ul_crud_and_masking(app):
    client, dbmod = app
    _seed(dbmod)
    assert login(client, "cp@example.com", "Passw0rd!").status_code == 303

    csrf = csrf_from(client, "/cabinet/counterparties/new?type=ul")
    # валидный ИНН 10 знаков: 7707083893 (Сбер)
    r = client.post(
        "/cabinet/counterparties/new",
        data={
            "csrf_token": csrf,
            "type": "ul",
            "name": "ООО Ромашка",
            "inn": "7707083893",
            "kpp": "770701001",
            "ogrn": "1027700132195",
            "address": "г. Москва, ул. Секретная, д. 10",
            "fio": "Иванов Иван Иванович",
            "source": "manual",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text[:500]
    cp_id = int(r.headers["location"].rstrip("/").split("/")[-1])

    r = client.get("/cabinet/counterparties/")
    assert r.status_code == 200
    assert "Ромашка" in r.text
    assert "Секретная" not in r.text  # адрес замаскирован в списке

    r = client.get(f"/cabinet/counterparties/{cp_id}")
    assert r.status_code == 200
    assert "7707083893" in r.text

    # чужая org
    _seed(dbmod, email="other-cp@example.com")
    token = csrf_from(client, "/cabinet/counterparties/")
    client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert login(client, "other-cp@example.com", "Passw0rd!").status_code == 303
    assert client.get(f"/cabinet/counterparties/{cp_id}").status_code == 404


def test_validation_rejects_bad_inn(app):
    client, dbmod = app
    _seed(dbmod, email="badinn@example.com")
    assert login(client, "badinn@example.com", "Passw0rd!").status_code == 303
    csrf = csrf_from(client, "/cabinet/counterparties/new?type=ul")
    r = client.post(
        "/cabinet/counterparties/new",
        data={
            "csrf_token": csrf,
            "type": "ul",
            "name": "ООО",
            "inn": "123",
            "source": "manual",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "ИНН" in r.text


def test_dadata_suggest_mocked(app):
    client, dbmod = app
    clear_dadata_cache()
    org_id, user_id = _seed(dbmod, email="dd@example.com")
    assert login(client, "dd@example.com", "Passw0rd!").status_code == 303

    fake = [
        SuggestItem(
            value='ООО "ЯНДЕКС"',
            data={
                "inn": "7736207543",
                "kpp": "770401001",
                "ogrn": "1027700229193",
                "name": {"full_with_opf": 'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "ЯНДЕКС"'},
                "address": {"unrestricted_value": "г Москва, ул Льва Толстого, д 16"},
                "management": {"name": "Садовский А.В."},
            },
        )
    ]

    with patch("app.services.dadata._request", return_value=[
        {"value": fake[0].value, "data": fake[0].data}
    ]):
        # без ключа _request не вызывается — включим ключ
        from app.config import get_settings

        get_settings.cache_clear()
        s = get_settings()
        object.__setattr__(s, "dadata_key", "test-key")

        r = client.get("/cabinet/counterparties/suggest/party?inn=7736207543")
        assert r.status_code == 200
        assert "ЯНДЕКС" in r.text or "Яндекс" in r.text.upper() or "ЯНДЕКС" in r.text
        assert "7736207543" in r.text

        fields = party_to_counterparty_fields(fake[0])
        assert fields["inn"] == "7736207543"
        assert "ЯНДЕКС" in fields["name"].upper()

        get_settings.cache_clear()


def test_dadata_soft_degrade_without_key(app):
    client, dbmod = app
    clear_dadata_cache()
    _seed(dbmod, email="nokey@example.com")
    assert login(client, "nokey@example.com", "Passw0rd!").status_code == 303
    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    object.__setattr__(s, "dadata_key", "")
    r = client.get("/cabinet/counterparties/suggest/party?q=тест")
    assert r.status_code == 200
    # пустой список — нет кнопок
    assert "button" not in r.text.lower() or r.text.count("<button") == 0
    get_settings.cache_clear()


def test_list_item_masks_passport():
    cp = Counterparty(
        org_id=1,
        type=CounterpartyType.fl,
        fio="Тестов",
        passport_series="4500",
        passport_number="123456",
        address="г. Москва, очень секретный адрес",
        source=CounterpartySource.manual,
    )
    item = counterparty_list_item(cp)
    assert "123456" not in item["passport"]
    assert "секретный" not in item["address"]
