"""Справочники из донора QuickDoc+ (ОКЕИ, КБК, НДС, ОКТМО и др.)."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger("dok.refs")

_REFS = Path(__file__).resolve().parents[1] / "data" / "refs"

ALLOWED_KINDS = frozenset(
    {
        "units",
        "kbk",
        "payment_bases",
        "payment_budgetStatus",
        "payment_budgetType",
        "payment_priority",
        "tax_rates",
        "countries",
        "currencies",
        "territories",
        "price_types",
    }
)

# поле формы → kind справочника
REF_FIELDS: dict[str, str] = {
    "единица_измерения": "units",
    "ед_изм": "units",
    "кбк": "kbk",
    "код_бк": "kbk",
    "основание_платежа": "payment_bases",
    "показатель_основания": "payment_bases",
    "статус_составителя": "payment_budgetStatus",
    "показатель_типа": "payment_budgetType",
    "очередность_платежа": "payment_priority",
    "октмо": "territories",
    "ставка_ндс": "tax_rates",
    "страна_происхождения": "countries",
}


@lru_cache
def load_ref(kind: str) -> list[Any]:
    if kind not in ALLOWED_KINDS:
        return []
    path = _REFS / f"{kind}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.exception("refs load %s", kind)
        return []
    if isinstance(data, list):
        return data
    return []


@lru_cache
def document_defaults() -> dict[str, Any]:
    path = _REFS / "document_defaults.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        log.exception("document_defaults")
        return {
            "default_tax_rate": "20 %",
            "default_without_tax_label": "Без НДС",
            "transfer_order_default_budget_basis": "0",
            "transfer_order_default_budget_status": "01",
            "transfer_order_default_priority": 5,
            "transfer_order_default_type": "электронно",
            "tax_rates": ["Без НДС", "0 %", "10 %", "20 %", "10 % / 110 %", "20 % / 120 %"],
        }


def _item_text(item: Any) -> tuple[str, str]:
    """(value, label) для подсказки."""
    if isinstance(item, str):
        return item, item
    if not isinstance(item, dict):
        s = str(item)
        return s, s
    if "short" in item and "label" in item:
        short = str(item.get("short") or "")
        label = str(item.get("label") or "")
        code = str(item.get("code") or "")
        display = f"{short} — {label}" if label and label != short else short
        if code:
            display = f"{display} ({code})"
        return short or label, display
    if "code" in item and "label" in item:
        code = str(item.get("code") or "")
        label = str(item.get("label") or "")
        return code, f"{code} — {label}" if label else code
    if "code" in item and "name" in item:
        code = str(item.get("code") or "")
        name = str(item.get("name") or "")
        return code, f"{code} — {name}" if name else code
    if "iso2" in item:
        short = str(item.get("short") or item.get("full") or "")
        return short, short
    if "name" in item and "code" in item:
        return str(item["code"]), f"{item['code']} — {item['name']}"
    # fallback
    val = str(item.get("value") or item.get("name") or item.get("label") or "")
    return val, val


def suggest_ref(kind: str, q: str, *, limit: int = 12) -> list[dict[str, str]]:
    """Подсказки справочника: [{value, label}, ...]."""
    if kind not in ALLOWED_KINDS:
        return []
    query = (q or "").strip().casefold()
    # ОКТМО большой — без запроса не отдаём
    if kind == "territories" and len(query) < 2:
        return []
    items = load_ref(kind)
    scored: list[tuple[int, dict[str, str]]] = []
    for item in items:
        value, label = _item_text(item)
        if not value and not label:
            continue
        hay = f"{value} {label}".casefold()
        if query and query not in hay:
            continue
        score = 50
        if query:
            vc = value.casefold()
            if vc == query:
                score = 0
            elif vc.startswith(query):
                score = 1
            elif query in vc:
                score = 2
            elif label.casefold().startswith(query):
                score = 3
            else:
                score = 10 + min(len(value), 40)
        scored.append((score, {"value": value, "label": label}))
    scored.sort(key=lambda x: (x[0], x[1]["label"]))
    out = [row for _, row in scored[:limit]]
    if not query and kind != "territories" and not out:
        for item in items[:limit]:
            value, label = _item_text(item)
            if value or label:
                out.append({"value": value, "label": label})
    return out


def tax_rate_options() -> list[str]:
    rates = document_defaults().get("tax_rates")
    if isinstance(rates, list) and rates:
        return [str(x) for x in rates]
    return list(load_ref("tax_rates"))
