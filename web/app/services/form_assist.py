"""Подсказки и связанные поля форм документов (T8, аналог desktop autocomplete/linked)."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document
from app.services.counters import peek_number
from app.services.package_master import ensure_core_on_path

NUMBER_FIELD_KEYS = {
    "номер_договора": "dogovor",
    "номер_счёта": "schet",
    "номер_акта": "akt",
    "номер_пко": "pko",
    "номер_допсоглашения": "dopsogl",
}

# Поля, для которых показываем history-suggest
HISTORY_FIELDS = frozenset(
    {
        "фио_клиента",
        "фио_эксперта",
        "фио_подписанта",
        "фио_подэкспертного",
        "фио_субъекта",
        "название_заказчика",
        "инн_заказчика",
        "кпп_заказчика",
        "огрн_заказчика",
        "адрес_клиента",
        "юр_адрес_заказчика",
        "адрес_заявителя",
        "адрес_субъекта",
        "адрес_эксперта",
        "суд",
        "тип_экспертизы",
        "предмет_договора",
        "наименование_услуги",
        "телефон_клиента",
        "email_клиента",
        "принято_от",
        "плательщик",
        "должность_подписанта",
        "должность_подписанта_род",
        "основание_подписанта",
        "паспорт_эксперта",
        "номер_дела",
        "банк_заказчика",
        "бик_заказчика",
        "р_с_заказчика",
        "к_с_заказчика",
    }
)

# field → dadata kind (reuse counterparties suggest)
DADATA_FIELDS = {
    "инн_заказчика": "party",
    "название_заказчика": "party",
    "адрес_клиента": "address",
    "юр_адрес_заказчика": "address",
    "адрес_заявителя": "address",
    "адрес_субъекта": "address",
    "бик_заказчика": "bank",
    "банк_заказчика": "bank",
}


def field_meta(var: str) -> dict[str, Any]:
    ensure_core_on_path()
    from docfiller_core import labels, utils

    return {
        "name": var,
        "label": labels.подпись(var),
        "hint": labels.подсказка(var) or "",
        "multiline": utils.is_multiline_field(var),
        "is_date": utils.is_date_field(var),
        "is_number": var in NUMBER_FIELD_KEYS,
        "history": var in HISTORY_FIELDS,
        "dadata": DADATA_FIELDS.get(var),
    }


def today_str() -> str:
    return date.today().strftime("%d.%m.%Y")


def prefill_dates(variables: list[str], values: dict[str, str] | None = None) -> dict[str, str]:
    ensure_core_on_path()
    from docfiller_core import utils

    out = dict(values or {})
    today = today_str()
    for var in variables:
        if utils.is_date_field(var) and not str(out.get(var) or "").strip():
            out[var] = today
    return out


def peek_numbers_for(db: Session, org_id: int, variables: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for var in variables:
        key = NUMBER_FIELD_KEYS.get(var)
        if key:
            out[var] = peek_number(db, org_id, key)
    return out


def history_suggest(
    db: Session,
    org_id: int,
    field: str,
    query: str,
    *,
    limit: int = 8,
) -> list[str]:
    """Уникальные значения поля из context документов org (свежие первые)."""
    if field not in HISTORY_FIELDS:
        return []
    q = (query or "").strip().casefold()
    rows = db.scalars(
        select(Document)
        .where(Document.org_id == org_id)
        .order_by(Document.created_at.desc(), Document.id.desc())
        .limit(200)
    ).all()
    seen: set[str] = set()
    out: list[str] = []
    for doc in rows:
        ctx = doc.context or {}
        if not isinstance(ctx, dict):
            continue
        raw = ctx.get(field)
        if raw is None:
            continue
        val = str(raw).strip()
        if not val or val in seen:
            continue
        if q and q not in val.casefold():
            continue
        seen.add(val)
        out.append(val)
        if len(out) >= limit:
            break
    return out


def _position_genitive(title: str) -> str:
    """Родительный падеж должности из офлайн-словаря core (если есть)."""
    ensure_core_on_path()
    from docfiller_core.config import _POSITION_DECLENSIONS

    key = (title or "").strip().casefold()
    if not key:
        return ""
    for base, forms in _POSITION_DECLENSIONS.items():
        if str(base).strip().casefold() == key and isinstance(forms, dict):
            return str(forms.get("род") or "").strip()
    return ""


def linked_values(
    src: str,
    value: str,
    *,
    targets: list[str] | None = None,
    current: dict[str, str] | None = None,
    only_empty: bool = True,
) -> dict[str, str]:
    """Пересчитать связанные поля. only_empty — не затирать заполненные вручную."""
    ensure_core_on_path()
    from docfiller_core import filters

    value = (value or "").strip()
    if not value:
        return {}
    cur = {k: str(v or "") for k, v in (current or {}).items()}
    wanted = set(targets) if targets else None
    proposed: dict[str, str] = {}

    def _put(name: str, val: str) -> None:
        if wanted is not None and name not in wanted:
            return
        if only_empty and str(cur.get(name) or "").strip():
            return
        if val:
            proposed[name] = val

    if src == "фио_клиента":
        _put("принято_от", filters.decline_fio(value, "род"))
        _put("плательщик", value)
    elif src == "название_заказчика":
        _put("плательщик", value)
        _put("принято_от", value)
    elif src == "фио_эксперта":
        _put("фио_субъекта", value)
        if cur.get("паспорт_эксперта"):
            _put("паспорт_субъекта", cur["паспорт_эксперта"])
        if cur.get("адрес_эксперта"):
            _put("адрес_субъекта", cur["адрес_эксперта"])
    elif src == "паспорт_эксперта":
        _put("паспорт_субъекта", value)
    elif src == "адрес_эксперта":
        _put("адрес_субъекта", value)
    elif src == "фио_подписанта":
        # краткое ФИО часто нужно рядом с должностью
        try:
            short = filters.initials_after(value)
        except Exception:
            short = ""
        if short:
            _put("фио_подписанта_кратко", short)
    elif src == "должность_подписанта":
        rod = _position_genitive(value)
        if rod:
            _put("должность_подписанта_род", rod)
    elif src in ("номер_договора", "дата_договора"):
        no = value if src == "номер_договора" else cur.get("номер_договора", "")
        dt = value if src == "дата_договора" else cur.get("дата_договора", "")
        if no:
            basis = f"Оплата по договору № {no}"
            if dt:
                basis += f" от {dt}"
            _put("основание_пко", basis)
            _put("предмет_основание", basis)
        if src == "дата_договора":
            _put("дата_начала", value)
    elif src == "дата_счёта":
        _put("дата_акта", value)
    elif src == "номер_счёта":
        # акт часто продолжает нумерацию счёта вручную — только подсказка в основание
        no = value
        dt = cur.get("дата_счёта", "")
        if no:
            note = f"Счёт № {no}"
            if dt:
                note += f" от {dt}"
            _put("приложение_пко", note)

    return proposed


def enrich_form_context(
    db: Session,
    org_id: int,
    variables: list[str],
    values: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Значения + meta + peek для шаблона формы."""
    vals = prefill_dates(variables, values)
    peeks = peek_numbers_for(db, org_id, variables)
    metas = {v: field_meta(v) for v in variables}
    return {"values": vals, "field_meta": metas, "number_peeks": peeks}
