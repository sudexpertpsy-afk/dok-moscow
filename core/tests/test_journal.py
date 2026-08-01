"""Тесты журнала документов (T-1)."""

import json
from docfiller_core import journal


def test_append_and_iter(tmp_path, monkeypatch):
    path = tmp_path / '.journal.jsonl'
    monkeypatch.setattr(journal, 'JOURNAL_PATH', path)

    journal.append('Договор_услуги_v2.docx', 'out.docx', {
        'номер_договора': '2026-004',
        'фио_клиента': 'Тестов Тест',
    })
    records = list(journal.iter_records())
    assert len(records) == 1
    assert records[0]['template'] == 'Договор_услуги_v2.docx'
    assert records[0]['context']['номер_договора'] == '2026-004'


def test_find_contracts(tmp_path, monkeypatch):
    path = tmp_path / '.journal.jsonl'
    monkeypatch.setattr(journal, 'JOURNAL_PATH', path)

    journal.append('Договор_услуги_v2.docx', 'a.docx', {
        'номер_договора': '2026-001',
        'фио_клиента': 'Иванов',
    })
    journal.append('Счёт_на_оплату.docx', 'b.docx', {'номер_счёта': '1'})

    found = journal.find_contracts(query='2026-001')
    assert len(found) == 1
    assert found[0]['context']['фио_клиента'] == 'Иванов'


def test_skip_broken_lines(tmp_path, monkeypatch):
    path = tmp_path / '.journal.jsonl'
    monkeypatch.setattr(journal, 'JOURNAL_PATH', path)
    path.write_text('not json\n{"ts":"x","template":"T","output":"o","context":{}}\n', encoding='utf-8')
    records = list(journal.iter_records())
    assert len(records) == 1
