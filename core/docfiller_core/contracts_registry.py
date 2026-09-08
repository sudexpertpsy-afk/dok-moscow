"""Реестр шаблонов комплекта (contracts_registry.json в каталоге Шаблоны)."""

from __future__ import annotations

import json
from pathlib import Path

REGISTRY_FILENAME = "contracts_registry.json"

# Значения по умолчанию — как раньше в master._CONTRACTS / packages.
DEFAULT_CONTRACTS = {
    "Физлицо": [
        "Договор_услуги_v2.docx",
        "Договор_услуги_с_печатью.docx",
        "Договор_освидетельствование.docx",
        "Договор_рецензия.docx",
        "Договор_обучение_СПЭ.docx",
        "Договор_обучение_полиграф.docx",
    ],
    "Юрлицо": [
        "Договор_услуги_юрлицо.docx",
        "Договор_рецензия_юрлицо.docx",
        "Договор_обучение_СПЭ_юрлицо.docx",
    ],
    "Эксперт (ГПД)": [
        "Договор_ГПД_эксперт.docx",
    ],
}

DEFAULT_PACKAGE_TEMPLATES = {
    "bill_fiz": "Счёт_на_оплату.docx",
    "bill_jur": "Счёт_на_оплату_юрлицо.docx",
    "act_fiz": "Акт_оказанных_услуг.docx",
    "act_jur": "Акт_оказанных_услуг_юрлицо.docx",
    "pko": "ПКО_КО-1.docx",
    "rko": "РКО_КО-2.docx",
    "payment": "Платёжное_поручение.docx",
    "upd": "УПД.docx",
    "sf": "Счёт_фактура.docx",
    "recon": "Акт_сверки.docx",
    "gpd_contract": "Договор_ГПД_эксперт.docx",
    "act_gpd": "Акт_ГПД_эксперт.docx",
    "consent": "Согласие_ПДн.docx",
}

DEFAULT_SELF_CONTAINED = ["Договор_освидетельствование.docx"]

CONTRACT_TYPES = ("Физлицо", "Юрлицо", "Эксперт (ГПД)")


def registry_path(templates_dir) -> Path:
    """Путь к contracts_registry.json.

    W-46 layered: предпочитаем TEMPLATES_DIR/contracts_registry.json,
    иначе system/contracts_registry.json (seed после rsync).
    """
    root = Path(templates_dir)
    at_root = root / REGISTRY_FILENAME
    if at_root.is_file():
        return at_root
    in_system = root / "system" / REGISTRY_FILENAME
    if in_system.is_file():
        return in_system
    return at_root


def default_registry() -> dict:
    return {
        "contracts": {k: list(v) for k, v in DEFAULT_CONTRACTS.items()},
        "self_contained": list(DEFAULT_SELF_CONTAINED),
        "package_templates": dict(DEFAULT_PACKAGE_TEMPLATES),
        "document_kinds": {},
    }


def load_registry(templates_dir) -> dict:
    path = registry_path(templates_dir)
    data = default_registry()
    if not path.is_file():
        return data
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return data
    if not isinstance(raw, dict):
        return data
    contracts = raw.get("contracts")
    if isinstance(contracts, dict):
        merged = {}
        for key in CONTRACT_TYPES:
            vals = contracts.get(key)
            if isinstance(vals, list):
                merged[key] = [str(x) for x in vals if str(x).endswith(".docx")]
            else:
                merged[key] = list(data["contracts"][key])
        data["contracts"] = merged
    sc = raw.get("self_contained")
    if isinstance(sc, list):
        data["self_contained"] = [str(x) for x in sc if str(x).endswith(".docx")]
    pkg = raw.get("package_templates")
    if isinstance(pkg, dict):
        for key, default in DEFAULT_PACKAGE_TEMPLATES.items():
            val = pkg.get(key)
            data["package_templates"][key] = (
                str(val) if isinstance(val, str) and val.endswith(".docx") else default
            )
    kinds = raw.get("document_kinds")
    if isinstance(kinds, dict):
        data["document_kinds"] = {
            str(k): str(v)
            for k, v in kinds.items()
            if str(k).endswith(".docx") and str(v).strip()
        }
    return data


def save_registry(templates_dir, data: dict) -> None:
    path = registry_path(templates_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "contracts": {k: list(data.get("contracts", {}).get(k, [])) for k in CONTRACT_TYPES},
        "self_contained": list(data.get("self_contained") or []),
        "package_templates": dict(data.get("package_templates") or DEFAULT_PACKAGE_TEMPLATES),
        "document_kinds": dict(data.get("document_kinds") or {}),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def rename_in_registry(templates_dir, old_name: str, new_name: str) -> dict:
    data = load_registry(templates_dir)
    for key in CONTRACT_TYPES:
        data["contracts"][key] = [
            new_name if x == old_name else x for x in data["contracts"][key]
        ]
    data["self_contained"] = [
        new_name if x == old_name else x for x in data["self_contained"]
    ]
    pkg = data["package_templates"]
    for key, val in list(pkg.items()):
        if val == old_name:
            pkg[key] = new_name
    kinds = data.setdefault("document_kinds", {})
    if old_name in kinds:
        kinds[new_name] = kinds.pop(old_name)
    save_registry(templates_dir, data)
    return data


def remove_from_registry(templates_dir, name: str) -> dict:
    data = load_registry(templates_dir)
    for key in CONTRACT_TYPES:
        data["contracts"][key] = [x for x in data["contracts"][key] if x != name]
    data["self_contained"] = [x for x in data["self_contained"] if x != name]
    kinds = data.get("document_kinds") or {}
    kinds.pop(name, None)
    data["document_kinds"] = kinds
    # package_templates не сбрасываем в пустоту — оставляем имя (файл могут вернуть)
    save_registry(templates_dir, data)
    return data


def add_contract(templates_dir, name: str, contract_type: str) -> dict:
    if contract_type not in CONTRACT_TYPES:
        raise ValueError("Неизвестный тип договора")
    data = load_registry(templates_dir)
    lst = data["contracts"].setdefault(contract_type, [])
    if name not in lst:
        lst.append(name)
    save_registry(templates_dir, data)
    return data


def package_name(templates_dir, key: str) -> str:
    data = load_registry(templates_dir)
    return data["package_templates"].get(key) or DEFAULT_PACKAGE_TEMPLATES[key]


def self_contained_set(templates_dir) -> set[str]:
    data = load_registry(templates_dir)
    return set(data.get("self_contained") or [])


def document_kind_from_registry(templates_dir, name: str) -> str | None:
    """Тип из contracts_registry.document_kinds или None."""
    data = load_registry(templates_dir)
    kinds = data.get("document_kinds") or {}
    val = kinds.get(Path(name).name)
    return str(val) if val else None

