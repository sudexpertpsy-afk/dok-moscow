"""
SQLite-хранилище Шаблонера (WP-02).

Схема — Приложение А ТЗ этап 4–6 (имена таблиц латиницей).
Режим WAL, внешние ключи включены.
Чтение журнала UI идёт через journal → этот модуль;
JSONL остаётся страховочным дублем.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from . import paths


# Совместимость с тестами: monkeypatch DB_PATH.
DB_PATH = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS parties (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('fl','ul','expert')),
    name TEXT,
    fio TEXT,
    inn TEXT,
    kpp TEXT,
    ogrn TEXT,
    snils TEXT,
    passport_series TEXT,
    passport_number TEXT,
    passport_issuer TEXT,
    passport_date TEXT,
    address TEXT,
    phone TEXT,
    email TEXT,
    bank_bik TEXT,
    bank_account TEXT,
    notes TEXT,
    anonymized INTEGER NOT NULL DEFAULT 0,
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contracts (
    id INTEGER PRIMARY KEY,
    party_id INTEGER REFERENCES parties(id),
    template TEXT,
    number TEXT,
    status TEXT,
    date_start TEXT,
    date_end TEXT,
    amount TEXT,
    paid INTEGER NOT NULL DEFAULT 0,
    file_path TEXT,
    template_hash TEXT,
    created TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    contract_id INTEGER REFERENCES contracts(id),
    party_id INTEGER REFERENCES parties(id),
    template TEXT NOT NULL,
    number TEXT,
    file_path TEXT,
    context_json TEXT,
    template_hash TEXT,
    created TEXT NOT NULL,
    legacy_key TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS counters (
    key TEXT PRIMARY KEY,
    prefix TEXT,
    number INTEGER NOT NULL DEFAULT 0,
    width INTEGER NOT NULL DEFAULT 1,
    suffix TEXT,
    updated TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    object_type TEXT,
    object_id INTEGER,
    details_json TEXT,
    ts TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_created ON documents(created);
CREATE INDEX IF NOT EXISTS idx_documents_template ON documents(template);
CREATE INDEX IF NOT EXISTS idx_documents_party ON documents(party_id);
CREATE INDEX IF NOT EXISTS idx_contracts_party ON contracts(party_id);
CREATE INDEX IF NOT EXISTS idx_contracts_number ON contracts(number);
CREATE INDEX IF NOT EXISTS idx_parties_inn ON parties(inn);
CREATE INDEX IF NOT EXISTS idx_parties_fio ON parties(fio);
"""


def _active_db_path():
    if DB_PATH is not None:
        return Path(DB_PATH)
    return paths.db_path()


def connect(db_file=None):
    """Открыть соединение с WAL и внешними ключами."""
    path = Path(db_file) if db_file is not None else _active_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init_schema(conn=None):
    """Создать таблицы, если их ещё нет."""
    own = conn is None
    if own:
        conn = connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        if own:
            conn.close()


def _now():
    return datetime.now().isoformat(timespec='seconds')


def legacy_key(ts, template, output):
    """Идемпотентный ключ записи журнала."""
    raw = f'{ts or ""}|{template or ""}|{output or ""}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _party_kind(context):
    ctx = context or {}
    if ctx.get('название_заказчика') or ctx.get('инн_заказчика'):
        return 'ul'
    if ctx.get('фио_эксперта') and not ctx.get('фио_клиента'):
        return 'expert'
    return 'fl'


def _party_fields(context):
    ctx = context or {}
    kind = _party_kind(ctx)
    fio = str(ctx.get('фио_клиента') or ctx.get('фио_эксперта') or '').strip()
    name = str(ctx.get('название_заказчика') or '').strip()
    return {
        'kind': kind,
        'name': name or None,
        'fio': fio or None,
        'inn': str(ctx.get('инн') or ctx.get('инн_заказчика') or '').strip() or None,
        'kpp': str(ctx.get('кпп') or ctx.get('кпп_заказчика') or '').strip() or None,
        'ogrn': str(ctx.get('огрн') or '').strip() or None,
        'snils': str(ctx.get('снилс') or '').strip() or None,
        'passport_series': str(ctx.get('паспорт_серия') or '').strip() or None,
        'passport_number': str(ctx.get('паспорт_номер') or '').strip() or None,
        'passport_issuer': str(ctx.get('паспорт_кем_выдан') or '').strip() or None,
        'passport_date': str(ctx.get('паспорт_дата') or '').strip() or None,
        'address': str(
            ctx.get('адрес_клиента') or ctx.get('адрес_заказчика') or ''
        ).strip() or None,
        'phone': str(
            ctx.get('телефон_клиента') or ctx.get('телефон') or ''
        ).strip() or None,
        'email': str(
            ctx.get('email_клиента') or ctx.get('email') or ''
        ).strip() or None,
        'bank_bik': str(ctx.get('бик') or '').strip() or None,
        'bank_account': str(ctx.get('р_счёт') or ctx.get('расчётный_счёт') or '').strip() or None,
        'notes': None,
    }


def find_or_create_party(conn, context):
    """Найти контрагента по ИНН или ФИО/наименованию, иначе создать."""
    fields = _party_fields(context)
    now = _now()
    row = None
    if fields['inn']:
        row = conn.execute(
            'SELECT id FROM parties WHERE inn = ? AND anonymized = 0 LIMIT 1',
            (fields['inn'],),
        ).fetchone()
    if row is None and fields['fio'] and fields['passport_number']:
        row = conn.execute(
            'SELECT id FROM parties WHERE lower(fio) = lower(?) '
            'AND passport_number = ? AND anonymized = 0 LIMIT 1',
            (fields['fio'], fields['passport_number']),
        ).fetchone()
    if row is None and fields['fio']:
        row = conn.execute(
            'SELECT id FROM parties WHERE lower(fio) = lower(?) AND kind = ? '
            'AND anonymized = 0 LIMIT 1',
            (fields['fio'], fields['kind']),
        ).fetchone()
    if row is None and fields['name']:
        row = conn.execute(
            'SELECT id FROM parties WHERE lower(name) = lower(?) AND kind = ? '
            'AND anonymized = 0 LIMIT 1',
            (fields['name'], fields['kind']),
        ).fetchone()
    if row is not None:
        # Дозаполнить пустые реквизиты свежим контекстом
        pid = int(row['id'])
        existing = conn.execute('SELECT * FROM parties WHERE id = ?', (pid,)).fetchone()
        updates = {}
        for col in (
            'phone', 'email', 'address', 'inn', 'kpp', 'ogrn', 'snils',
            'passport_series', 'passport_number', 'passport_issuer', 'passport_date',
            'bank_bik', 'bank_account',
        ):
            new_val = fields.get(col)
            old_val = existing[col] if existing is not None else None
            if new_val and not old_val:
                updates[col] = new_val
        if updates:
            updates['updated'] = now
            sets = ', '.join(f'{k} = ?' for k in updates)
            conn.execute(
                f'UPDATE parties SET {sets} WHERE id = ?',
                (*updates.values(), pid),
            )
        return pid

    if not fields['fio'] and not fields['name']:
        return None

    cur = conn.execute(
        '''INSERT INTO parties (
            kind, name, fio, inn, kpp, ogrn, snils,
            passport_series, passport_number, passport_issuer, passport_date,
            address, phone, email, bank_bik, bank_account, notes,
            anonymized, created, updated
        ) VALUES (
            :kind, :name, :fio, :inn, :kpp, :ogrn, :snils,
            :passport_series, :passport_number, :passport_issuer, :passport_date,
            :address, :phone, :email, :bank_bik, :bank_account, :notes,
            0, :created, :updated
        )''',
        {**fields, 'created': now, 'updated': now},
    )
    return int(cur.lastrowid)


def record_document(template_name, output_name, context, ts=None, conn=None):
    """Записать документ в БД (идемпотентно по legacy_key)."""
    own = conn is None
    if own:
        conn = connect()
        init_schema(conn)
    try:
        created = ts or _now()
        key = legacy_key(created, template_name, output_name)
        existing = conn.execute(
            'SELECT id FROM documents WHERE legacy_key = ?', (key,)
        ).fetchone()
        if existing:
            return int(existing['id'])

        ctx = dict(context or {})
        party_id = find_or_create_party(conn, ctx)
        number = str(
            ctx.get('номер_договора')
            or ctx.get('номер_пко')
            or ctx.get('номер_счёта')
            or ''
        ).strip() or None

        contract_id = None
        tpl = str(template_name or '')
        if tpl.startswith('Договор_') and party_id is not None:
            cur = conn.execute(
                '''INSERT INTO contracts (
                    party_id, template, number, status, date_start, date_end,
                    amount, paid, file_path, template_hash, created
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, ?)''',
                (
                    party_id,
                    tpl,
                    number,
                    'active',
                    str(ctx.get('дата_договора') or '').strip() or None,
                    str(ctx.get('дата_окончания') or '').strip() or None,
                    str(ctx.get('сумма') or '').strip() or None,
                    str(output_name or ''),
                    created,
                ),
            )
            contract_id = int(cur.lastrowid)

        cur = conn.execute(
            '''INSERT INTO documents (
                contract_id, party_id, template, number, file_path,
                context_json, template_hash, created, legacy_key
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)''',
            (
                contract_id,
                party_id,
                tpl,
                number,
                str(output_name or ''),
                json.dumps(ctx, ensure_ascii=False),
                created,
                key,
            ),
        )
        if own:
            conn.commit()
        return int(cur.lastrowid)
    finally:
        if own:
            conn.close()


def upsert_counter(key, prefix, number, width, suffix, conn=None):
    """Записать/обновить счётчик."""
    own = conn is None
    if own:
        conn = connect()
        init_schema(conn)
    try:
        conn.execute(
            '''INSERT INTO counters (key, prefix, number, width, suffix, updated)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                 prefix=excluded.prefix,
                 number=excluded.number,
                 width=excluded.width,
                 suffix=excluded.suffix,
                 updated=excluded.updated''',
            (key, prefix or '', int(number), int(width or 1), suffix or '', _now()),
        )
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


def iter_document_records(newest_first=True):
    """Записи в формате журнала: {ts, template, output, context}."""
    path = _active_db_path()
    if not path.exists():
        return
    conn = connect(path)
    try:
        init_schema(conn)
        order = 'DESC' if newest_first else 'ASC'
        rows = conn.execute(
            f'SELECT created, template, file_path, context_json '
            f'FROM documents ORDER BY created {order}, id {order}'
        ).fetchall()
        for row in rows:
            try:
                ctx = json.loads(row['context_json'] or '{}')
            except Exception:
                ctx = {}
            yield {
                'ts': row['created'],
                'template': row['template'],
                'output': row['file_path'] or '',
                'context': ctx,
            }
    finally:
        conn.close()


def count_documents():
    path = _active_db_path()
    if not path.exists():
        return 0
    conn = connect(path)
    try:
        init_schema(conn)
        row = conn.execute('SELECT COUNT(*) AS n FROM documents').fetchone()
        return int(row['n'])
    finally:
        conn.close()


def count_counters():
    path = _active_db_path()
    if not path.exists():
        return 0
    conn = connect(path)
    try:
        init_schema(conn)
        row = conn.execute('SELECT COUNT(*) AS n FROM counters').fetchone()
        return int(row['n'])
    finally:
        conn.close()


def get_counter(key):
    """Вернуть словарь счётчика или None."""
    path = _active_db_path()
    if not path.exists():
        return None
    conn = connect(path)
    try:
        init_schema(conn)
        row = conn.execute(
            'SELECT key, prefix, number, width, suffix FROM counters WHERE key = ?',
            (key,),
        ).fetchone()
        if not row:
            return None
        return {
            'prefix': row['prefix'] or '',
            'number': int(row['number']),
            'width': int(row['width'] or 1),
            'suffix': row['suffix'] or '',
        }
    finally:
        conn.close()


def import_legacy(journal_file=None, counters_file=None, db_file=None):
    """Импорт .journal.jsonl и .counters.json в SQLite.

    Идемпотентно (по legacy_key / ключу счётчика). Делает бэкап исходников.
    Возвращает отчёт: journal_imported, counters_imported, skipped, backup_dir.
    """
    paths.ensure_dirs()
    jp = Path(journal_file) if journal_file else paths.journal_path()
    cp = Path(counters_file) if counters_file else paths.counters_path()
    dp = Path(db_file) if db_file else _active_db_path()

    report = {
        'journal_imported': 0,
        'counters_imported': 0,
        'skipped': 0,
        'backup_dir': None,
    }

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_root = paths.backups_dir() / f'pre_db_migration_{ts}'
    backup_root.mkdir(parents=True, exist_ok=True)
    report['backup_dir'] = str(backup_root)

    if jp.exists():
        shutil.copy2(jp, backup_root / jp.name)
    if cp.exists():
        shutil.copy2(cp, backup_root / cp.name)

    conn = connect(dp)
    try:
        init_schema(conn)

        if jp.exists():
            with jp.open(encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        report['skipped'] += 1
                        continue
                    key = legacy_key(
                        rec.get('ts'), rec.get('template'), rec.get('output'),
                    )
                    exists = conn.execute(
                        'SELECT 1 FROM documents WHERE legacy_key = ?', (key,)
                    ).fetchone()
                    if exists:
                        report['skipped'] += 1
                        continue
                    doc_id = record_document(
                        rec.get('template'),
                        rec.get('output'),
                        rec.get('context') or {},
                        ts=rec.get('ts'),
                        conn=conn,
                    )
                    if doc_id:
                        report['journal_imported'] += 1
                    else:
                        report['skipped'] += 1

        if cp.exists():
            try:
                raw = json.loads(cp.read_text(encoding='utf-8')) or {}
            except Exception:
                raw = {}
            for key, parsed in raw.items():
                if not isinstance(parsed, dict):
                    report['skipped'] += 1
                    continue
                try:
                    number = int(parsed['number'])
                    width = int(parsed.get('width') or 1)
                except (KeyError, TypeError, ValueError):
                    report['skipped'] += 1
                    continue
                exists = conn.execute(
                    'SELECT number FROM counters WHERE key = ?', (key,)
                ).fetchone()
                if exists is not None:
                    report['skipped'] += 1
                    continue
                upsert_counter(
                    key,
                    parsed.get('prefix', ''),
                    number,
                    width,
                    parsed.get('suffix', ''),
                    conn=conn,
                )
                report['counters_imported'] += 1

        conn.commit()
    finally:
        conn.close()

    flag = paths.data_dir() / paths.DB_MIGRATION_FLAG
    flag.write_text(_now() + '\n', encoding='utf-8')
    return report


def ensure_migrated():
    """При первом запуске импортировать legacy, если БД ещё пуста."""
    paths.ensure_dirs()
    init_schema()
    flag = paths.data_dir() / paths.DB_MIGRATION_FLAG
    if flag.exists() and count_documents() > 0:
        return {'journal_imported': 0, 'counters_imported': 0, 'skipped': 0}
    # Повторный импорт безопасен (идемпотентен)
    return import_legacy()


def _row_to_party(row):
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def get_party(party_id):
    """Карточка контрагента по id."""
    if party_id is None:
        return None
    conn = connect()
    try:
        init_schema(conn)
        row = conn.execute(
            'SELECT * FROM parties WHERE id = ?', (int(party_id),)
        ).fetchone()
        return _row_to_party(row)
    finally:
        conn.close()


def search_parties(query='', limit=50, *, include_anonymized=False):
    """Поиск по ФИО, наименованию, ИНН, телефону, e-mail, номеру договора.

    Фильтрация по Unicode — в Python (SQLite lower() не умеет кириллицу).
    """
    conn = connect()
    try:
        init_schema(conn)
        q = (query or '').strip().lower()
        anon_sql = '' if include_anonymized else 'AND p.anonymized = 0'
        rows = conn.execute(
            f'''SELECT p.* FROM parties p
                WHERE 1=1 {anon_sql}
                ORDER BY id DESC
                LIMIT 2000''',
        ).fetchall()
        parties = [_row_to_party(r) for r in rows]
        if not q:
            parties.sort(key=lambda p: (p.get('fio') or p.get('name') or '').lower())
            return parties[: int(limit)]

        # Номера договоров / документов (кириллица — тоже в Python)
        number_hits = set()
        for r in conn.execute(
            '''SELECT party_id, number, file_path FROM documents
               WHERE party_id IS NOT NULL'''
        ).fetchall():
            blob = f"{r['number'] or ''} {r['file_path'] or ''}".lower()
            if q in blob:
                number_hits.add(int(r['party_id']))
        for r in conn.execute(
            'SELECT party_id, number FROM contracts WHERE party_id IS NOT NULL'
        ).fetchall():
            if q in str(r['number'] or '').lower():
                number_hits.add(int(r['party_id']))

        matched = []
        for p in parties:
            hay = ' '.join([
                str(p.get('fio') or ''),
                str(p.get('name') or ''),
                str(p.get('inn') or ''),
                str(p.get('phone') or ''),
                str(p.get('email') or ''),
                str(p.get('address') or ''),
            ]).lower()
            if q in hay or p.get('id') in number_hits:
                matched.append(p)
            if len(matched) >= int(limit):
                break
        return matched
    finally:
        conn.close()


def party_display_name(party):
    if not party:
        return '—'
    return (
        str(party.get('fio') or '').strip()
        or str(party.get('name') or '').strip()
        or f"#{party.get('id')}"
    )


def party_kind_label(kind):
    return {'fl': 'Физлицо', 'ul': 'Юрлицо', 'expert': 'Эксперт'}.get(kind, kind or '')


def party_to_context(party):
    """Преобразовать карточку в поля формы/мастера."""
    if not party:
        return {}
    kind = party.get('kind') or 'fl'
    ctx = {}
    if kind == 'ul':
        if party.get('name'):
            ctx['название_заказчика'] = party['name']
        if party.get('inn'):
            ctx['инн_заказчика'] = party['inn']
        if party.get('kpp'):
            ctx['кпп_заказчика'] = party['kpp']
        if party.get('address'):
            ctx['адрес_заказчика'] = party['address']
        if party.get('phone'):
            ctx['телефон'] = party['phone']
        if party.get('email'):
            ctx['email'] = party['email']
    elif kind == 'expert':
        if party.get('fio'):
            ctx['фио_эксперта'] = party['fio']
        if party.get('inn'):
            ctx['инн'] = party['inn']
        if party.get('snils'):
            ctx['снилс'] = party['snils']
        if party.get('address'):
            ctx['адрес_клиента'] = party['address']
        if party.get('phone'):
            ctx['телефон_клиента'] = party['phone']
        if party.get('email'):
            ctx['email_клиента'] = party['email']
    else:
        if party.get('fio'):
            ctx['фио_клиента'] = party['fio']
        if party.get('inn'):
            ctx['инн'] = party['inn']
        if party.get('address'):
            ctx['адрес_клиента'] = party['address']
        if party.get('phone'):
            ctx['телефон_клиента'] = party['phone']
        if party.get('email'):
            ctx['email_клиента'] = party['email']
        if party.get('passport_series'):
            ctx['паспорт_серия'] = party['passport_series']
        if party.get('passport_number'):
            ctx['паспорт_номер'] = party['passport_number']
        if party.get('passport_issuer'):
            ctx['паспорт_кем_выдан'] = party['passport_issuer']
        if party.get('passport_date'):
            ctx['паспорт_дата'] = party['passport_date']
    if party.get('ogrn') and 'огрн' not in ctx:
        ctx['огрн'] = party['ogrn']
    if party.get('bank_bik'):
        ctx['бик'] = party['bank_bik']
    if party.get('bank_account'):
        ctx['р_счёт'] = party['bank_account']
    return {k: v for k, v in ctx.items() if v}


def list_party_documents(party_id, limit=100):
    """История документов контрагента."""
    conn = connect()
    try:
        init_schema(conn)
        rows = conn.execute(
            '''SELECT id, template, number, file_path, created, context_json
               FROM documents
               WHERE party_id = ?
               ORDER BY created DESC, id DESC
               LIMIT ?''',
            (int(party_id), int(limit)),
        ).fetchall()
        result = []
        for row in rows:
            try:
                ctx = json.loads(row['context_json'] or '{}')
            except Exception:
                ctx = {}
            result.append({
                'id': row['id'],
                'template': row['template'],
                'number': row['number'],
                'output': row['file_path'],
                'ts': row['created'],
                'context': ctx,
            })
        return result
    finally:
        conn.close()


def update_party(party_id, fields):
    """Обновить карточку; fields — словарь колонок БД."""
    allowed = {
        'kind', 'name', 'fio', 'inn', 'kpp', 'ogrn', 'snils',
        'passport_series', 'passport_number', 'passport_issuer', 'passport_date',
        'address', 'phone', 'email', 'bank_bik', 'bank_account', 'notes',
        'anonymized',
    }
    data = {k: fields[k] for k in fields if k in allowed}
    if not data:
        return False
    data['updated'] = _now()
    sets = ', '.join(f'{k} = :{k}' for k in data)
    data['id'] = int(party_id)
    conn = connect()
    try:
        init_schema(conn)
        conn.execute(f'UPDATE parties SET {sets} WHERE id = :id', data)
        conn.commit()
        return True
    finally:
        conn.close()


def save_party_from_context(context):
    """Публичная обёртка: найти или создать контрагента из контекста формы."""
    conn = connect()
    try:
        init_schema(conn)
        party_id = find_or_create_party(conn, context)
        conn.commit()
        return party_id
    finally:
        conn.close()


def log_event(kind, object_type=None, object_id=None, details=None):
    conn = connect()
    try:
        init_schema(conn)
        conn.execute(
            '''INSERT INTO events (kind, object_type, object_id, details_json, ts)
               VALUES (?, ?, ?, ?, ?)''',
            (
                kind,
                object_type,
                object_id,
                json.dumps(details or {}, ensure_ascii=False),
                _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
