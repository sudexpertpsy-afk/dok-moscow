"""
Централизованные пути данных приложения «Шаблонер».

Каталог программы (код, шаблоны Word) остаётся рядом с пакетом.
Изменяемые данные — в ~/Library/Application Support/Шаблонер/
(настройки, журнал, счётчики, реестры Excel, бэкапы).
Выходные документы — в ~/Documents/Шаблонер/Готовые_документы/.

При первом запуске migrate_legacy_data() копирует файлы со старых мест
(рядом с приложением) в новые каталоги с резервной копией исходников.
"""

from __future__ import annotations

import shutil
import unicodedata
from datetime import datetime
from pathlib import Path


APP_NAME = 'Шаблонер'

CONFIG_FILENAME = 'настройки.yaml'
COUNTERS_FILENAME = '.counters.json'
JOURNAL_FILENAME = '.journal.jsonl'
DB_FILENAME = 'shabloner.sqlite'
LOGS_DIRNAME = 'logs'
OUTPUT_DIRNAME = 'Готовые_документы'
MIGRATION_FLAG = '.migration_wp01_done'
DB_MIGRATION_FLAG = '.migration_wp02_done'

# Корень установки (рядом с пакетом docfiller/)
APP_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_ROOT / 'Шаблоны'


def normalize_name(text):
    """NFC-нормализация имён файлов (macOS часто хранит NFD)."""
    return unicodedata.normalize('NFC', str(text))


def data_dir():
    """~/Library/Application Support/Шаблонер/"""
    return Path.home() / 'Library' / 'Application Support' / APP_NAME


def documents_root():
    """~/Documents/Шаблонер/"""
    return Path.home() / 'Documents' / APP_NAME


def output_dir():
    """~/Documents/Шаблонер/Готовые_документы/"""
    return documents_root() / OUTPUT_DIRNAME


def backups_dir():
    """Резервные копии в Application Support."""
    return data_dir() / 'backups'


def config_path():
    return data_dir() / CONFIG_FILENAME


def counters_path():
    return data_dir() / COUNTERS_FILENAME


def journal_path():
    return data_dir() / JOURNAL_FILENAME


def db_path():
    """Файл SQLite в Application Support."""
    return data_dir() / DB_FILENAME


def logs_dir():
    """Каталог логов ошибок."""
    return data_dir() / LOGS_DIRNAME


def registries_dir():
    """Каталог Excel-реестров (тот же, что data_dir)."""
    return data_dir()


def ensure_dirs():
    """Создать каталоги данных и шаблонов, если их ещё нет."""
    data_dir().mkdir(parents=True, exist_ok=True)
    output_dir().mkdir(parents=True, exist_ok=True)
    backups_dir().mkdir(parents=True, exist_ok=True)
    logs_dir().mkdir(parents=True, exist_ok=True)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


def find_named(directory, filename):
    """Найти файл/папку в directory с учётом NFC/NFD."""
    directory = Path(directory)
    if not directory.exists():
        return None
    target = normalize_name(filename)
    direct = directory / filename
    if direct.exists():
        return direct
    for path in directory.iterdir():
        if normalize_name(path.name) == target:
            return path
    return None


def list_registry_files(directory=None):
    """Список файлов Реестр*.xlsx в каталоге данных (или указанном)."""
    base = Path(directory) if directory is not None else registries_dir()
    if not base.exists():
        return []
    return sorted(
        (
            p for p in base.glob('Реестр*.xlsx')
            if not p.name.startswith('~$')
            and '.example.' not in p.name
        ),
        key=lambda p: normalize_name(p.name),
    )


def _copy_file_if_needed(src, dst):
    """Скопировать файл, не перезаписывая существующий целевой."""
    src = Path(src)
    dst = Path(dst)
    if not src.is_file():
        return False
    if dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _copy_tree_if_needed(src, dst):
    """Скопировать каталог, если целевого ещё нет."""
    src = Path(src)
    dst = Path(dst)
    if not src.is_dir():
        return False
    if dst.exists():
        return False
    shutil.copytree(src, dst)
    return True


def migrate_legacy_data(app_root=None):
    """Перенести данные из каталога программы в стандартные пути.

    Идемпотентно: уже существующие целевые файлы не затираются.
    Перед копированием исходники дублируются в backups/pre_migration_*.
    Возвращает отчёт: migrated, skipped, backup_dir.
    """
    root = Path(app_root) if app_root is not None else APP_ROOT
    ensure_dirs()

    report = {
        'migrated': [],
        'skipped': [],
        'backup_dir': None,
    }

    jobs = []  # (src, dst, kind: 'file'|'dir')

    mapping = [
        (CONFIG_FILENAME, config_path()),
        (COUNTERS_FILENAME, counters_path()),
        (JOURNAL_FILENAME, journal_path()),
    ]
    for name, dest in mapping:
        src = find_named(root, name)
        if src is None:
            continue
        if dest.exists():
            report['skipped'].append(normalize_name(src.name))
            continue
        jobs.append((src, dest, 'file'))

    for src in list_registry_files(root):
        dest = registries_dir() / normalize_name(src.name)
        if dest.exists():
            report['skipped'].append(normalize_name(src.name))
            continue
        jobs.append((src, dest, 'file'))

    legacy_output = find_named(root, OUTPUT_DIRNAME)
    if legacy_output is not None and legacy_output.is_dir():
        for item in legacy_output.iterdir():
            if item.name == '.DS_Store':
                continue
            dest = output_dir() / item.name
            if dest.exists():
                report['skipped'].append(f'{OUTPUT_DIRNAME}/{item.name}')
                continue
            jobs.append((item, dest, 'dir' if item.is_dir() else 'file'))

    legacy_backups = find_named(root, '.backups')
    if legacy_backups is not None and legacy_backups.is_dir():
        for item in legacy_backups.iterdir():
            if not item.is_file() or item.name == '.DS_Store':
                continue
            dest = backups_dir() / item.name
            if dest.exists():
                report['skipped'].append(f'.backups/{item.name}')
                continue
            jobs.append((item, dest, 'file'))

    if not jobs:
        flag = data_dir() / MIGRATION_FLAG
        if not flag.exists():
            flag.write_text(
                datetime.now().isoformat(timespec='seconds') + '\n',
                encoding='utf-8',
            )
        return report

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_root = backups_dir() / f'pre_migration_{ts}'
    backup_root.mkdir(parents=True, exist_ok=True)
    report['backup_dir'] = str(backup_root)

    for src, dest, kind in jobs:
        try:
            bak = backup_root / src.name
            if kind == 'file':
                if not bak.exists():
                    shutil.copy2(src, bak)
                if _copy_file_if_needed(src, dest):
                    report['migrated'].append(
                        f'{src} → {dest}'
                    )
                else:
                    report['skipped'].append(normalize_name(src.name))
            else:
                if not bak.exists():
                    shutil.copytree(src, bak)
                if _copy_tree_if_needed(src, dest):
                    report['migrated'].append(
                        f'{src}/ → {dest}/'
                    )
                else:
                    report['skipped'].append(f'{src.name}/')
        except OSError as exc:
            report['skipped'].append(f'{src}: {exc}')

    flag = data_dir() / MIGRATION_FLAG
    flag.write_text(
        datetime.now().isoformat(timespec='seconds') + '\n',
        encoding='utf-8',
    )
    return report
