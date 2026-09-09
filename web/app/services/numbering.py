"""W-49 B: шаблоны номеров документов (паритет с legacy prefix+n+suffix)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

# Токены шаблона. Legacy-эквивалент: «{префикс}{n}{суффикс}».
TOKEN_RE = re.compile(
    r"\{(префикс|суффикс|n|nn|nnn|гггг|гг)\}"
)

DEFAULT_TEMPLATE = "{префикс}{n}{суффикс}"

# Ключи счётчиков, которые показываем в настройках (даже если ещё не созданы).
COUNTER_CATALOG: tuple[tuple[str, str], ...] = (
    ("dogovor", "Договоры"),
    ("schet", "Счета"),
    ("akt", "Акты"),
    ("pko", "ПКО"),
    ("rko", "РКО"),
    ("payment", "Платёжные поручения"),
    ("upd", "УПД"),
    ("sf", "Счета-фактуры"),
    ("sverka", "Акты сверки"),
    ("dopsogl", "Доп. соглашения"),
    ("ishod", "Исходящие письма"),
    ("zakl_gpk", "Заключения (ГПК)"),
    ("zakl_upk", "Заключения (УПК)"),
)


class NumberingError(ValueError):
    """Ошибка настройки нумерации."""


def pad_n(n: int, width: int) -> str:
    if width <= 0:
        return str(int(n))
    return str(int(n)).zfill(width)


def render_number(
    template: str | None,
    *,
    n: int,
    prefix: str = "",
    suffix: str = "",
    width: int = 0,
    on: date | None = None,
) -> str:
    """Собрать номер. template=None/пустой → legacy prefix+zfill(n)+suffix."""
    pref = prefix or ""
    suf = suffix or ""
    day = on or datetime.now(timezone.utc).date()
    tmpl = (template or "").strip()
    if not tmpl:
        return f"{pref}{pad_n(n, width)}{suf}"

    def _repl(m: re.Match[str]) -> str:
        tok = m.group(1)
        if tok == "префикс":
            return pref
        if tok == "суффикс":
            return suf
        if tok == "n":
            return pad_n(n, width)
        if tok == "nn":
            return pad_n(n, 2)
        if tok == "nnn":
            return pad_n(n, 3)
        if tok == "гггг":
            return f"{day.year:04d}"
        if tok == "гг":
            return f"{day.year % 100:02d}"
        return m.group(0)

    return TOKEN_RE.sub(_repl, tmpl)


def template_has_year(template: str | None) -> bool:
    t = template or ""
    return "{гггг}" in t or "{гг}" in t


def validate_template(template: str) -> str:
    t = (template or "").strip()
    if not t:
        return DEFAULT_TEMPLATE
    if len(t) > 128:
        raise NumberingError("Шаблон номера не длиннее 128 символов.")
    # Неизвестные {…} — ошибка (кроме наших токенов).
    for brace in re.findall(r"\{[^}]+\}", t):
        inner = brace[1:-1]
        if inner not in {"префикс", "суффикс", "n", "nn", "nnn", "гггг", "гг"}:
            raise NumberingError(f"Неизвестный токен в шаблоне: {brace}")
    if "{n}" not in t and "{nn}" not in t and "{nnn}" not in t:
        raise NumberingError("В шаблоне нужен токен номера: {n}, {nn} или {nnn}.")
    return t


@dataclass(frozen=True)
class CounterView:
    key: str
    label: str
    prefix: str
    suffix: str
    value: int  # уже выданный максимум (0 = ещё не выдавали)
    template: str
    reset_yearly: bool
    preview: str
    exists: bool


def catalog_label(key: str) -> str:
    for k, label in COUNTER_CATALOG:
        if k == key:
            return label
    return key
