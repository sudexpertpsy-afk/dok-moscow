"""Доступ к payment_settings: «настроен» = TerminalKey + пароль, без гейта Тест/Бой.

Настройки читаются из БД на каждый запрос (без кэша ORM между сессиями).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.billing.crypto import decrypt_secret
from app.models import PaymentMode, PaymentSettings
from app.services.billing import ensure_payment_settings

log = logging.getLogger("dok.billing")

TAXATION_CODES: frozenset[str] = frozenset(
    {
        "osn",
        "usn_income",
        "usn_income_outcome",
        "envd",
        "esn",
        "patent",
    }
)

VAT_CODES: frozenset[str] = frozenset(
    {
        "none",
        "vat0",
        "vat5",
        "vat7",
        "vat10",
        "vat22",
        "vat105",
        "vat107",
        "vat110",
        "vat122",
    }
)

# Человекочитаемые значения из админки / старых сохранений → коды API Т-Кассы
_TAXATION_ALIASES: dict[str, str] = {
    "усн": "usn_income",
    "усн 6%": "usn_income",
    "усн6%": "usn_income",
    "усн (доходы)": "usn_income",
    "усн доходы": "usn_income",
    "усн доходы-расходы": "usn_income_outcome",
    "усн д-р": "usn_income_outcome",
    "осно": "osn",
    "осн": "osn",
    "патент": "patent",
    "есхн": "esn",
    "енвд": "envd",
}

_VAT_ALIASES: dict[str, str] = {
    "без ндс": "none",
    "безндс": "none",
    "нет": "none",
    "0": "vat0",
    "ндс 0%": "vat0",
    "ндс 0": "vat0",
    "5%": "vat5",
    "ндс 5%": "vat5",
    "7%": "vat7",
    "ндс 7%": "vat7",
    "10%": "vat10",
    "ндс 10%": "vat10",
    "20%": "vat22",
    "22%": "vat22",
    "ндс 20%": "vat22",
    "ндс 22%": "vat22",
}


@dataclass(frozen=True)
class TerminalStatus:
    ready: bool
    test_mode: bool
    terminal_key: str | None
    taxation: str
    vat_rate: str
    recurrents_enabled: bool
    reason: str | None = None


def normalize_taxation(raw: str | None, *, default: str = "usn_income") -> str:
    text = (raw or "").strip()
    if not text:
        return default
    lower = text.lower().replace("ё", "е")
    if lower in TAXATION_CODES:
        return lower
    mapped = _TAXATION_ALIASES.get(lower)
    if mapped:
        return mapped
    if lower.replace("-", "_") in TAXATION_CODES:
        return lower.replace("-", "_")
    log.warning("unknown taxation %r → default %s", text, default)
    return default


def normalize_vat(raw: str | None, *, default: str = "none") -> str:
    text = (raw or "").strip()
    if not text:
        return default
    lower = text.lower().replace("ё", "е")
    if lower in VAT_CODES:
        return lower
    mapped = _VAT_ALIASES.get(lower)
    if mapped:
        return mapped
    log.warning("unknown vat_rate %r → default %s", text, default)
    return default


def invalidate_payment_settings_cache() -> None:
    """Совместимость: кэша ORM нет; вызов при сохранении админки оставляем явным."""
    return None


def get_payment_settings(db: Session) -> PaymentSettings:
    """Singleton id=1 из БД на текущий запрос."""
    return ensure_payment_settings(db)


def is_terminal_configured(row: PaymentSettings | None) -> bool:
    """Настроен = TerminalKey и пароль заполнены, независимо от Тест/Бой."""
    if row is None:
        return False
    return bool((row.terminal_key or "").strip() and (row.password_encrypted or "").strip())


def is_test_mode(row: PaymentSettings | None) -> bool:
    if row is None:
        return True
    mode = row.mode
    if isinstance(mode, PaymentMode):
        return mode == PaymentMode.test
    return str(mode) != PaymentMode.live.value


def terminal_status(db: Session) -> TerminalStatus:
    row = get_payment_settings(db)
    ready = is_terminal_configured(row)
    reason = None
    if not ready:
        if not (row.terminal_key or "").strip():
            reason = "не задан TerminalKey"
        elif not (row.password_encrypted or "").strip():
            reason = "не задан пароль терминала"
        else:
            reason = "настройки неполные"
    else:
        try:
            decrypt_secret(row.password_encrypted or "")
        except ValueError:
            ready = False
            reason = "пароль терминала не расшифровывается (проверьте SECRET_KEY)"
            log.error("payment_settings: decrypt failed for terminal password")
    return TerminalStatus(
        ready=ready,
        test_mode=is_test_mode(row),
        terminal_key=(row.terminal_key or "").strip() or None,
        taxation=normalize_taxation(row.taxation),
        vat_rate=normalize_vat(row.vat_rate),
        recurrents_enabled=bool(row.recurrents_enabled),
        reason=reason,
    )
