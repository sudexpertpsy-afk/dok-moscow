"""Тесты утилит (T-9)."""

from datetime import date
from pathlib import Path

from docfiller_core.utils import (
    output_stem,
    normalize_name,
    daily_output_dir,
    output_relative_path,
    resolve_output_path,
)


def test_output_stem_priority():
    ctx = {
        'фио_клиента': 'Иванов',
        'номер_договора': '2026-004',
    }
    assert output_stem(ctx) == '2026-004'


def test_output_stem_fallback():
    assert output_stem({'фио_клиента': 'Петров'}) == 'Петров'
    assert output_stem({}) == 'документ'


def test_normalize_name_nfc():
    assert normalize_name('Test.docx') == 'Test.docx'


def test_daily_output_dir(tmp_path):
    folder = daily_output_dir(tmp_path, when=date(2026, 7, 10))
    assert folder == tmp_path / '2026-07-10'
    assert folder.is_dir()


def test_output_relative_and_resolve(tmp_path):
    base = tmp_path / 'out'
    day = daily_output_dir(base, when=date(2026, 7, 10))
    full = day / 'doc.docx'
    full.write_text('x')
    rel = output_relative_path(base, full)
    assert rel == '2026-07-10/doc.docx'
    assert resolve_output_path(base, rel) == full
    assert resolve_output_path(base, 'doc.docx') == base / 'doc.docx'
