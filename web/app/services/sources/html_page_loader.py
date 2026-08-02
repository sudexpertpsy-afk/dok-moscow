"""Загрузка HTML-страницы НПА → упрощённый body_html (seed_url bootstrap)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser

from app.services.sources.http_client import ThrottledClient


class HtmlPageLoaderError(RuntimeError):
    pass


@dataclass
class HtmlPageDocument:
    body_html: str
    source_url: str
    title: str | None = None


class _TextExtractor(HTMLParser):
    """Собирает видимый текст, отбрасывая script/style/nav-шум."""

    SKIP = frozenset({"script", "style", "noscript", "svg", "nav", "footer", "header"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        t = tag.lower()
        if t in self.SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if t == "title":
            self._in_title = True
        if t in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if t == "title":
            self._in_title = False
        if t in {"p", "div", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        if data.strip():
            self.parts.append(data)


_NOISE_LINE = re.compile(
    r"(?i)^(cookie|мы используем|принять|политик[аи].*cookie|"
    r"подписаться|реклама|войти|регистрация|"
    r"консультантплюс|гарант|см\. текст в предыдущей редакции|"
    r"список изменяющих документов)\b"
)


def html_to_body(raw_html: str) -> tuple[str, str | None]:
    """Сырой HTML → (body_html из <p>, title)."""
    parser = _TextExtractor()
    try:
        parser.feed(raw_html or "")
        parser.close()
    except Exception as exc:
        raise HtmlPageLoaderError(f"Не удалось разобрать HTML: {exc}") from exc
    title = unescape("".join(parser.title_parts)).strip() or None
    text = unescape("".join(parser.parts))
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    paras: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        line = " ".join(block.split()).strip()
        if len(line) < 40 and _NOISE_LINE.search(line):
            continue
        if len(line) < 2:
            continue
        # отсечь явный UI-мусор коротких строк
        if len(line) < 12 and line.casefold() in {"принять", "войти", "меню", "поиск"}:
            continue
        paras.append(line)
    if len(paras) < 5:
        raise HtmlPageLoaderError("Слишком мало текста на странице")
    # отрезать хвост с cookie/подписками
    cut = len(paras)
    for i, p in enumerate(paras):
        if "cookie" in p.casefold() and i > len(paras) // 2:
            cut = i
            break
    paras = paras[:cut]
    body = "".join(f"<p>{_escape(p)}</p>" for p in paras)
    return body, title


def _escape(value: str) -> str:
    return (
        (value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def load_html_page(url: str, *, http: ThrottledClient | None = None) -> HtmlPageDocument:
    owns = http is None
    client = http or ThrottledClient()
    try:
        r = client.get(url, use_cache=False)
        r.raise_for_status()
        ctype = (r.headers.get("content-type") or "").casefold()
        if "html" not in ctype and not (r.text or "").lstrip().lower().startswith("<!doctype"):
            # иногда отдают text/html без заголовка
            if "<html" not in (r.text or "").casefold()[:500]:
                raise HtmlPageLoaderError(f"Ответ не HTML ({ctype})")
        body, title = html_to_body(r.text)
        return HtmlPageDocument(body_html=body, source_url=url, title=title)
    finally:
        if owns:
            client.close()
