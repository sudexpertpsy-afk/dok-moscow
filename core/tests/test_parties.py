"""Тесты картотеки контрагентов (WP-03)."""

from docfiller_core import db


def test_party_search_and_context(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'p.sqlite')
    db.init_schema()
    pid = db.save_party_from_context({
        'фио_клиента': 'Иванов Иван Иванович',
        'инн': '7707083893',
        'телефон_клиента': '89161234567',
        'email_клиента': 'a@b.ru',
        'адрес_клиента': 'Москва',
        'номер_договора': 'У-100',
    })
    # документ с номером договора — для поиска
    db.record_document(
        'Договор_услуги_v2.docx', 'out.docx',
        {
            'фио_клиента': 'Иванов Иван Иванович',
            'инн': '7707083893',
            'номер_договора': 'У-100',
        },
        ts='2026-07-30T10:00:00',
    )
    assert pid
    found = db.search_parties('Иванов')
    assert len(found) >= 1
    assert found[0]['fio'].startswith('Иванов')

    by_inn = db.search_parties('7707083893')
    assert by_inn

    by_contract = db.search_parties('У-100')
    assert by_contract

    party = db.get_party(pid)
    ctx = db.party_to_context(party)
    assert ctx['фио_клиента'].startswith('Иванов')
    assert ctx.get('инн') == '7707083893'

    docs = db.list_party_documents(pid)
    assert docs
    assert docs[0]['template'].startswith('Договор_')


def test_party_dedup_by_inn(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'p2.sqlite')
    db.init_schema()
    a = db.save_party_from_context({'фио_клиента': 'А', 'инн': '7707083893'})
    b = db.save_party_from_context({'фио_клиента': 'Б', 'инн': '7707083893'})
    assert a == b
