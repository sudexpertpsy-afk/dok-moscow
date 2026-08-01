"""Тесты логики мастера «Новый комплект» (T-5)."""

from pathlib import Path

import pytest

from docfiller_core import master
from docfiller_core.filler import list_template_variables
from docfiller_core.utils import resolve_template_path

TEMPLATES = Path(__file__).resolve().parent.parent / 'Шаблоны'


def _vars(tpl):
    return set(list_template_variables(resolve_template_path(TEMPLATES, tpl)))


def test_core_fields_cover_contracts():
    """Ядро — подмножество переменных каждого договора типа."""
    for тип in master.ТИПЫ:
        core = set(master.core_fields(тип))
        for tpl in master._CONTRACTS.get(тип, []):
            path = resolve_template_path(TEMPLATES, tpl)
            if not path.exists():
                continue
            contract_vars = _vars(tpl)
            assert core <= contract_vars, f'{тип}/{tpl}: ядро {core - contract_vars}'


def test_contract_options_include_without():
    opts = master.contract_options('Физлицо', TEMPLATES)
    assert master.БЕЗ_ДОГОВОРА in opts
    assert any('Договор_' in o for o in opts)
    assert 'Договор_освидетельствование.docx' in opts


def test_extra_options_examination_self_contained():
    """Счёт-договор уже содержит акт и ПКО — доп. пакет не предлагаем."""
    assert master.extra_options('Физлицо', 'Договор_освидетельствование.docx') == []


def test_extra_options_fiz_pko_default():
    extras = dict(master.extra_options('Физлицо', 'Договор_рецензия.docx'))
    assert extras.get('ПКО_КО-1.docx') is True
    assert extras.get('Счёт_на_оплату.docx') is True


def test_extra_options_jur_pko_off():
    extras = dict(master.extra_options('Юрлицо', 'Договор_услуги_юрлицо.docx'))
    assert extras.get('ПКО_КО-1.docx') is False


def test_extra_options_gpd():
    extras = dict(master.extra_options('Эксперт (ГПД)', 'Договор_ГПД_эксперт.docx'))
    assert 'Акт_ГПД_эксперт.docx' in extras
    assert 'Согласие_ПДн.docx' in extras


def test_collect_variables_no_duplicates():
    selected = ['Договор_рецензия.docx', 'Счёт_на_оплату.docx', 'Акт_оказанных_услуг.docx']
    core, additional = master.collect_variables(TEMPLATES, selected, 'Физлицо')
    assert not (set(core) & set(additional))
    merged = set(core) | set(additional)
    for tpl in selected:
        assert _vars(tpl) <= merged


def test_build_contexts_splits_values():
    selected = ['Договор_рецензия.docx', 'Счёт_на_оплату.docx']
    core = {'фио_клиента': 'Иванов', 'номер_договора': '2026-001'}
    extra = {'номер_счёта': 'СЧ-1', 'дата_счёта': '10.07.2026'}
    contexts = master.build_contexts(TEMPLATES, selected, core, extra)
    assert contexts['Договор_рецензия.docx']['фио_клиента'] == 'Иванов'
    assert contexts['Счёт_на_оплату.docx']['номер_счёта'] == 'СЧ-1'
    assert 'номер_счёта' not in contexts['Договор_рецензия.docx'] or (
        contexts['Договор_рецензия.docx'].get('номер_счёта', '') == ''
    )


def test_infer_type():
    assert master.infer_type_from_template('Договор_ГПД_эксперт.docx') == 'Эксперт (ГПД)'
    assert master.infer_type_from_template('Договор_услуги_юрлицо.docx') == 'Юрлицо'
    assert master.infer_type_from_template('Договор_рецензия.docx') == 'Физлицо'
    assert master.infer_type_from_template('Договор_освидетельствование.docx') == 'Физлицо'


def test_display_name_examination():
    assert master.display_name('Договор_освидетельствование.docx') == (
        'Договор освидетельствование'
    )
