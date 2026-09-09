"""W-49: связи полей карточки с комплектом и синонимами импорта."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from app.models import CounterpartyType, Event
from app.services.cp_import import (
    _journal_fields_from_context,
    auto_map_columns,
)
from app.services.package_master import (
    core_from_counterparty,
    counterparty_from_core,
    merge_step3_values,
)


def test_synonyms_map_customer_headers():
    headers = [
        "Название_заказчика",
        "ИНН_заказчика",
        "юр_адрес_заказчика",
        "бик_заказчика",
        "р_с_заказчика",
        "фио_подписанта",
        "паспорт_кем_выдан",
    ]
    m = auto_map_columns(headers)
    assert m["name"] == 0
    assert m["inn"] == 1
    assert m["address"] == 2
    assert m["bank_bik"] == 3
    assert m["bank_account"] == 4
    assert m["fio"] == 5
    assert m["passport_issuer"] == 6


def test_journal_reads_legal_address_and_bank():
    typ, fields = _journal_fields_from_context(
        {
            "название_заказчика": "ООО Тест",
            "инн_заказчика": "7707083893",
            "юр_адрес_заказчика": "Москва",
            "фио_подписанта": "Иванов И.И.",
            "бик_заказчика": "044525225",
            "р_с_заказчика": "407028101",
        }
    )
    assert typ == CounterpartyType.ul
    assert fields["address"] == "Москва"
    assert fields["fio"] == "Иванов И.И."
    assert fields["bank_bik"] == "044525225"
    assert fields["bank_account"] == "407028101"


def test_journal_fl_passport_issuer_alias():
    typ, fields = _journal_fields_from_context(
        {
            "фио_клиента": "Петров П.П.",
            "паспорт_серия": "4500",
            "паспорт_номер": "123456",
            "паспорт_кем_выдан": "ОВД",
        }
    )
    assert typ == CounterpartyType.fl
    assert fields["passport_issuer"] == "ОВД"


def test_core_from_counterparty_fills_bank_and_passport():
    ul = SimpleNamespace(
        name="ООО",
        inn="7707083893",
        kpp="770701001",
        ogrn="1027700132195",
        address="Москва",
        fio="Сидоров",
        phone="+7900",
        email="a@b.c",
        bank_name="Сбер",
        bank_bik="044525225",
        bank_account="40702810",
        bank_corr_account="30101",
        passport_series=None,
        passport_number=None,
        passport_issuer=None,
        passport_date=None,
        snils=None,
    )
    core = core_from_counterparty("Юрлицо", ul)
    assert core["бик_заказчика"] == "044525225"
    assert core["р_с_заказчика"] == "40702810"
    assert core["фио_подписанта"] == "Сидоров"
    back = counterparty_from_core("Юрлицо", core)
    assert back["bank_bik"] == "044525225"
    assert back["fio"] == "Сидоров"

    fl = SimpleNamespace(
        name=None,
        fio="Иванов",
        inn="123456789012",
        address="СПб",
        phone="",
        email="",
        bank_name=None,
        bank_bik=None,
        bank_account=None,
        bank_corr_account=None,
        passport_series="1234",
        passport_number="567890",
        passport_issuer="ОВД",
        passport_date="01.01.2010",
        snils="112-233-445 95",
    )
    core_fl = core_from_counterparty("Физлицо", fl)
    assert core_fl["паспорт_серия"] == "1234"
    assert core_fl["паспорт_кем_выдан"] == "ОВД"
    assert core_fl["снилс"] == "112-233-445 95"


def test_merge_step3_order_form_over_wizard_over_card():
    core, add = merge_step3_values(
        form_core={"название_заказчика": "С формы", "инн_заказчика": ""},
        form_additional={"бик_заказчика": ""},
        wizard_core={"название_заказчика": "Из мастера", "юр_адрес_заказчика": "Адрес мастера"},
        wizard_additional={"р_с_заказчика": "407"},
        card_values={
            "название_заказчика": "Из карточки",
            "бик_заказчика": "044525225",
            "паспорт_серия": "4500",
        },
    )
    # форма побеждает (в т.ч. пустой ИНН/БИК на форме)
    assert core["название_заказчика"] == "С формы"
    assert core["инн_заказчика"] == ""
    assert add["бик_заказчика"] == ""
    # нет на форме → мастер, затем карточка
    assert core["юр_адрес_заказчика"] == "Адрес мастера"
    assert add["р_с_заказчика"] == "407"
    assert core["паспорт_серия"] == "4500"


def test_upsert_empty_bik_does_not_wipe_card(app):
    from app.defaults import empty_requisites
    from app.models import Organization
    from app.services.package_generate import upsert_counterparty

    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="UpsertOrg", requisites=empty_requisites())
        db.add(org)
        db.flush()
        cp = upsert_counterparty(
            db,
            org.id,
            "Юрлицо",
            {
                "название_заказчика": "ООО Банк",
                "инн_заказчика": "7707083893",
                "бик_заказчика": "044525225",
                "банк_заказчика": "Сбербанк",
            },
            None,
            user_id=1,
        )
        db.flush()
        assert cp.bank_bik == "044525225"
        created = db.scalars(
            select(Event).where(
                Event.org_id == org.id,
                Event.type == "counterparty.created_on_generate",
            )
        ).all()
        assert len(created) == 1

        upsert_counterparty(
            db,
            org.id,
            "Юрлицо",
            {
                "название_заказчика": "ООО Банк",
                "инн_заказчика": "7707083893",
                "бик_заказчика": "",
                "банк_заказчика": "",
            },
            cp.id,
            user_id=1,
        )
        db.refresh(cp)
        assert cp.bank_bik == "044525225"
        assert cp.bank_name == "Сбербанк"

        # непустое с формы побеждает
        upsert_counterparty(
            db,
            org.id,
            "Юрлицо",
            {
                "название_заказчика": "ООО Банк",
                "бик_заказчика": "044525593",
            },
            cp.id,
            user_id=1,
        )
        db.refresh(cp)
        assert cp.bank_bik == "044525593"
        updated = db.scalars(
            select(Event).where(
                Event.org_id == org.id,
                Event.type == "counterparty.updated_on_generate",
            )
        ).all()
        assert updated
        assert "bank_bik" in (updated[-1].details or {}).get("fields", [])
        db.commit()
    finally:
        db.close()


def test_dadata_aliases_include_address_and_bank_short():
    from app.services.form_assist import DADATA_FIELDS

    assert DADATA_FIELDS["адрес_заказчика"] == "address"
    assert DADATA_FIELDS["бик"] == "bank"
    assert DADATA_FIELDS["банк"] == "bank"
