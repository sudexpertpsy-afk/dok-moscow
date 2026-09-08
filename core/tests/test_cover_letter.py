"""Сопроводительное письмо: манифест, пакет, эталонная генерация."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

from docfiller_core.contracts_registry import document_kind_from_registry, load_registry
from docfiller_core.filler import fill_template, list_template_variables
from docfiller_core.packages import related_documents
from docfiller_core.system_fields import get_standard_field, invalidate_registry_cache
from docfiller_core.template_manifest import document_kind, infer_group, load_manifest
from fixtures_sample_context import TEMPLATES_DIR, build_context_for_template

ETALON_DIR = Path(__file__).resolve().parent / "etalon"
TPL = "Сопроводительное_письмо.docx"

ORG_SETTINGS = {
    "организация": {
        "короткое_название": 'ООО «УСЭ»',
        "полное_название": 'ООО «УЧРЕЖДЕНИЕ СУДЕБНОЙ ЭКСПЕРТИЗЫ»',
        "инн": "7707817216",
        "огрн": "5137746012619",
        "юр_адрес": "г. Москва, тест",
        "телефон": "+7 (495) 000-00-00",
        "email": "test@use.moscow",
    },
    "исполнитель": {
        "должность": "Генеральный директор",
        "фио_кратко": "П.П. Подписантов",
    },
}


@pytest.fixture(autouse=True)
def _clear_registry():
    invalidate_registry_cache()
    yield
    invalidate_registry_cache()


def test_manifest_cover_letter():
    man = load_manifest(TEMPLATES_DIR / TPL)
    assert man is not None
    assert man.group == "Письма суду"
    assert man.kind == "документ"
    assert len(man.fields) == 14
    assert man.fields["исх_номер"].type == "counter"
    assert man.fields["исх_номер"].required is True
    assert man.fields["адресат"].type == "multiline"
    assert man.fields["адресат"].required is True
    assert man.fields["что_направляется_вин"].required is True
    assert man.fields["приложения"].required is True
    assert man.fields["исх_дата"].default == "сегодня"
    assert man.fields["вх_номер"].default == "—"
    assert man.fields["обращение"].default == "Уважаемый суд!"
    assert man.counter_key == "ishod"
    required = [n for n, f in man.fields.items() if f.required]
    assert len(required) == 4


def test_group_and_no_package():
    assert infer_group(TPL) == "Письма суду"
    assert related_documents(TPL) == []
    data = load_registry(TEMPLATES_DIR)
    assert data["document_kinds"].get(TPL) == "документ"
    assert document_kind_from_registry(TEMPLATES_DIR, TPL) == "документ"
    assert document_kind(TPL, templates_dir=TEMPLATES_DIR) == "документ"
    for _ctype, lst in data["contracts"].items():
        assert TPL not in lst


def test_fields_in_system_registry():
    invalidate_registry_cache()
    spec = get_standard_field("исх_номер")
    assert spec is not None
    assert spec["type"] == "counter"
    assert get_standard_field("что_направляется_вин") is not None
    assert get_standard_field("основание_направления") is not None


def test_fill_no_placeholders_and_org_header(tmp_path):
    src = TEMPLATES_DIR / TPL
    out = tmp_path / TPL
    ctx = build_context_for_template(TPL)
    fill_template(src, out, ctx, settings=ORG_SETTINGS)
    xml = ZipFile(out).read("word/document.xml").decode("utf-8")
    assert "{{" not in xml
    assert "}}" not in xml
    assert "7707817216" in xml
    assert "СОПРОВОДИТЕЛЬНОЕ" in xml
    assert "Тверской" in xml
    assert "заключения эксперта" in xml
    assert "П.П. Подписантов" in xml or "Подписантов" in xml


def test_etalon_exists_and_clean():
    path = ETALON_DIR / TPL
    assert path.is_file(), f"Нет эталона {path}"
    xml = ZipFile(path).read("word/document.xml").decode("utf-8")
    assert "{{" not in xml
    assert path.stat().st_size > 500


def test_variables_exclude_settings_root():
    vars_ = list_template_variables(TEMPLATES_DIR / TPL)
    assert "настройки" not in vars_
    for must in (
        "исх_номер",
        "адресат",
        "что_направляется_род",
        "что_направляется_вин",
        "основание_направления",
        "приложения",
    ):
        assert must in vars_
