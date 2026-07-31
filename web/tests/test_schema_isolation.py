"""Фикстуры двух организаций и тесты изоляции данных (W-02)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.defaults import empty_requisites
from app.models import (
    Contract,
    Counter,
    Counterparty,
    CounterpartySource,
    CounterpartyType,
    Document,
    DocumentFormat,
    Event,
    Organization,
    User,
    UserRole,
)
from app.org_scope import (
    get_contract_for_org,
    get_counterparty_for_org,
    get_document_for_org,
    list_contracts,
    list_counterparties,
    list_documents,
    list_events,
)
from app.privacy import counterparty_list_item, mask_passport
from app.security import hash_password
from fastapi import HTTPException


@pytest.fixture()
def two_orgs(app):
    """Две организации с пользователями, контрагентами, договорами, документами."""
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        org_a = Organization(name="Орг Альфа", requisites=empty_requisites())
        org_b = Organization(name="Орг Бета", requisites=empty_requisites())
        org_a.requisites["организация"]["короткое_название"] = "Альфа"
        org_b.requisites["организация"]["короткое_название"] = "Бета"
        db.add_all([org_a, org_b])
        db.flush()

        user_a = User(
            org_id=org_a.id,
            email="alpha@example.com",
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
        )
        user_b = User(
            org_id=org_b.id,
            email="beta@example.com",
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
        )
        db.add_all([user_a, user_b])
        db.flush()

        cp_a = Counterparty(
            org_id=org_a.id,
            type=CounterpartyType.ul,
            name="ООО АльфаКлиент",
            inn="7701234567",
            passport_series="4500",
            passport_number="123456",
            address="г. Москва, ул. Тайная, д. 1",
            source=CounterpartySource.manual,
        )
        cp_b = Counterparty(
            org_id=org_b.id,
            type=CounterpartyType.ul,
            name="ООО БетаКлиент",
            inn="7701234567",  # тот же ИНН — допустим в другой org
            source=CounterpartySource.manual,
        )
        db.add_all([cp_a, cp_b])
        db.flush()

        contract_a = Contract(
            org_id=org_a.id,
            counterparty_id=cp_a.id,
            template="Договор_услуги_v2.docx",
            number="А-1",
            file_path="files/a/contract.docx",
        )
        contract_b = Contract(
            org_id=org_b.id,
            counterparty_id=cp_b.id,
            template="Договор_услуги_v2.docx",
            number="Б-1",
            file_path="files/b/contract.docx",
        )
        db.add_all([contract_a, contract_b])
        db.flush()

        doc_a = Document(
            org_id=org_a.id,
            contract_id=contract_a.id,
            counterparty_id=cp_a.id,
            template="Договор_услуги_v2.docx",
            number="А-1",
            file_path="files/a/2026-07/doc.docx",
            format=DocumentFormat.docx,
            context={"фио_клиента": "Секретно"},
            created_by=user_a.id,
        )
        doc_b = Document(
            org_id=org_b.id,
            contract_id=contract_b.id,
            counterparty_id=cp_b.id,
            template="Договор_услуги_v2.docx",
            number="Б-1",
            file_path="files/b/2026-07/doc.docx",
            format=DocumentFormat.docx,
            context={},
            created_by=user_b.id,
        )
        db.add_all([doc_a, doc_b])

        db.add_all(
            [
                Counter(org_id=org_a.id, key="dogovor", prefix="А-", value=1, suffix=""),
                Counter(org_id=org_b.id, key="dogovor", prefix="Б-", value=1, suffix=""),
                Event(org_id=org_a.id, user_id=user_a.id, type="login", details={}),
                Event(org_id=org_b.id, user_id=user_b.id, type="login", details={}),
            ]
        )
        db.commit()

        ids = {
            "org_a": org_a.id,
            "org_b": org_b.id,
            "cp_a": cp_a.id,
            "cp_b": cp_b.id,
            "contract_a": contract_a.id,
            "contract_b": contract_b.id,
            "doc_a": doc_a.id,
            "doc_b": doc_b.id,
            "user_a": user_a.id,
            "user_b": user_b.id,
        }
    finally:
        db.close()

    return client, dbmod, ids


def test_requisites_shape(two_orgs):
    _, dbmod, ids = two_orgs
    db = dbmod.SessionLocal()
    try:
        org = db.get(Organization, ids["org_a"])
        assert "организация" in org.requisites
        assert "банк" in org.requisites
        assert "исполнитель" in org.requisites
        assert "прайс" in org.requisites
        assert "склонения" in org.requisites
        assert org.requisites["организация"]["короткое_название"] == "Альфа"
    finally:
        db.close()


def test_list_queries_are_org_scoped(two_orgs):
    _, dbmod, ids = two_orgs
    db = dbmod.SessionLocal()
    try:
        cps_a = list_counterparties(db, ids["org_a"])
        cps_b = list_counterparties(db, ids["org_b"])
        assert {c.id for c in cps_a} == {ids["cp_a"]}
        assert {c.id for c in cps_b} == {ids["cp_b"]}

        assert {c.id for c in list_contracts(db, ids["org_a"])} == {ids["contract_a"]}
        assert {c.id for c in list_contracts(db, ids["org_b"])} == {ids["contract_b"]}

        assert {d.id for d in list_documents(db, ids["org_a"])} == {ids["doc_a"]}
        assert {d.id for d in list_documents(db, ids["org_b"])} == {ids["doc_b"]}

        assert all(e.org_id == ids["org_a"] for e in list_events(db, ids["org_a"]))
        assert all(e.org_id == ids["org_b"] for e in list_events(db, ids["org_b"]))
    finally:
        db.close()


def test_cross_org_get_returns_404(two_orgs):
    _, dbmod, ids = two_orgs
    db = dbmod.SessionLocal()
    try:
        with pytest.raises(HTTPException) as exc:
            get_counterparty_for_org(db, ids["org_a"], ids["cp_b"])
        assert exc.value.status_code == 404

        with pytest.raises(HTTPException) as exc:
            get_contract_for_org(db, ids["org_a"], ids["contract_b"])
        assert exc.value.status_code == 404

        with pytest.raises(HTTPException) as exc:
            get_document_for_org(db, ids["org_a"], ids["doc_b"])
        assert exc.value.status_code == 404
    finally:
        db.close()


def test_http_org_isolation(two_orgs):
    client, _, ids = two_orgs
    from conftest import login

    assert login(client, "alpha@example.com", "Passw0rd!").status_code == 303
    assert client.get(f"/cabinet/org/{ids['org_a']}").status_code == 200
    assert client.get(f"/cabinet/org/{ids['org_b']}").status_code == 404


def test_same_inn_allowed_in_different_orgs(two_orgs):
    _, dbmod, ids = two_orgs
    db = dbmod.SessionLocal()
    try:
        inns = db.scalars(select(Counterparty.inn)).all()
        assert inns.count("7701234567") == 2
    finally:
        db.close()


def test_pdn_masked_in_list_item(two_orgs):
    _, dbmod, ids = two_orgs
    db = dbmod.SessionLocal()
    try:
        cp = db.get(Counterparty, ids["cp_a"])
        item = counterparty_list_item(cp)
        assert "123456" not in item["passport"]
        assert item["passport"].endswith("56")
        assert "Тайная" not in item["address"]
        assert mask_passport("4500", "123456") == "4500 ****56"
    finally:
        db.close()
