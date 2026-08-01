"""Обновление реквизитов организации и счётчиков."""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.defaults import empty_requisites
from app.models import Counter, Organization
from app.services.templates import ensure_core_on_path


ORG_FIELDS = [
    "короткое_название",
    "полное_название",
    "инн",
    "кпп",
    "огрн",
    "юр_адрес",
    "почтовый_адрес",
    "телефон",
    "email",
    "лицензия",
    "окпо",
    "город",
]
BANK_FIELDS = ["расчётный_счёт", "банк", "бик", "корр_счёт"]
SIGNATORY_BLOCKS = {
    "исполнитель": ["фио", "фио_кратко", "должность", "должность_род", "основание"],
    "бухгалтер": ["фио"],
    "кассир": ["фио"],
}
PRICE_FIELDS = ["сппэ", "кспэ", "рецензия", "обучение_спэ", "обучение_полиграф"]


def ensure_requisites(org: Organization) -> dict:
    base = empty_requisites()
    current = org.requisites or {}
    # deep merge: current overrides base
    ensure_core_on_path()
    from docfiller_core.config import _deep_merge

    merged = _deep_merge(base, current)
    org.requisites = merged
    return merged


def update_section(org: Organization, section: str, values: dict[str, Any]) -> list[str]:
    """Обновить секцию реквизитов. Возвращает ошибки валидации."""
    req = ensure_requisites(org)
    errors: list[str] = []
    ensure_core_on_path()
    from docfiller_core import validators as v

    if section == "организация":
        block = dict(req.get("организация") or {})
        for f in ORG_FIELDS:
            if f in values:
                block[f] = str(values.get(f) or "").strip()
        inn = block.get("инн") or ""
        if inn:
            issue = v.validate_inn(inn)
            if issue:
                errors.append(issue.message)
        email = block.get("email") or ""
        if email:
            issue = v.validate_email(email)
            if issue:
                errors.append(issue.message)
        if not errors:
            req["организация"] = block

    elif section == "банк":
        block = dict(req.get("банк") or {})
        for f in BANK_FIELDS:
            if f in values:
                block[f] = str(values.get(f) or "").strip()
        bik = block.get("бик") or ""
        account = block.get("расчётный_счёт") or ""
        if bik:
            issue = v.validate_bik(bik)
            if issue:
                errors.append(issue.message)
        if account and bik:
            issue = v.validate_account(account, bik)
            if issue:
                errors.append(issue.message)
        if not errors:
            req["банк"] = block

    elif section == "подписанты":
        for name, fields in SIGNATORY_BLOCKS.items():
            block = dict(req.get(name) or {})
            for f in fields:
                key = f"{name}.{f}"
                if key in values:
                    block[f] = str(values.get(key) or "").strip()
            req[name] = block

    elif section == "прайс":
        block = dict(req.get("прайс") or {})
        for f in PRICE_FIELDS:
            if f in values:
                raw = str(values.get(f) or "").strip().replace(" ", "")
                if not raw:
                    block[f] = 0
                    continue
                try:
                    block[f] = int(float(raw.replace(",", ".")))
                except ValueError:
                    errors.append(f"Прайс «{f}»: укажите число")
        if not errors:
            req["прайс"] = block
    else:
        errors.append("Неизвестная секция настроек")

    if not errors:
        org.requisites = copy.deepcopy(req)
        flag_modified(org, "requisites")
    return errors


def list_counters(db: Session, org_id: int) -> list[Counter]:
    return list(
        db.scalars(select(Counter).where(Counter.org_id == org_id).order_by(Counter.key)).all()
    )


def adjust_counter(
    db: Session,
    org_id: int,
    key: str,
    *,
    value: int | None = None,
    prefix: str | None = None,
    suffix: str | None = None,
) -> Counter | None:
    counter = db.get(Counter, {"org_id": org_id, "key": key})
    if counter is None:
        if value is None:
            return None
        counter = Counter(org_id=org_id, key=key, prefix=prefix or "", value=value, suffix=suffix or "")
        db.add(counter)
    else:
        if value is not None:
            counter.value = int(value)
        if prefix is not None:
            counter.prefix = prefix
        if suffix is not None:
            counter.suffix = suffix
    db.flush()
    return counter
