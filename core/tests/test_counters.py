"""Тесты счётчиков (T-9.3)."""

from docfiller_core import counters


def test_parse_and_format():
    p = counters.parse_number('2026-001')
    assert p['prefix'] == '2026-'
    assert p['number'] == 1
    assert counters.format_number(p) == '2026-001'


def test_no_cross_template_pollution_for_contract(tmp_path, monkeypatch):
    path = tmp_path / '.counters.json'
    monkeypatch.setattr(counters, 'COUNTERS_PATH', path)

    counters.remember_use('номер_договора', '2026-010', template_name='Договор_A.docx')
    # Другой шаблон не должен получить подсказку из общего ключа номер_договора
    assert counters.suggest_next('номер_договора', template_name='Договор_B.docx') == ''


def test_parse_rejects_terminal_garbage():
    """Мусор из терминала/буфера не должен раздувать суффикс номера."""
    junk = '009\n/1277У-2026/08-ФЛUsers/use/Downloads/...\nTraceback <frozen>'
    p = counters.parse_number(junk)
    assert p is not None
    assert p['number'] == 9
    assert '\n' not in p['prefix'] + p['suffix']
    assert 'Traceback' not in p['prefix'] + p['suffix']
    assert len(p['prefix'] + p['suffix']) <= 80


def test_load_cleans_corrupt_counter(tmp_path, monkeypatch):
    path = tmp_path / '.counters.json'
    monkeypatch.setattr(counters, 'COUNTERS_PATH', path)
    path.write_text(
        '{"номер_пко": {"prefix": "", "number": 8, "width": 3,'
        ' "suffix": "\\n/1277 junk Traceback <frozen>"}}',
        encoding='utf-8',
    )
    assert counters.suggest_next('номер_пко') == '009'

