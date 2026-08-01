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
    old_p = html_to_paragraphs(old_html)
    new_p = html_to_paragraphs(new_html)
    parts: list[str] = ['<div class="diff-preview">']
    for line in difflib.ndiff(old_p, new_p):
        if line.startswith("- "):
            parts.append(f'<p class="diff-del">{_escape(line[2:])}</p>')
        elif line.startswith("+ "):
            parts.append(f'<p class="diff-ins">{_escape(line[2:])}</p>')
        elif line.startswith("? "):
            continue
        else:
            parts.append(f"<p>{_escape(line[2:])}</p>")
    parts.append("</div>")
    return "\n".join(parts)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
