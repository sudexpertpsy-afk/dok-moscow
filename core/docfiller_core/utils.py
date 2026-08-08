"""Утилиты, не требующие GUI. Можно использовать из любого модуля."""

import subprocess
import unicodedata
from datetime import date, datetime
from pathlib import Path


def normalize_name(text):
    """NFC-нормализация имён файлов (macOS хранит в NFD)."""
    return unicodedata.normalize('NFC', str(text))


def open_in_default_app(path):
    """Открыть файл в стандартном приложении macOS (или системы по умолчанию)."""
    try:
        subprocess.run(['open', str(path)], check=False)
    except Exception:
        pass


def reveal_in_finder(path):
    """Подсветить файл/папку в Finder."""
    try:
        path = Path(path)
        if path.is_file():
            subprocess.run(['open', '-R', str(path)], check=False)
        else:
            subprocess.run(['open', str(path)], check=False)
    except Exception:
        pass


def safe_filename(text, default='документ'):
    """Превратить строку в безопасное имя файла (без / \\ : * и т. п.)."""
    if not text:
        return default
    bad = '<>:"/\\|?*\n\r\t'
    name = ''.join('_' if c in bad else c for c in str(text)).strip(' .')
    return name[:120] or default


def is_date_field(name):
    """Поле выглядит как поле для даты — для автоподстановки сегодняшней даты в форме."""
    n = name.lower()
    return n.startswith('дата_') or n.endswith('_дата') or n == 'дата'


def is_multiline_field(name):
    """Многострочные поля формы — tk.Text вместо Entry."""
    prefixes = (
        'предмет_', 'новая_редакция_', 'дополнительные_условия',
        'цели_обработки', 'условия_расчётов', 'вопрос_',
    )
    exact = {
        'вопросы_эксперту',
        'объекты_исследования',
        'материалы_дела',
        'применённые_методы',
        'примененные_методы',
        'содержание_исследования',
        'оценка_результатов',
        'выводы',
        'дополнительные_обстоятельства',
        'приложения',
        'адресат',
        'что_направляется_вин',
        'основание_направления',
        'дополнительный_текст',
    }
    n = str(name)
    return n in exact or any(n.startswith(p) for p in prefixes)


def output_stem(context):
    """Имя выходного файла по приоритету полей контекста."""
    priority = (
        'номер_договора', 'номер_акта', 'номер_заключения', 'исх_номер',
        'фио_клиента', 'название_заказчика', 'фио_эксперта',
    )
    ctx = context or {}
    for key in priority:
        val = ctx.get(key)
        if val not in (None, ''):
            return safe_filename(str(val))
    for val in ctx.values():
        if val not in (None, ''):
            return safe_filename(str(val))
    return 'документ'


def resolve_template_path(templates_dir, name):
    """Найти файл шаблона с учётом NFC/NFD на macOS."""
    templates_dir = Path(templates_dir)
    target = normalize_name(name)
    for path in templates_dir.glob('*.docx'):
        if path.name.startswith('~$'):
            continue
        if normalize_name(path.name) == target:
            return path
    return templates_dir / name


def get_widget_value(widget):
    """Прочитать значение Entry или Text."""
    # Desktop UI helper (tk); оставляем для локального docfiller.
    import tkinter as tk
    if isinstance(widget, tk.Text):
        return widget.get('1.0', 'end-1c')
    return widget.get()


def daily_output_dir(base_dir, when=None):
    """Папка вывода за день: Готовые_документы/YYYY-MM-DD/."""
    base_dir = Path(base_dir)
    if when is None:
        day = date.today()
    elif isinstance(when, datetime):
        day = when.date()
    elif isinstance(when, date):
        day = when
    elif isinstance(when, str):
        day = datetime.strptime(when[:10], '%Y-%m-%d').date()
    else:
        day = date.today()
    folder = base_dir / day.strftime('%Y-%m-%d')
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def resolve_output_path(base_dir, stored_name):
    """Полный путь к сохранённому документу (поддержка подпапок по дате)."""
    base_dir = Path(base_dir)
    name = normalize_name(str(stored_name or ''))
    if not name:
        return base_dir
    return base_dir / name


def output_relative_path(base_dir, full_path):
    """Относительный путь для журнала/реестра от корня «Готовые_документы»."""
    base_dir = Path(base_dir)
    full_path = Path(full_path)
    try:
        return normalize_name(str(full_path.relative_to(base_dir)))
    except ValueError:
        return normalize_name(full_path.name)
