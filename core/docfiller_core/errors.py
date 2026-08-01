"""
Журнал ошибок приложения (WP-06).

Ротация logs/шаблонер.log (5×2 МБ), маскирование ПДн в сообщениях,
перехват необработанных исключений.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
import traceback
from pathlib import Path

from . import paths


LOG_NAME = 'shabloner'
LOG_FILENAME = 'шаблонер.log'

# Поля/паттерны, которые нельзя писать в лог как есть.
_PDN_KEYS = (
    'фио_клиента', 'фио_эксперта', 'паспорт_серия', 'паспорт_номер',
    'паспорт_кем_выдан', 'снилс', 'телефон', 'телефон_клиента',
    'email', 'email_клиента', 'адрес_клиента', 'адрес_заказчика',
    'инн', 'инн_заказчика',
)

_EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
_PHONE_RE = re.compile(r'(?<!\d)(?:\+7|8|7)[\s\-()]*\d(?:[\s\-()]*\d){9}(?!\d)')
_PASSPORT_RE = re.compile(r'\b\d{2}\s?\d{2}\s?\d{6}\b')
_INN_RE = re.compile(r'\b\d{10}(\d{2})?\b')
_SNILS_RE = re.compile(r'\b\d{3}-\d{3}-\d{3}\s?\d{2}\b')


def logs_dir():
    return paths.logs_dir()


def log_file_path():
    return logs_dir() / LOG_FILENAME


def mask_pdn(text):
    """Убрать/замаскировать персональные данные в произвольной строке."""
    if text is None:
        return ''
    s = str(text)
    s = _EMAIL_RE.sub('[email]', s)
    s = _PHONE_RE.sub('[телефон]', s)
    s = _SNILS_RE.sub('[снилс]', s)
    s = _PASSPORT_RE.sub('[паспорт]', s)
    # ИНН маскируем осторожно — только явные 10/12 рядом с меткой
    s = re.sub(
        r'(инн\s*[:＝=]?\s*)\d{10,12}',
        r'\1[инн]',
        s,
        flags=re.IGNORECASE,
    )
    for key in _PDN_KEYS:
        s = re.sub(
            rf'({re.escape(key)}\s*[:＝=]\s*)([^\s,;|}}]+)',
            r'\1***',
            s,
            flags=re.IGNORECASE,
        )
    return s


def mask_context(context):
    """Словарь контекста с замаскированными ПДн (для логов)."""
    result = {}
    for key, val in (context or {}).items():
        if key in _PDN_KEYS or any(p in key for p in ('фио', 'паспорт', 'телефон', 'email', 'адрес', 'снилс')):
            result[key] = '***'
        else:
            result[key] = val
    return result


class _PdnFilter(logging.Filter):
    def filter(self, record):
        try:
            if isinstance(record.msg, str):
                record.msg = mask_pdn(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: mask_pdn(v) for k, v in record.args.items()}
                elif isinstance(record.args, tuple):
                    record.args = tuple(mask_pdn(a) for a in record.args)
        except Exception:
            pass
        return True


_configured = False


def setup_logging():
    """Настроить ротацию файла логов. Идемпотентно."""
    global _configured
    if _configured:
        return logging.getLogger(LOG_NAME)

    paths.ensure_dirs()
    logs_dir().mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOG_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    handler = logging.handlers.RotatingFileHandler(
        str(log_file_path()),
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8',
    )
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    ))
    handler.addFilter(_PdnFilter())
    logger.addHandler(handler)
    logger.propagate = False
    _configured = True
    return logger


def get_logger():
    if not _configured:
        return setup_logging()
    return logging.getLogger(LOG_NAME)


def log_exception(exc, *, context=None, where=''):
    """Записать исключение без ПДн."""
    logger = get_logger()
    tb = mask_pdn(''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    extra = ''
    if context:
        extra = ' context=' + mask_pdn(repr(mask_context(context)))
    where_s = f' ({where})' if where else ''
    logger.error('Unhandled%s: %s%s\n%s', where_s, mask_pdn(repr(exc)), extra, tb)


def install_excepthook(show_dialog=None):
    """Перехватить необработанные исключения.

    show_dialog(exc, log_path) — опциональный callback для UI.
    """
    setup_logging()
    previous = sys.excepthook

    def _hook(exc_type, exc, tb):
        try:
            log_exception(exc, where='excepthook')
        except Exception:
            pass
        if show_dialog is not None:
            try:
                show_dialog(exc, log_file_path())
                return
            except Exception:
                pass
        previous(exc_type, exc, tb)

    sys.excepthook = _hook


def open_logs_folder():
    from .utils import reveal_in_finder
    path = logs_dir()
    path.mkdir(parents=True, exist_ok=True)
    reveal_in_finder(path)
