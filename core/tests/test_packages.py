"""Тесты пакетов документов (T-4)."""

from docfiller_core import packages


def test_gpd_package():
    docs = packages.related_documents('Договор_ГПД_эксперт.docx')
    names = [n for n, _ in docs]
    assert 'Акт_ГПД_эксперт.docx' in names
    assert 'Согласие_ПДн.docx' in names
    assert 'Счёт_на_оплату.docx' not in names


def test_self_contained_examination_contract_has_no_package():
    assert packages.related_documents('Договор_освидетельствование.docx') == []


def test_gpd_suggest_fields():
    ctx = {
        'номер_договора': '2026-001',
        'фио_эксперта': 'Петров П.П.',
        'паспорт_эксперта': '1234 567890',
        'адрес_эксперта': 'г. Москва',
    }
    docs = packages.related_documents('Договор_ГПД_эксперт.docx')
    fields = packages.suggest_fields('Договор_ГПД_эксперт.docx', ctx, docs)
    assert fields['фио_субъекта'] == 'Петров П.П.'
    assert fields['паспорт_субъекта'] == '1234 567890'
    assert 'цели_обработки' in fields
