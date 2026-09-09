"""Регрессия: выбор из картотеки заполняет поля шага 1 комплекта."""

from __future__ import annotations

import re

from app.defaults import empty_requisites
from app.models import (
    Counterparty,
    CounterpartySource,
    CounterpartyType,
    Organization,
    User,
    UserRole,
)
from app.security import hash_password
from conftest import login


def _seed(dbmod, email="cp-prefill@example.com"):
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="PrefillOrg", requisites=empty_requisites())
        db.add(org)
        db.flush()
        db.add(
            User(
                org_id=org.id,
                email=email,
                password_hash=hash_password("Passw0rd!"),
                role=UserRole.user,
                is_active=True,
            )
        )
        cp = Counterparty(
            org_id=org.id,
            type=CounterpartyType.ul,
            name='ООО "МаркерПрефилл"',
            inn="7707083893",
            kpp="770701001",
            address="г. Москва, ул. Тестовая, д. 1",
            fio="Иванов Иван Иванович",
            bank_bik="044525225",
            bank_name="Сбербанк",
            bank_account="40702810123456789012",
            source=CounterpartySource.manual,
        )
        db.add(cp)
        db.commit()
        return org.id, cp.id
    finally:
        db.close()


def _input_value(html: str, name: str) -> str | None:
    # value="..." у input name="..."
    m = re.search(
        rf'name="{re.escape(name)}"[^>]*value="([^"]*)"',
        html,
    )
    if m:
        return m.group(1)
    m = re.search(
        rf'value="([^"]*)"[^>]*name="{re.escape(name)}"',
        html,
    )
    return m.group(1) if m else None


def test_package_step1_prefills_from_catalog(app):
    client, dbmod = app
    _, cp_id = _seed(dbmod)
    assert login(client, "cp-prefill@example.com", "Passw0rd!").status_code == 303

    r = client.get(f"/cabinet/package/?counterparty_id={cp_id}")
    assert r.status_code == 200
    assert "МаркерПрефилл" in r.text
    assert _input_value(r.text, "название_заказчика") == 'ООО &quot;МаркерПрефилл&quot;' or \
        'ООО "МаркерПрефилл"' in (_input_value(r.text, "название_заказчика") or "") or \
        "МаркерПрефилл" in (_input_value(r.text, "название_заказчика") or "")
    assert _input_value(r.text, "инн_заказчика") == "7707083893"
    assert "770701001" in (_input_value(r.text, "кпп_заказчика") or "")
    assert "Тестовая" in (_input_value(r.text, "юр_адрес_заказчика") or "")
    assert "Иванов" in (_input_value(r.text, "фио_подписанта") or "")


def test_document_form_prefills_from_catalog(app):
    client, dbmod = app
    _, cp_id = _seed(dbmod, email="doc-prefill@example.com")
    assert login(client, "doc-prefill@example.com", "Passw0rd!").status_code == 303

    r = client.get(
        f"/cabinet/documents/new/Договор_услуги_юрлицо.docx?counterparty_id={cp_id}"
    )
    assert r.status_code == 200
    assert 'name="counterparty_id"' in r.text
    assert "Из картотеки" in r.text
    assert _input_value(r.text, "инн_заказчика") == "7707083893"
    assert "МаркерПрефилл" in (_input_value(r.text, "название_заказчика") or r.text)
    assert "Тестовая" in (_input_value(r.text, "юр_адрес_заказчика") or "")
