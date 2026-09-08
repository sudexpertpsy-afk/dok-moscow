"""Системный реестр полей встроенных шаблонов (W-41).

Хранится в JSON рядом с пакетом; при отсутствии файла — собирается из labels/utils.
Админка («Шаблоны → Поля») пишет в этот же файл.
"""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import labels, utils

FIELD_TYPES = (
    "string",
    "multiline",
    "date",
    "money",
    "checkbox",
    "select",
    "counter",
)

FIELD_TYPE_LABELS = {
    "string": "Строка",
    "multiline": "Многострочный",
    "date": "Дата",
    "money": "Деньги",
    "checkbox": "Флажок",
    "select": "Выбор из списка",
    "counter": "Счётчик",
}

# Стандартные поля-счётчики (как в web form_assist.NUMBER_FIELD_KEYS).
_COUNTER_NAMES = frozenset(
    {
        "номер_договора",
        "номер_счёта",
        "номер_акта",
        "номер_пко",
        "номер_допсоглашения",
        "номер_заключения",
        "исх_номер",
    }
)
_MONEY_NAMES = frozenset(
    {
        "сумма",
        "сумма_к_оплате",
        "стоимость",
        "цена_итог",
        "цена_один",
        "цена_один2",
        "цена_многих",
        "цена_многих2",
    }
)

_REGISTRY_PATH = Path(__file__).resolve().parent / "fields_registry.json"
_lock = threading.RLock()
_cache: dict[str, dict[str, Any]] | None = None


def registry_path() -> Path:
    return _REGISTRY_PATH


def _infer_type(name: str) -> str:
    if name in _COUNTER_NAMES:
        return "counter"
    if name in _MONEY_NAMES:
        return "money"
    if utils.is_date_field(name):
        return "date"
    if utils.is_multiline_field(name):
        return "multiline"
    return "string"


def _entry_from_labels(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "label": labels.подпись(name),
        "type": _infer_type(name),
        "required": False,
        "default": "",
        "hint": labels.подсказка(name) or "",
        "options": [],
    }


def default_registry() -> dict[str, dict[str, Any]]:
    names = sorted(set(labels.ПОДПИСИ) | set(labels.ПОДСКАЗКИ))
    return {n: _entry_from_labels(n) for n in names}


def _normalize_entry(name: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    base = _entry_from_labels(name)
    if not isinstance(raw, dict):
        return base
    ftype = str(raw.get("type") or base["type"]).strip()
    if ftype not in FIELD_TYPES:
        ftype = base["type"]
    options = raw.get("options")
    if not isinstance(options, list):
        options = []
    options = [str(x).strip() for x in options if str(x).strip()]
    return {
        "name": name,
        "label": str(raw.get("label") or base["label"]).strip() or base["label"],
        "type": ftype,
        "required": bool(raw.get("required", False)),
        "default": str(raw.get("default") or ""),
        "hint": str(raw.get("hint") or ""),
        "options": options,
    }


def load_registry(*, force: bool = False) -> dict[str, dict[str, Any]]:
    global _cache
    with _lock:
        if _cache is not None and not force:
            return deepcopy(_cache)
        data = default_registry()
        path = registry_path()
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = None
            if isinstance(raw, dict):
                items = raw.get("fields") if "fields" in raw else raw
                if isinstance(items, dict):
                    for name, entry in items.items():
                        key = str(name).strip()
                        if not key:
                            continue
                        data[key] = _normalize_entry(key, entry if isinstance(entry, dict) else None)
                elif isinstance(items, list):
                    for entry in items:
                        if not isinstance(entry, dict):
                            continue
                        key = str(entry.get("name") or "").strip()
                        if not key:
                            continue
                        data[key] = _normalize_entry(key, entry)
        # Поля из *.manifest.yaml рядом с шаблонами (W-42)
        try:
            from . import paths as app_paths
            from .template_manifest import load_manifest

            root = Path(app_paths.TEMPLATES_DIR)
            for docx in root.glob("*.docx"):
                if docx.name.startswith("~$"):
                    continue
                man = load_manifest(docx)
                if not man:
                    continue
                for fname, spec in man.fields.items():
                    entry = spec.as_registry_entry()
                    if fname in data:
                        # Не затирать чужой тип при совпадении смысла; дополняем пустое
                        cur = data[fname]
                        merged = dict(cur)
                        if not merged.get("label") and entry.get("label"):
                            merged["label"] = entry["label"]
                        if not merged.get("hint") and entry.get("hint"):
                            merged["hint"] = entry["hint"]
                        if entry.get("type") and entry["type"] != "string":
                            merged["type"] = entry["type"]
                        if entry.get("required"):
                            merged["required"] = True
                        if entry.get("default") and not merged.get("default"):
                            merged["default"] = entry["default"]
                        data[fname] = _normalize_entry(fname, merged)
                    else:
                        data[fname] = _normalize_entry(fname, entry)
        except Exception:
            pass
        _cache = data
        return deepcopy(data)


def save_registry(fields: dict[str, dict[str, Any]]) -> None:
    global _cache
    normalized = {
        name: _normalize_entry(name, entry)
        for name, entry in sorted(fields.items(), key=lambda kv: kv[0])
    }
    path = registry_path()
    payload = {"fields": normalized}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    with _lock:
        _cache = normalized


def invalidate_registry_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def is_standard_field(name: str) -> bool:
    return name in load_registry()


def get_standard_field(name: str) -> dict[str, Any] | None:
    return load_registry().get(name)


def list_standard_fields() -> list[dict[str, Any]]:
    reg = load_registry()
    return [reg[k] for k in sorted(reg)]


def upsert_standard_field(entry: dict[str, Any]) -> dict[str, Any]:
    name = str(entry.get("name") or "").strip()
    if not name:
        raise ValueError("Нужно имя поля")
    reg = load_registry()
    reg[name] = _normalize_entry(name, entry)
    save_registry(reg)
    return reg[name]


def delete_standard_field(name: str) -> None:
    reg = load_registry()
    if name not in reg:
        raise KeyError(name)
    del reg[name]
    save_registry(reg)
