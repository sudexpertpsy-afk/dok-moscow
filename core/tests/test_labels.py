"""Тесты словаря подписей (T-2)."""

from pathlib import Path

import pytest

from docfiller_core.filler import list_template_variables
from docfiller_core.labels import подпись, ПОДПИСИ
from docfiller_core.utils import resolve_template_path

TEMPLATES = Path(__file__).resolve().parent.parent / 'Шаблоны'


def _all_template_vars():
    result = set()
    for p in TEMPLATES.glob('*.docx'):
        if p.name.startswith('~$'):
            continue
        try:
            result.update(list_template_variables(resolve_template_path(TEMPLATES, p.name)))
        except Exception:
            pass
    return result


@pytest.mark.parametrize('var_name', sorted(_all_template_vars()))
def test_label_no_underscore(var_name):
    text = подпись(var_name)
    assert '_' not in text
    assert text.strip()


def test_all_vars_in_dictionary():
    missing = _all_template_vars() - set(ПОДПИСИ.keys())
    assert not missing, f'Нет подписей для: {missing}'
