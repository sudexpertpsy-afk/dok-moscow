"""T8: form-assist — peek, history, linked, UX формы."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.defaults import empty_requisites
from app.models import (
    Counter,
    Document,
    DocumentFormat,
    Organization,
    User,
    UserRole,
)
from app.security import hash_password
from app.services.counters import allocate_number, peek_number
from app.services.form_assist import history_suggest, linked_values, prefill_dates
from conftest import login


def _org_user(dbmod, email="t8@example.com"):
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="T8Org", requisites=empty_requisites())
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
        db.commit()
        return org.id, email
    finally:
        db.close()


def test_peek_does_not_increment(app):
    _, dbmod = app
    org_id, _ = _org_user(dbmod, "peek@example.com")
    db = dbmod.SessionLocal()
    try:
        p1 = peek_number(db, org_id, "dogovor", prefix="Д-")
        p2 = peek_number(db, org_id, "dogovor", prefix="Д-")
        assert p1 == p2
        n, formatted = allocate_number(db, org_id, "dogovor", prefix="Д-")
        db.commit()
        assert formatted == p1
        assert n == 1
        assert peek_number(db, org_id, "dogovor") != p1
    finally:
        db.close()


def test_linked_fio_to_genitive():
    from app.services.package_master import ensure_core_on_path

    ensure_core_on_path()
    from docfiller_core import filters

    src = "Иванов Иван Иванович"
    out = linked_values("фио_клиента", src, targets=["принято_от"], only_empty=True)
    assert out["принято_от"] == filters.decline_fio(src, "род")
    # only_empty: не затирать
    out2 = linked_values(
        "фио_клиента",
        src,
        targets=["принято_от"],
        current={"принято_от": "Уже заполнено"},
        only_empty=True,
    )
    assert "принято_от" not in out2


def test_linked_expert_to_subject():
    out = linked_values(
        "фио_эксперта",
        "Петров Пётр Петрович",
        current={"паспорт_эксперта": "1234 567890", "адрес_эксперта": "Москва"},
        only_empty=True,
    )
    assert out["фио_субъекта"] == "Петров Пётр Петрович"
    assert out["паспорт_субъекта"] == "1234 567890"
    assert out["адрес_субъекта"] == "Москва"


def test_linked_customer_passport_dates_and_signatory():
    from app.services.package_master import ensure_core_on_path

    ensure_core_on_path()
    from docfiller_core import filters

    out = linked_values("название_заказчика", "ООО Ромашка", only_empty=True)
    assert out["плательщик"] == "ООО Ромашка"
    assert out["принято_от"] == "ООО Ромашка"

    out = linked_values("паспорт_эксперта", "4500 123456", only_empty=True)
    assert out["паспорт_субъекта"] == "4500 123456"
    out = linked_values("адрес_эксперта", "г. Москва", only_empty=True)
    assert out["адрес_субъекта"] == "г. Москва"

    fio = "Сидоров Сидор Сидорович"
    out = linked_values("фио_подписанта", fio, only_empty=True)
    assert out["фио_подписанта_кратко"] == filters.initials_after(fio)

    out = linked_values(
        "дата_договора",
        "01.02.2026",
        current={"номер_договора": "Д-10"},
        only_empty=True,
    )
    assert out["дата_начала"] == "01.02.2026"
    assert "Д-10" in out["основание_пко"]
    assert "01.02.2026" in out["основание_пко"]
    assert out["предмет_основание"] == out["основание_пко"]

    out = linked_values(
        "номер_счёта",
        "С-3",
        current={"дата_счёта": "05.02.2026"},
        only_empty=True,
    )
    assert out["приложение_пко"] == "Счёт № С-3 от 05.02.2026"

    out = linked_values("дата_счёта", "05.02.2026", only_empty=True)
    assert out["дата_акта"] == "05.02.2026"


def test_history_suggest_org_isolation(app):
    client, dbmod = app
    org_a, email_a = _org_user(dbmod, "ha@example.com")
    org_b, email_b = _org_user(dbmod, "hb@example.com")
    db = dbmod.SessionLocal()
    try:
        db.add(
            Document(
                org_id=org_a,
                template="dogovor",
                number="1",
                format=DocumentFormat.docx,
                file_path="a.docx",
                context={"фио_клиента": "Иванов Секретный А"},
            )
        )
        db.add(
            Document(
                org_id=org_b,
                template="dogovor",
                number="1",
                format=DocumentFormat.docx,
                file_path="b.docx",
                context={"фио_клиента": "Иванов Чужой Б"},
            )
        )
        db.commit()
        hits = history_suggest(db, org_a, "фио_клиента", "Ива")
        assert "Иванов Секретный А" in hits
        assert "Иванов Чужой Б" not in hits
    finally:
        db.close()

    assert login(client, email_a, "Passw0rd!").status_code == 303
    r = client.get("/cabinet/form-assist/suggest?field=фио_клиента&q=Ива")
    assert r.status_code == 200
    assert "Секретный" in r.text
    assert "Чужой" not in r.text


def test_document_form_prefill_and_labels(app):
    client, dbmod = app
    _org_user(dbmod, "form@example.com")
    assert login(client, "form@example.com", "Passw0rd!").status_code == 303
    # любой шаблон из списка
    r = client.get("/cabinet/documents/")
    assert r.status_code == 200
    # взять первый new link
    import re

    m = re.search(r'/cabinet/documents/new/([^"\']+)', r.text)
    if not m:
        # если шаблонов нет в тестовом окружении — проверим только prefill helper
        today = date.today().strftime("%d.%m.%Y")
        assert prefill_dates(["дата_договора", "фио_клиента"], {})["дата_договора"] == today
        return
    tpl = m.group(1)
    r = client.get(f"/cabinet/documents/new/{tpl}")
    assert r.status_code == 200
    assert "field-block" in r.text
    assert "form-assist.js" in r.text or "form-assist" in r.text
    today = date.today().strftime("%d.%m.%Y")
    # если в шаблоне есть дата — она предзаполнена
    if "дата_" in r.text or "name=\"дата" in r.text:
        assert today in r.text


def test_linked_api(app):
    client, dbmod = app
    _org_user(dbmod, "link@example.com")
    assert login(client, "link@example.com", "Passw0rd!").status_code == 303
    from app.services.package_master import ensure_core_on_path

    ensure_core_on_path()
    from docfiller_core import filters

    fio = "Сидоров Сидор Сидорович"
    r = client.get(
        "/cabinet/form-assist/linked",
        params={"src": "фио_клиента", "value": fio, "targets": "принято_от", "only_empty": 1},
    )
    assert r.status_code == 200
    assert r.json()["принято_от"] == filters.decline_fio(fio, "род")


def test_peek_numbers_api(app):
    client, dbmod = app
    org_id, email = _org_user(dbmod, "peekapi@example.com")
    db = dbmod.SessionLocal()
    try:
        db.add(Counter(org_id=org_id, key="dogovor", prefix="Д-", value=5, suffix=""))
        db.commit()
    finally:
        db.close()
    assert login(client, email, "Passw0rd!").status_code == 303
    r = client.get("/cabinet/form-assist/peek-numbers?fields=номер_договора")
    assert r.status_code == 200
    assert r.json()["номер_договора"] == "Д-6"
