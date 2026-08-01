"""Тесты SQLite и миграции (WP-02)."""

import json
from pathlib import Path

from docfiller_core import db, journal, paths


def test_schema_and_record(tmp_path, monkeypatch):
    db_file = tmp_path / 't.sqlite'
    monkeypatch.setattr(db, 'DB_PATH', db_file)
    db.init_schema()
    doc_id = db.record_document(
        'Договор_услуги_v2.docx',
        '2026-07-30/out.docx',
        {'фио_клиента': 'Иванов Иван', 'номер_договора': 'У-1', 'сумма': '1000'},
        ts='2026-07-30T12:00:00',
    )
    assert doc_id > 0
    assert db.count_documents() == 1
    rows = list(db.iter_document_records())
    assert len(rows) == 1
    assert rows[0]['context']['фио_клиента'] == 'Иванов Иван'


def test_import_legacy_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'data_dir', lambda: tmp_path)
    monkeypatch.setattr(paths, 'backups_dir', lambda: tmp_path / 'backups')
    (tmp_path / 'backups').mkdir()

    journal_file = tmp_path / '.journal.jsonl'
    counters_file = tmp_path / '.counters.json'
    db_file = tmp_path / 'shabloner.sqlite'
    monkeypatch.setattr(db, 'DB_PATH', db_file)

    records = [
        {
            'ts': '2026-01-01T10:00:00',
            'template': 'Договор_услуги_v2.docx',
            'output': 'a.docx',
            'context': {'фио_клиента': 'Петров', 'номер_договора': '1'},
        },
        {
            'ts': '2026-01-02T10:00:00',
            'template': 'Счёт_на_оплату.docx',
            'output': 'b.docx',
            'context': {'номер_счёта': '2'},
        },
    ]
    journal_file.write_text(
        '\n'.join(json.dumps(r, ensure_ascii=False) for r in records) + '\n',
        encoding='utf-8',
    )
    counters_file.write_text(json.dumps({
        'Договор_услуги_v2.docx::номер_договора': {
            'prefix': 'У-', 'number': 5, 'width': 1, 'suffix': '',
        },
    }, ensure_ascii=False), encoding='utf-8')

    r1 = db.import_legacy(journal_file, counters_file, db_file)
    assert r1['journal_imported'] == 2
    assert r1['counters_imported'] == 1
    assert db.count_documents() == 2
    assert db.count_counters() == 1

    r2 = db.import_legacy(journal_file, counters_file, db_file)
    assert r2['journal_imported'] == 0
    assert db.count_documents() == 2
    assert db.count_counters() == 1

    ctr = db.get_counter('Договор_услуги_v2.docx::номер_договора')
    assert ctr['number'] == 5
    assert ctr['prefix'] == 'У-'


def test_journal_reads_from_db(tmp_path, monkeypatch):
    jpath = tmp_path / '.journal.jsonl'
    monkeypatch.setattr(journal, 'JOURNAL_PATH', jpath)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'shabloner.sqlite')

    journal.append('Договор_услуги_v2.docx', 'x.docx', {
        'фио_клиента': 'Сидоров', 'номер_договора': '99',
    })
    found = journal.find_contracts(query='Сидоров')
    assert len(found) == 1
    assert found[0]['context']['номер_договора'] == '99'
    # JSONL дубль тоже есть
    assert jpath.exists()
    assert len(jpath.read_text(encoding='utf-8').strip().splitlines()) == 1
