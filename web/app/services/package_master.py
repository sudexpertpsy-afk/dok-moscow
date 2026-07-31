"""Мастер комплектов — обёртка над master.py / packages.py."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.models import CounterpartyType
from app.services.templates import ensure_core_on_path, templates_dir

TYPE_LABELS = ("Физлицо", "Юрлицо", "Эксперт (ГПД)")

TYPE_TO_CP = {
    "Физлицо": CounterpartyType.fl,
    "Юрлицо": CounterpartyType.ul,
    "Эксперт (ГПД)": CounterpartyType.expert,
}

CP_TO_TYPE = {v: k for k, v in TYPE_TO_CP.items()}

SESSION_KEY = "package_wizard"


def _master():
    ensure_core_on_path()
    from docfiller_core import master

    return master


def _packages():
    ensure_core_on_path()
    from docfiller_core import packages

    return packages


def contract_options(тип: str) -> list[str]:
    return _master().contract_options(тип, templates_dir())


def extra_options(тип: str, contract_template: str, requisites: dict | None) -> list[tuple[str, bool]]:
    pkgs = _packages()
    master = _master()
    if not contract_template or contract_template == master.БЕЗ_ДОГОВОРА:
        return []
    return pkgs.related_documents(contract_template, settings=requisites or {})


def selected_templates(contract_template: str, extras: list[str]) -> list[str]:
    return _master().selected_templates_list(contract_template, extras)


def collect_fields(selected: list[str], тип: str) -> tuple[list[str], list[str]]:
    return _master().collect_variables(templates_dir(), selected, тип)


def build_contexts(selected: list[str], core_values: dict, additional: dict) -> dict[str, dict]:
    return _master().build_contexts(templates_dir(), selected, core_values, additional)


def field_defaults(
    contract_template: str,
    core_values: dict,
    extras: list[str],
    *,
    next_numbers: dict[str, str] | None = None,
) -> dict[str, str]:
    """Подсказки полей пакета без файловых счётчиков Шаблонера."""
    ensure_core_on_path()
    from docfiller_core import filters, packages

    docs = [(n, True) for n in extras]
    # suggest_fields тянет desktop-счётчики — собираем аналог сами
    c = dict(core_values or {})
    today = date.today().strftime("%d.%m.%Y")
    contract_no = str(c.get("номер_договора") or "")
    fields: dict[str, str] = {}
    doc_names = set(extras)
    nums = next_numbers or {}

    bill = {packages.BILL_FIZ, packages.BILL_JUR}
    act = {packages.ACT_FIZ, packages.ACT_JUR}
    if doc_names & bill:
        fields["номер_счёта"] = nums.get("номер_счёта") or contract_no
        fields["дата_счёта"] = today
    if doc_names & act:
        fields["номер_акта"] = nums.get("номер_акта") or contract_no
        fields["дата_акта"] = str(c.get("дата_акта") or today)
    if packages.PKO in doc_names:
        fields["номер_пко"] = nums.get("номер_пко") or "1"
        fields["дата_пко"] = today
        payer = str(c.get("название_заказчика") or c.get("фио_клиента") or "")
        if payer and not c.get("название_заказчика"):
            payer = filters.decline_fio(payer, "род")
        fields["принято_от"] = payer
        fields["основание_пко"] = (
            f"Оплата по договору № {contract_no} от {c.get('дата_договора', '')}"
            if contract_no
            else "Оплата по договору"
        )
        fields["приложение_пко"] = "—"

    fields["наименование_услуги"] = packages.default_service_name(contract_template, c)

    if str(contract_template) == packages.GPD_CONTRACT:
        fields["номер_акта"] = nums.get("номер_акта") or contract_no
        fields["дата_акта"] = today
        fields["фио_субъекта"] = str(c.get("фио_эксперта") or "")
        fields["паспорт_субъекта"] = str(c.get("паспорт_эксперта") or "")
        fields["адрес_субъекта"] = str(c.get("адрес_эксперта") or "")
        fields["цели_обработки"] = (
            "заключение и исполнение гражданско-правового договора, "
            "ведение кадрового и бухгалтерского учёта"
        )
    return {k: str(v) for k, v in fields.items() if v is not None}


def infer_type(template_name: str) -> str:
    return _master().infer_type_from_template(template_name)


def display_name(template_name: str) -> str:
    return _master().display_name(template_name)


def counterparty_from_core(тип: str, core: dict[str, Any]) -> dict[str, Any]:
    """Поля Counterparty из ядра мастера."""
    if тип == "Юрлицо":
        return {
            "type": CounterpartyType.ul,
            "name": core.get("название_заказчика") or "",
            "fio": core.get("фио_подписанта") or "",
            "inn": core.get("инн_заказчика") or "",
            "kpp": core.get("кпп_заказчика") or "",
            "ogrn": core.get("огрн_заказчика") or "",
            "address": core.get("юр_адрес_заказчика") or "",
            "phone": core.get("телефон_клиента") or "",
            "email": core.get("email_клиента") or "",
        }
    if тип == "Эксперт (ГПД)":
        return {
            "type": CounterpartyType.expert,
            "name": None,
            "fio": core.get("фио_эксперта") or "",
            "phone": core.get("телефон") or "",
            "email": core.get("email") or "",
            "address": core.get("адрес_эксперта") or "",
        }
    return {
        "type": CounterpartyType.fl,
        "name": None,
        "fio": core.get("фио_клиента") or "",
        "address": core.get("адрес_клиента") or "",
        "phone": core.get("телефон_клиента") or "",
        "email": core.get("email_клиента") or "",
    }


def core_from_counterparty(тип: str, cp) -> dict[str, str]:
    if тип == "Юрлицо":
        return {
            "название_заказчика": cp.name or "",
            "инн_заказчика": cp.inn or "",
            "кпп_заказчика": cp.kpp or "",
            "огрн_заказчика": cp.ogrn or "",
            "юр_адрес_заказчика": cp.address or "",
            "фио_подписанта": cp.fio or "",
            "телефон_клиента": cp.phone or "",
            "email_клиента": cp.email or "",
        }
    if тип == "Эксперт (ГПД)":
        return {
            "фио_эксперта": cp.fio or "",
            "адрес_эксперта": cp.address or "",
            "телефон": cp.phone or "",
            "email": cp.email or "",
        }
    return {
        "фио_клиента": cp.fio or "",
        "адрес_клиента": cp.address or "",
        "телефон_клиента": cp.phone or "",
        "email_клиента": cp.email or "",
    }
