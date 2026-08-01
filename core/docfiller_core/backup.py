"""
Автоматическое резервное копирование (WP-05).

Ежедневный zip: база, настройки, шаблоны → Application Support/…/backups/
Ротация: 30 суточных + 12 месячных. Опционально вторая копия в доп. папку.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from . import paths


PREFS_NAME = 'backup_prefs.json'
DAILY_KEEP = 30
MONTHLY_KEEP = 12
STATE_NAME = '.last_backup_day'


def _prefs_path():
    return paths.data_dir() / PREFS_NAME


def load_prefs():
    path = _prefs_path()
    if not path.exists():
        return {'extra_folder': ''}
    try:
        data = json.loads(path.read_text(encoding='utf-8')) or {}
    except Exception:
        data = {}
    return {
        'extra_folder': str(data.get('extra_folder') or ''),
    }


def save_prefs(prefs):
    paths.ensure_dirs()
    _prefs_path().write_text(
        json.dumps(prefs or {}, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )


def set_extra_folder(folder):
    prefs = load_prefs()
    prefs['extra_folder'] = str(folder or '')
    save_prefs(prefs)


def _last_backup_marker():
    return paths.backups_dir() / STATE_NAME


def _today():
    return datetime.now().strftime('%Y-%m-%d')


def should_run_today():
    marker = _last_backup_marker()
    if not marker.exists():
        return True
    try:
        return marker.read_text(encoding='utf-8').strip() != _today()
    except Exception:
        return True


def _archive_name(kind='daily'):
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    return f'{kind}_{ts}.zip'


def create_backup(*, force=False, kind=None):
    """Создать zip-бэкап. Возвращает путь или None, если сегодня уже был."""
    paths.ensure_dirs()
    if not force and not should_run_today():
        return None

    day = _today()
    kind = kind or ('monthly' if day.endswith('-01') else 'daily')
    out = paths.backups_dir() / _archive_name(kind)

    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        mapping = [
            (paths.db_path(), 'data/shabloner.sqlite'),
            (paths.config_path(), 'data/настройки.yaml'),
            (paths.journal_path(), 'data/.journal.jsonl'),
            (paths.counters_path(), 'data/.counters.json'),
            (_prefs_path(), 'data/backup_prefs.json'),
        ]
        for src, arc in mapping:
            if src.exists() and src.is_file():
                zf.write(src, arc)
        templates = paths.TEMPLATES_DIR
        if templates.exists():
            for path in templates.rglob('*'):
                if path.is_file() and path.name != '.DS_Store':
                    zf.write(path, f'Шаблоны/{path.relative_to(templates)}')

    # Проверка целостности
    try:
        with zipfile.ZipFile(out, 'r') as zf:
            bad = zf.testzip()
            if bad:
                out.unlink(missing_ok=True)
                raise RuntimeError(f'Повреждённый архив (файл {bad})')
    except zipfile.BadZipFile as exc:
        out.unlink(missing_ok=True)
        raise RuntimeError('Не удалось создать корректный zip-архив') from exc

    prefs = load_prefs()
    extra = prefs.get('extra_folder') or ''
    if extra:
        dest_dir = Path(extra).expanduser()
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out, dest_dir / out.name)
        except OSError:
            pass

    _last_backup_marker().write_text(day + '\n', encoding='utf-8')
    rotate_backups()
    return out


def list_backups():
    """Список архивов от новых к старым."""
    folder = paths.backups_dir()
    if not folder.exists():
        return []
    files = [
        p for p in folder.glob('*.zip')
        if p.name.startswith(('daily_', 'monthly_', 'manual_'))
    ]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def rotate_backups():
    files = list_backups()
    daily = [p for p in files if p.name.startswith('daily_')]
    monthly = [p for p in files if p.name.startswith('monthly_')]
    for obsolete in daily[DAILY_KEEP:]:
        try:
            obsolete.unlink()
        except OSError:
            pass
    for obsolete in monthly[MONTHLY_KEEP:]:
        try:
            obsolete.unlink()
        except OSError:
            pass


def restore_backup(archive_path, *, restore_templates=False):
    """Восстановить базу и настройки из архива.

    Шаблоны по умолчанию не перезаписываются (осторожность).
    Возвращает отчёт {restored: [...], errors: [...]}.
    """
    archive_path = Path(archive_path)
    report = {'restored': [], 'errors': []}
    if not archive_path.exists():
        report['errors'].append('Файл архива не найден')
        return report

    try:
        zf = zipfile.ZipFile(archive_path, 'r')
    except zipfile.BadZipFile:
        report['errors'].append('Архив повреждён или это не zip-файл')
        return report

    with zf:
        bad = zf.testzip()
        if bad:
            report['errors'].append(f'Архив повреждён (файл {bad})')
            return report

        paths.ensure_dirs()
        # Снимок текущих данных перед восстановлением
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safety = paths.backups_dir() / f'pre_restore_{stamp}'
        safety.mkdir(parents=True, exist_ok=True)
        for src in (paths.db_path(), paths.config_path()):
            if src.exists():
                shutil.copy2(src, safety / src.name)

        mapping = {
            'data/shabloner.sqlite': paths.db_path(),
            'data/настройки.yaml': paths.config_path(),
            'data/.journal.jsonl': paths.journal_path(),
            'data/.counters.json': paths.counters_path(),
            'data/backup_prefs.json': _prefs_path(),
        }
        for arc_name, dest in mapping.items():
            try:
                data = zf.read(arc_name)
            except KeyError:
                continue
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                report['restored'].append(str(dest.name))
            except OSError as exc:
                report['errors'].append(f'{dest.name}: {exc}')

        if restore_templates:
            for info in zf.infolist():
                if not info.filename.startswith('Шаблоны/') or info.is_dir():
                    continue
                rel = info.filename[len('Шаблоны/'):]
                if not rel or rel.endswith('/'):
                    continue
                dest = paths.TEMPLATES_DIR / rel
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(info.filename))
                    report['restored'].append(f'Шаблоны/{rel}')
                except OSError as exc:
                    report['errors'].append(f'{rel}: {exc}')

    return report


def maybe_backup_on_startup():
    """Вызвать при запуске приложения (не чаще раза в сутки)."""
    try:
        return create_backup(force=False)
    except Exception:
        return None
