"""Маскирование ПДн и секретов в логах контейнеров (W-34 / как W-06)."""

from __future__ import annotations

import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S+"), r"\1=***"),
    (re.compile(r"(?i)authorization:\s*bearer\s+\S+"), "Authorization: Bearer ***"),
    (re.compile(r"\b\d{4}\s?\d{6}\b"), "**** ******"),  # паспорт грубо
    (re.compile(r"\b\d{10,12}\b"), lambda m: m.group(0)[:2] + "***" + m.group(0)[-2:]),  # ИНН
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "***@***"),
]


def mask_log_line(line: str) -> str:
    out = line or ""
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out


def mask_log_text(text: str) -> str:
    return "\n".join(mask_log_line(ln) for ln in (text or "").splitlines())
