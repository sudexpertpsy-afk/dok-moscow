"""Парсер deploy.log для PREV_TAG (W-50 A.1) — тестируемый отдельно от bash."""

from __future__ import annotations

import re

_OK_RE = re.compile(
    r"tag=(?P<tag>\S+)\s+sha=(?P<sha>\S+)\s+status=(?P<status>ok|fail)\s+df=(?P<df>\S+)"
)
_LEGACY_DONE_RE = re.compile(r"deploy done tag=([^\s]+)")


def parse_ok_tags(log_text: str) -> list[str]:
    """Список тегов status=ok (или legacy deploy done) в порядке появления."""
    tags: list[str] = []
    for line in log_text.splitlines():
        m = _OK_RE.search(line)
        if m and m.group("status") == "ok":
            tags.append(m.group("tag"))
            continue
        lm = _LEGACY_DONE_RE.search(line)
        if lm:
            tags.append(lm.group(1))
    return tags


def previous_ok_tag(log_text: str) -> str | None:
    """Предпоследний успешный тег (rollback / prune keep рядом с текущим)."""
    tags = parse_ok_tags(log_text)
    if len(tags) < 2:
        return None
    return tags[-2]
