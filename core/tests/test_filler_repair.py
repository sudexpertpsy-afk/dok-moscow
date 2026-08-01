"""Тесты починки таблиц в docx после docxtpl."""

import zipfile
from pathlib import Path

from lxml import etree

from docfiller_core.filler import repair_document_tables, fill_template
from fixtures_sample_context import build_context_for_template

TEMPLATES = Path(__file__).resolve().parent.parent / 'Шаблоны'
_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def _table_grid_vs_spans(docx_path):
    """Пары (grid_cols, max_row_span) по прямым ячейкам — без вложенных таблиц."""
    root = etree.fromstring(zipfile.ZipFile(docx_path).read('word/document.xml'))
    result = []
    for tbl in root.iter(_W + 'tbl'):
        grid = tbl.find(f'{_W}tblGrid')
        grid_cols = len(list(grid)) if grid is not None else 0
        max_span = 0
        for tr in tbl:
            if tr.tag != f'{_W}tr':
                continue
            span = 0
            for tc in tr:
                if tc.tag != f'{_W}tc':
                    continue
                gs = 1
                tc_pr = tc.find(f'{_W}tcPr')
                if tc_pr is not None:
                    g_el = tc_pr.find(f'{_W}gridSpan')
                    if g_el is not None:
                        gs = int(g_el.get(f'{_W}val') or 1)
                span += gs
            max_span = max(max_span, span)
        result.append((grid_cols, max_span))
    return result


def test_repair_pko_template_grids(tmp_path):
    """После рендера ПКО сетка колонок совпадает с числом ячеек."""
    tpl = TEMPLATES / 'ПКО_КО-1.docx'
    out = tmp_path / 'pko.docx'
    ctx = build_context_for_template('ПКО_КО-1.docx')
    fill_template(tpl, out, ctx)
    pairs = _table_grid_vs_spans(out)
    assert pairs, 'ожидались таблицы в ПКО'
    for grid_cols, max_span in pairs:
        assert grid_cols == max_span, f'grid {grid_cols} != span {max_span}'


def test_repair_pko_keeps_outer_three_columns(tmp_path):
    """Внешняя таблица ордер|линия|квитанция остаётся трёхколоночной."""
    tpl = TEMPLATES / 'ПКО_КО-1.docx'
    out = tmp_path / 'pko.docx'
    ctx = build_context_for_template('ПКО_КО-1.docx')
    fill_template(tpl, out, ctx)
    root = etree.fromstring(zipfile.ZipFile(out).read('word/document.xml'))
    outer = root.find(f'.//{_W}tbl')
    assert outer is not None
    grid = outer.find(f'{_W}tblGrid')
    assert len(list(grid)) == 3
    row = next(c for c in outer if c.tag == f'{_W}tr')
    cells = [c for c in row if c.tag == f'{_W}tc']
    assert len(cells) == 3
    xml = zipfile.ZipFile(out).read('word/document.xml').decode('utf-8')
    assert 'ns0:' not in xml


def test_repair_document_tables_idempotent(tmp_path):
    tpl = TEMPLATES / 'ПКО_КО-1.docx'
    out = tmp_path / 'pko.docx'
    ctx = build_context_for_template('ПКО_КО-1.docx')
    fill_template(tpl, out, ctx)
    before = out.read_bytes()
    repair_document_tables(out)
    repair_document_tables(out)
    assert out.read_bytes() == before
