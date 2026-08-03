"""W-42: манифесты и шаблоны заключения эксперта."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

from docfiller_core.contracts_registry import document_kind_from_registry, load_registry
from docfiller_core.filler import fill_template, list_template_variables
from docfiller_core.packages import related_documents
from docfiller_core.system_fields import get_standard_field, invalidate_registry_cache
from docfiller_core.template_manifest import (
    GROUP_ORDER,
    document_kind,
    infer_group,
    load_manifest,
)
from fixtures_sample_context import TEMPLATES_DIR, build_context_for_template

ETALON_DIR = Path(__file__).resolve().parent / "etalon"
CONCLUSION_TEMPLATES = [
    "Заключение_эксперта_гражданский_процесс.docx",
    "Заключение_эксперта_уголовный_процесс.docx",
]

ORG_SETTINGS = {
    "организация": {
        "короткое_название": 'ООО «УСЭ»',
        "полное_название": 'ООО «УЧРЕЖДЕНИЕ СУДЕБНОЙ ЭКСПЕРТИЗЫ»',
        "инн": "7707817216",
        "огрн": "5137746012619",
        "юр_адрес": "г. Москва, тест",
        "телефон": "+7 (495) 000-00-00",
        "email": "test@use.moscow",
    }
}


@pytest.fixture(autouse=True)
def _clear_registry():
    invalidate_registry_cache()
    yield
    invalidate_registry_cache()


@pytest.mark.parametrize("name", CONCLUSION_TEMPLATES)
def test_manifest_loaded(name):
    man = load_manifest(TEMPLATES_DIR / name)
    assert man is not None
    assert man.group == "Заключения эксперта"
    assert man.kind == "заключение"
    assert len(man.fields) == 31
    assert man.fields["номер_заключения"].type == "counter"
    assert man.fields["вопросы_эксперту"].type == "multiline"
    assert man.fields["дата_заключения"].default == "сегодня"
    assert man.fields["присутствовавшие"].default == "не присутствовали"
    assert man.counter_key.startswith("zaklyuchenie_")


def test_group_order_after_letters():
    assert GROUP_ORDER.index("Письма суду") < GROUP_ORDER.index("Заключения эксперта")
    assert infer_group("Заключение_эксперта_гражданский_процесс.docx") == "Заключения эксперта"
    assert infer_group("СППЭ_информация_суду.docx") == "Письма суду"


@pytest.mark.parametrize("name", CONCLUSION_TEMPLATES)
def test_no_related_package(name):
    assert related_documents(name) == []


def test_registry_document_kinds():
    data = load_registry(TEMPLATES_DIR)
    for name in CONCLUSION_TEMPLATES:
        assert data["document_kinds"].get(name) == "заключение"
        assert document_kind_from_registry(TEMPLATES_DIR, name) == "заключение"
        assert document_kind(name, templates_dir=TEMPLATES_DIR) == "заключение"
        # не в договорах комплекта
        for ctype, lst in data["contracts"].items():
            assert name not in lst


def test_fields_in_system_registry():
    invalidate_registry_cache()
    spec = get_standard_field("номер_заключения")
    assert spec is not None
    assert spec["type"] == "counter"
    q = get_standard_field("вопросы_эксперту")
    assert q is not None
    assert q["type"] == "multiline"


@pytest.mark.parametrize("name", CONCLUSION_TEMPLATES)
def test_fill_no_placeholders_and_org_header(name, tmp_path):
    src = TEMPLATES_DIR / name
    out = tmp_path / name
    ctx = build_context_for_template(name)
    fill_template(src, out, ctx, settings=ORG_SETTINGS)
    xml = ZipFile(out).read("word/document.xml").decode("utf-8")
    assert "{{" not in xml
    assert "}}" not in xml
    assert "7707817216" in xml
    assert "эталон" in xml.lower()
    # Заголовок в шаблоне — ЗАГЛАВНЫМИ
    assert "ЗАКЛЮЧЕНИЕ" in xml


@pytest.mark.parametrize("name", CONCLUSION_TEMPLATES)
def test_etalon_files_exist_and_clean(name):
    path = ETALON_DIR / name
    assert path.is_file(), f"Нет эталона {path}"
    xml = ZipFile(path).read("word/document.xml").decode("utf-8")
    assert "{{" not in xml
    assert path.stat().st_size > 500


def test_variables_exclude_settings_root():
    vars_ = list_template_variables(TEMPLATES_DIR / CONCLUSION_TEMPLATES[0])
    assert "настройки" not in vars_
    assert "номер_заключения" in vars_
    assert "вопросы_эксперту" in vars_
