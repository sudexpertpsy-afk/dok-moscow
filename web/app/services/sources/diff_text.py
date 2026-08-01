"""Сравнение редакций по абзацам (difflib.ndiff)."""

from __future__ import annotations

import difflib

from app.services.sources.ips_loader import html_to_paragraphs


def paragraph_diff(old_html: str, new_html: str) -> str:
    """Текстовый diff: строки с - / + / ? как у ndiff."""
    old_p = html_to_paragraphs(old_html)
    new_p = html_to_paragraphs(new_html)
    return "\n".join(difflib.ndiff(old_p, new_p))


def html_diff_preview(old_html: str, new_html: str) -> str:
    """Простой HTML-просмотр: удалено — красным, добавлено — зелёным."""
    return ndiff_to_html(paragraph_diff(old_html, new_html))


def ndiff_to_html(diff_text: str | None) -> str:
    """Преобразовать сохранённый ndiff в HTML для админки (W-19)."""
    if not (diff_text or "").strip():
        return '<div class="diff-preview"><p class="muted">Нет diff</p></div>'
    parts: list[str] = ['<div class="diff-preview">']
    for line in diff_text.splitlines():
        if line.startswith("- "):
            parts.append(f'<p class="diff-del">{_escape(line[2:])}</p>')
        elif line.startswith("+ "):
            parts.append(f'<p class="diff-ins">{_escape(line[2:])}</p>')
        elif line.startswith("? "):
            continue
        elif line.startswith("  "):
            parts.append(f"<p>{_escape(line[2:])}</p>")
        else:
            parts.append(f"<p>{_escape(line)}</p>")
    parts.append("</div>")
    return "\n".join(parts)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
