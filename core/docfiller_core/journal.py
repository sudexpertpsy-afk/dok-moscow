"""
Журнал выписанных документов.

Запись: JSONL (страховочный дубль) + SQLite (основное чтение UI).
Чтение: сначала база; если пуста — fallback на JSONL.
"""

import json
from datetime import datetime
from pathlib import Path

from . import paths


# Совместимость: тесты подменяют JOURNAL_PATH через monkeypatch.
JOURNAL_PATH = paths.journal_path()
APP_ROOT = paths.APP_ROOT


def _active_journal_path():
    return JOURNAL_PATH if JOURNAL_PATH is not None else paths.journal_path()


def _db_module():
    """Подготовить db: при тестовом JOURNAL_PATH — sqlite рядом с ним."""
    from . import db as dbmod

    jp = Path(_active_journal_path())
    default_jp = Path(paths.journal_path())
    if jp.resolve() != default_jp.resolve():
        dbmod.DB_PATH = jp.parent / 'shabloner.sqlite'
    return dbmod


def append(template_name, output_name, context):
    """Добавить запись о созданном документе."""
    ts = datetime.now().isoformat(timespec='seconds')
    record = {
        'ts': ts,
        'template': template_name,
        'output': output_name,
        'context': dict(context or {}),
    }
    try:
        path = _active_journal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception:
        pass

    try:
        _db_module().record_document(
            template_name, output_name, context, ts=ts,
        )
    except Exception:
        pass


def iter_records(newest_first=True):
    """Генератор записей журнала; битые строки пропускаются."""
    try:
        rows = list(_db_module().iter_document_records(newest_first=newest_first))
        if rows:
            for rec in rows:
                yield rec
            return
    except Exception:
        pass

    path = _active_journal_path()
    if not path.exists():
        return
    try:
        with path.open(encoding='utf-8') as f:
            lines = f.readlines()
    except Exception:
        return
    ordered = reversed(lines) if newest_first else lines
    for line in ordered:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue


def find_contracts(query='', limit=30):
    """Договоры из журнала для выбора в связанных документах."""
    q = (query or '').lower()
    results = []
    for rec in iter_records(newest_first=True):
        tpl = str(rec.get('template') or '')
        if not tpl.startswith('Договор_'):
            continue
        ctx = rec.get('context') or {}
        if q:
            haystack = ' '.join([
                str(ctx.get('номер_договора', '')),
                str(ctx.get('фио_клиента', '')),
                str(ctx.get('название_заказчика', '')),
                str(ctx.get('фио_эксперта', '')),
            ]).lower()
            if q not in haystack:
                continue
        results.append(rec)
        if len(results) >= limit:
            break
    return results


def last_record_for(template_name):
    """Последняя запись по имени шаблона."""
    for rec in iter_records(newest_first=True):
        if rec.get('template') == template_name:
            return rec
    return None
