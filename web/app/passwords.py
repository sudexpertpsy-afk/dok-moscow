"""Политика паролей (W-09 / ASVS L1)."""

from __future__ import annotations

import re

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128

_HAS_LETTER = re.compile(r"[A-Za-zА-Яа-яЁё]")
_HAS_DIGIT = re.compile(r"\d")


def validate_password(password: str, *, email: str | None = None) -> str | None:
    """Вернуть текст ошибки или None, если пароль допустим."""
    if password is None:
        return "Укажите пароль."
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Пароль не короче {MIN_PASSWORD_LENGTH} символов."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Пароль не длиннее {MAX_PASSWORD_LENGTH} символов."
    if password.strip() != password:
        return "Пароль не должен начинаться или заканчиваться пробелом."
    if not _HAS_LETTER.search(password) or not _HAS_DIGIT.search(password):
        return "Пароль должен содержать хотя бы одну букву и одну цифру."
    if email:
        local = email.strip().lower().split("@", 1)[0]
        if local and local in password.lower():
            return "Пароль не должен содержать часть e-mail."
    return None


def password_policy_hint() -> str:
    return (
        f"Не короче {MIN_PASSWORD_LENGTH} символов, буква и цифра, "
        "без части вашего e-mail."
    )
