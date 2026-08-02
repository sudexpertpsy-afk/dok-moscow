"""Банк в настройках → подстановка в счёт на оплату."""

from __future__ import annotations

import re
from pathlib import Path
from zipfile import ZipFile

from app.defaults import empty_requisites
from app.models import Organization, User, UserRole
from app.security import hash_password
from app.services.settings_svc import (
    bank_from_form,
    bank_is_complete,
    ensure_requisites,
    normalize_bank_block,
)
from app.services.templates import absolute_file, generate_docx
from conftest import csrf_from, login

# Рабочая пара р/с + БИК (Сбер, как в фикстурах)
_ACCOUNT = "40702810338000013478"
_BIK = "044525225"
_BANK = 'ПАО "СБЕРБАНК РОССИИ"'
_CORR = "30101810400000000225"


def _seed(dbmod, email="bank@example.com"):
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="БанкОрг", requisites=empty_requisites())
        org.requisites["организация"]["полное_название"] = "ООО БанкОрг"
        org.requisites["организация"]["инн"] = "7707817216"
        org.requisites["организация"]["кпп"] = "770701001"
        org.requisites["организация"]["юр_адрес"] = "Москва"
        org.requisites["исполнитель"]["фио"] = "Иванов Иван Иванович"
        org.requisites["исполнитель"]["должность"] = "директор"
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
        return org.id
    finally:
        db.close()


def _docx_text(path: Path) -> str:
    xml = ZipFile(path).read("word/document.xml").decode("utf-8")
    text = re.sub(r"<[^>]+>", "", xml)
    return re.sub(r"\s+", " ", text)


def test_normalize_bank_aliases_without_yo():
    block = normalize_bank_block(
        {
            "расчетный_счет": _ACCOUNT,
            "банк": _BANK,
            "бик": _BIK,
            "корр_счет": _CORR,
        }
    )
    assert block["расчётный_счёт"] == _ACCOUNT
    assert block["корр_счёт"] == _CORR
    assert bank_is_complete({"банк": block})


def test_bank_from_form_ascii_names():
    class Form(dict):
        def get(self, key, default=None):
            return super().get(key, default)

        def __contains__(self, key):
            return dict.__contains__(self, key)

    form = Form(
        {
            "account": _ACCOUNT,
            "bank_name": _BANK,
            "bank_bik": _BIK,
            "corr_account": _CORR,
        }
    )
    values = bank_from_form(form)
    assert values["расчётный_счёт"] == _ACCOUNT
    assert values["бик"] == _BIK
    assert values["банк"] == _BANK
    assert values["корр_счёт"] == _CORR


def test_settings_bank_save_and_invoice_fill(app, tmp_path, monkeypatch):
    client, dbmod = app
    monkeypatch.setenv("FILES_ROOT", str(tmp_path / "files"))
    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    object.__setattr__(s, "files_root", str(tmp_path / "files"))

    org_id = _seed(dbmod, email="bankfill@example.com")
    assert login(client, "bankfill@example.com", "Passw0rd!").status_code == 303

    # Пустой банк — форма счёта предупреждает
    r = client.get("/cabinet/documents/new/Счёт_на_оплату.docx")
    assert r.status_code == 200
    assert "банковские реквизиты" in r.text.lower()

    csrf = csrf_from(client, "/cabinet/settings/bank")
    r = client.post(
        "/cabinet/settings/bank",
        data={
            "csrf_token": csrf,
            "account": _ACCOUNT,
            "bank_name": _BANK,
            "bank_bik": _BIK,
            "corr_account": _CORR,
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "сохранены" in r.text.lower()

    db = dbmod.SessionLocal()
    try:
        org = db.get(Organization, org_id)
        req = ensure_requisites(org)
        assert req["банк"]["расчётный_счёт"] == _ACCOUNT
        assert req["банк"]["бик"] == _BIK
        assert bank_is_complete(req)

        doc = generate_docx(
            db=db,
            org=org,
            user_id=None,
            template_name="Счёт_на_оплату.docx",
            context={
                "номер_счёта": "1",
                "дата_счёта": "02.08.2026",
                "фио_клиента": "Клиент Тестов",
                "номер_договора": "Д-1",
                "дата_договора": "02.08.2026",
                "наименование_услуги": "Экспертиза",
                "сумма": "1000",
            },
            number="1",
        )
        text = _docx_text(absolute_file(doc))
        assert _ACCOUNT in text
        assert _BIK in text
        assert "СБЕРБАНК" in text.upper() or "Сбербанк" in text
    finally:
        db.close()

    # После заполнения банка генерация через форму не блокируется предупреждением-ошибкой
    csrf = csrf_from(client, "/cabinet/documents/new/Счёт_на_оплату.docx")
    r = client.post(
        "/cabinet/documents/new/Счёт_на_оплату.docx",
        data={
            "csrf_token": csrf,
            "номер_счёта": "2",
            "дата_счёта": "02.08.2026",
            "фио_клиента": "Ещё Клиент",
            "номер_договора": "Д-2",
            "дата_договора": "02.08.2026",
            "наименование_услуги": "Услуга",
            "сумма": "2000",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)


def test_invoice_blocked_when_bank_empty(app):
    client, dbmod = app
    _seed(dbmod, email="nobank@example.com")
    assert login(client, "nobank@example.com", "Passw0rd!").status_code == 303
    csrf = csrf_from(client, "/cabinet/documents/new/Счёт_на_оплату.docx")
    r = client.post(
        "/cabinet/documents/new/Счёт_на_оплату.docx",
        data={
            "csrf_token": csrf,
            "номер_счёта": "1",
            "дата_счёта": "02.08.2026",
            "фио_клиента": "Клиент",
            "номер_договора": "1",
            "дата_договора": "02.08.2026",
            "наименование_услуги": "Услуга",
            "сумма": "1000",
        },
    )
    assert r.status_code == 400
    assert "Настройки → Банк" in r.text or "банковские реквизиты" in r.text.lower()
