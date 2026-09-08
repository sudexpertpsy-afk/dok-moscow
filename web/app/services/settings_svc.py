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
ORG_FIELD_LABELS = {
    "короткое_название": "Краткое название",
    "полное_название": "Полное название",
    "инн": "ИНН",
    "кпп": "КПП",
    "огрн": "ОГРН",
    "юр_адрес": "Юридический адрес",
    "почтовый_адрес": "Почтовый адрес",
    "телефон": "Телефон",
    "email": "E-mail",
    "лицензия": "Лицензия",
    "окпо": "ОКПО",
    "город": "Город",
}
BANK_FIELDS = ["расчётный_счёт", "банк", "бик", "корр_счёт"]
# Стабильные ASCII-имена формы → ключи в JSONB (как в шаблонах).
# Cyrillic name= в HTML иногда теряется в прокси/браузере — тогда банк «сохраняется» пустым.
BANK_FORM_MAP = {
    "account": "расчётный_счёт",
    "bank_name": "банк",
    "bik": "бик",
    "bank_bik": "бик",  # имя поля подсказки DaData
    "corr_account": "корр_счёт",
}
BANK_FIELD_LABELS = {
    "расчётный_счёт": "Расчётный счёт",
    "банк": "Банк",
    "бик": "БИК",
    "корр_счёт": "Корр. счёт",
}
# Поддержка ключей без «ё» (ручной YAML / старые данные).
_BANK_KEY_ALIASES = {
    "расчетный_счет": "расчётный_счёт",
    "расчетный_счёт": "расчётный_счёт",
    "расчётный_счет": "расчётный_счёт",
    "корр_счет": "корр_счёт",
}
SIGNATORY_BLOCKS = {
    "исполнитель": ["фио", "фио_кратко", "должность", "должность_род", "основание"],
    "бухгалтер": ["фио"],
    "кассир": ["фио"],
}
PRICE_FIELDS = ["сппэ", "кспэ", "рецензия", "обучение_спэ", "обучение_полиграф"]


def normalize_bank_block(raw: dict | None) -> dict[str, str]:
    """Привести блок «банк» к каноническим ключам с «ё»."""
    src = dict(raw or {})
    for alias, canonical in _BANK_KEY_ALIASES.items():
        if canonical not in src or not str(src.get(canonical) or "").strip():
            if alias in src and str(src.get(alias) or "").strip():
                src[canonical] = src[alias]
    out: dict[str, str] = {}
    for key in BANK_FIELDS:
        out[key] = str(src.get(key) or "").strip()
    return out


def bank_from_form(form: Any) -> dict[str, str]:
    """Считать банковские поля из формы (ASCII-имена и устаревшие кириллические)."""
    values: dict[str, str] = {}
    for form_name, key in BANK_FORM_MAP.items():
        if form_name in form:
            raw = str(form.get(form_name) or "").strip()
            # bank_bik и bik пишут в один ключ — не затирать непустым пустым
            if key in values and not raw:
                continue
            values[key] = raw
    for key in BANK_FIELDS:
        if key in form and (key not in values or not values[key]):
            values[key] = str(form.get(key) or "").strip()
    return values


def bank_is_complete(requisites: dict | None) -> bool:
    block = normalize_bank_block((requisites or {}).get("банк"))
    return all(block.get(k) for k in ("расчётный_счёт", "банк", "бик", "корр_счёт"))


def is_bill_template(template_name: str) -> bool:
    """Счёт на оплату (в т.ч. переименованные шаблоны Счет_/Счёт_)."""
    stem = template_name.rsplit(".", 1)[0].casefold().replace("ё", "е")
    return "на_оплату" in stem or stem.startswith("счет_на")


def ensure_requisites(org: Organization) -> dict:
    """Полная структура реквизитов (defaults ⊕ JSONB). Не пачкает сессию на чтении."""
    base = empty_requisites()
    current = org.requisites or {}
    ensure_core_on_path()
    from docfiller_core.config import _deep_merge

    merged = _deep_merge(base, current)
    merged["банк"] = normalize_bank_block(merged.get("банк"))
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
        block = normalize_bank_block(req.get("банк"))
        for f in BANK_FIELDS:
            if f in values:
                block[f] = str(values.get(f) or "").strip()
        block = normalize_bank_block(block)
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
