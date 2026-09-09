"""Валидация и сборка полей контрагента."""

from __future__ import annotations

from app.models import CounterpartySource, CounterpartyType
from app.services.templates import ensure_core_on_path


def validate_counterparty_form(тип: CounterpartyType, fields: dict) -> list[str]:
    """Вернуть список сообщений об ошибках (пусто — ок)."""
    ensure_core_on_path()
    from docfiller_core import validators as v

    errors: list[str] = []
    inn = (fields.get("inn") or "").strip()
    ogrn = (fields.get("ogrn") or "").strip()
    kpp = (fields.get("kpp") or "").strip()
    bik = (fields.get("bank_bik") or "").strip()
    account = (fields.get("bank_account") or "").strip()
    series = (fields.get("passport_series") or "").strip()
    number = (fields.get("passport_number") or "").strip()

    if тип == CounterpartyType.ul:
        if not (fields.get("name") or "").strip():
            errors.append("Укажите наименование организации")
        if inn:
            issue = v.validate_inn(inn)
            if issue:
                errors.append(issue.message)
        if ogrn:
            issue = v.validate_ogrn(ogrn)
            if issue:
                errors.append(issue.message)
        if kpp:
            issue = v.validate_kpp(kpp)
            if issue:
                errors.append(issue.message)
    else:
        if not (fields.get("fio") or "").strip():
            errors.append("Укажите ФИО")
        if inn:
            issue = v.validate_inn(inn)
            if issue:
                errors.append(issue.message)
        if series or number:
            for issue in v.validate_passport(series, number):
                errors.append(issue.message)

    if bik:
        issue = v.validate_bik(bik)
        if issue:
            errors.append(issue.message)
    if account:
        issue = v.validate_account(account, bik or "000000000")
        if issue and bik:
            errors.append(issue.message)
        elif account and not bik:
            # без БИК — только длина
            digits = "".join(ch for ch in account if ch.isdigit())
            if len(digits) != 20:
                errors.append("Счёт должен содержать 20 цифр")

    email = (fields.get("email") or "").strip()
    if email:
        issue = v.validate_email(email)
        if issue:
            errors.append(issue.message)
    phone = (fields.get("phone") or "").strip()
    if phone:
        issue = v.validate_phone(phone)
        if issue:
            errors.append(issue.message)

    return errors


def parse_type(raw: str) -> CounterpartyType:
    try:
        return CounterpartyType(raw)
    except ValueError:
        return CounterpartyType.fl


def form_to_fields(form) -> dict:
    keys = [
        "name",
        "fio",
        "inn",
        "kpp",
        "ogrn",
        "snils",
        "passport_series",
        "passport_number",
        "passport_issuer",
        "passport_date",
        "address",
        "phone",
        "email",
        "bank_name",
        "bank_bik",
        "bank_account",
        "bank_corr_account",
        "notes",
    ]
    return {k: str(form.get(k) or "").strip() or None for k in keys}


def apply_fields(cp, fields: dict, *, source: CounterpartySource | None = None) -> None:
    for k, v in fields.items():
        setattr(cp, k, v)
    if source is not None:
        cp.source = source


def delete_counterparty(db, org_id: int, cp) -> None:
    """Удалить контрагента своей org вместе со связанными договорами.

    Документы и события календаря: FK SET NULL. Раньше договоры блокировали
    удаление (RESTRICT), а в кабинете не было UI удаления договоров — тупик.
    """
    from sqlalchemy import select

    from app.models import Contract

    if cp.org_id != org_id:
        raise ValueError("Контрагент другой организации")
    contracts = list(
        db.scalars(
            select(Contract).where(
                Contract.org_id == org_id,
                Contract.counterparty_id == cp.id,
            )
        ).all()
    )
    for contract in contracts:
        db.delete(contract)
    db.delete(cp)
