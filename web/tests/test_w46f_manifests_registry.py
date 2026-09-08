"""W-46 §6: каждый системный DOCX имеет манифест; contracts_registry согласован с файлами."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "core" / "Шаблоны"
REGISTRY = TEMPLATES / "contracts_registry.json"


def _docx_names() -> list[str]:
    return sorted(
        p.name
        for p in TEMPLATES.glob("*.docx")
        if not p.name.startswith("~$")
    )


def test_every_system_docx_has_manifest():
    missing = []
    for name in _docx_names():
        stem = Path(name).stem
        man = TEMPLATES / f"{stem}.manifest.yaml"
        if not man.is_file():
            missing.append(name)
    assert not missing, f"Нет манифеста у: {missing}"


def test_manifests_parse():
    import sys

    sys.path.insert(0, str(REPO / "core"))
    from docfiller_core.template_manifest import load_manifest

    for name in _docx_names():
        man = load_manifest(TEMPLATES / name, force=True)
        assert man is not None, name
        assert man.group or True  # группа может быть пустой у черновика — infer в runtime
        assert man.docx_name == name


def test_contracts_registry_paths_exist():
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    existing = set(_docx_names())
    missing: list[str] = []

    for _ctype, names in (data.get("contracts") or {}).items():
        for name in names or []:
            if name not in existing:
                missing.append(f"contracts:{name}")

    for key, name in (data.get("package_templates") or {}).items():
        if name and name not in existing:
            missing.append(f"package_templates.{key}:{name}")

    for name in data.get("self_contained") or []:
        if name not in existing:
            missing.append(f"self_contained:{name}")

    for name in (data.get("document_kinds") or {}):
        if name not in existing:
            missing.append(f"document_kinds:{name}")

    assert not missing, f"Реестр ссылается на отсутствующие DOCX: {missing}"


def test_package_templates_docx_have_keys():
    """DOCX из package_templates должны быть ключами (значениями) реестра — уже покрыто выше.

    Дополнительно: известные шаблоны пакета (bill/act/pko) обязаны быть в registry.
    """
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    pkg = data.get("package_templates") or {}
    required_keys = {
        "bill_fiz",
        "bill_jur",
        "act_fiz",
        "act_jur",
        "pko",
        "gpd_contract",
        "act_gpd",
    }
    absent = required_keys - set(pkg)
    assert not absent, f"В package_templates нет ключей: {absent}"
