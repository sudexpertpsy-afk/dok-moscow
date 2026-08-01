"""Загрузка действующей редакции из ИПС (HTML → нормализованный HTML)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode

from app.services.sources import ips_config as cfg
from app.services.sources.http_client import ThrottledClient


class IpsLoaderError(Exception):
    pass


@dataclass
class IpsDocument:
    nd: str
    rdk: str | None
    title: str
    body_html: str
    source_url: str


class _ContentExtractor(HTMLParser):
    """Достаёт inner HTML первого подходящего контейнера."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._capture = False
        self._depth = 0
        self._chunks: list[str] = []
        self.title = ""
        self._in_title = False
        self.found = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {k.lower(): (v or "") for k, v in attrs}
        if tag.lower() == "title":
            self._in_title = True
        if not self.found and not self._capture:
            for sel in cfg.CONTENT_SELECTORS:
                key = sel["attr"]
                if key in ad and sel["value"] in ad[key].split():
                    self._capture = True
                    self._depth = 1
                    self.found = True
                    return
        if self._capture:
            self._depth += 1
            attr_s = "".join(
                f' {k}="{v}"' if v is not None else f" {k}" for k, v in attrs
            )
            self._chunks.append(f"<{tag}{attr_s}>")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False
        if not self._capture:
            return
        self._depth -= 1
        if self._depth <= 0:
            self._capture = False
            return
        self._chunks.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._capture:
            self._chunks.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._capture:
            self._chunks.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._capture:
            self._chunks.append(f"&#{name};")

    @property
    def content(self) -> str:
        return "".join(self._chunks)


def build_doc_itself_url(nd: str, rdk: str | None = None) -> str:
    q = dict(cfg.DOC_ITSELF_QUERY)
    q["nd"] = str(nd)
    if rdk is not None and str(rdk) != "":
        q["rdk"] = str(rdk)
    return f"{cfg.IPS_BASE}/?{urlencode(q)}"


def build_docbody_url(nd: str) -> str:
    q = dict(cfg.DOC_BODY_QUERY)
    q["nd"] = str(nd)
    return f"{cfg.IPS_BASE}/?{urlencode(q)}"


def normalize_ips_html(raw_html: str) -> str:
    """Убрать скрипты/стили, схлопнуть пустые строки, оставить смысловую разметку."""
    text = raw_html or ""
    for tag in cfg.STRIP_TAGS:
        text = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}>",
            "",
            text,
            flags=re.I | re.S,
        )
        text = re.sub(rf"<{tag}\b[^>]*/?>", "", text, flags=re.I)
    # комментарии условные IE и обычные
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_paragraphs(html: str) -> list[str]:
    """Грубое разбиение на абзацы для diff."""
    plain = re.sub(r"<(br|/p|/div|/tr|/h\d)[^>]*>", "\n", html or "", flags=re.I)
    plain = re.sub(r"<[^>]+>", "", plain)
    plain = re.sub(r"&nbsp;", " ", plain, flags=re.I)
    plain = re.sub(r"&quot;", '"', plain, flags=re.I)
    plain = re.sub(r"&amp;", "&", plain, flags=re.I)
    plain = re.sub(r"&lt;", "<", plain, flags=re.I)
    plain = re.sub(r"&gt;", ">", plain, flags=re.I)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in plain.splitlines()]
    return [ln for ln in lines if ln]


def load_ips_document(
    nd: str,
    *,
    rdk: str | None = None,
    http: ThrottledClient | None = None,
) -> IpsDocument:
    """Скачать и нормализовать текст редакции по nd."""
    if not nd or not re.fullmatch(r"\d{5,}", str(nd)):
        raise IpsLoaderError("Некорректный ips_nd")
    owns = http is None
    client = http or ThrottledClient()
    try:
        url = build_doc_itself_url(str(nd), rdk)
        response = client.get(url, use_cache=True)
        if response.status_code != 200:
            raise IpsLoaderError(f"ИПС HTTP {response.status_code}")
        raw = response.content.decode(cfg.RESPONSE_ENCODING, errors="replace")
        extractor = _ContentExtractor()
        extractor.feed(raw)
        extractor.close()
        body = normalize_ips_html(extractor.content)
        if len(body) < 20:
            raise IpsLoaderError("Пустой или слишком короткий текст ИПС")
        title = (extractor.title or "").strip()
        return IpsDocument(
            nd=str(nd),
            rdk=rdk,
            title=title,
            body_html=body,
            source_url=url,
        )
    finally:
        if owns:
            client.close()


def latest_rdk_from_docbody(
    nd: str,
    *,
    http: ThrottledClient | None = None,
) -> int | None:
    """Максимальный rdk со страницы карточки документа."""
    owns = http is None
    client = http or ThrottledClient()
    try:
        url = build_docbody_url(str(nd))
        response = client.get(url, use_cache=True)
        response.raise_for_status()
        raw = response.content.decode(cfg.RESPONSE_ENCODING, errors="replace")
        nums = [int(x) for x in re.findall(r"rdk=(\d+)", raw)]
        return max(nums) if nums else None
    finally:
        if owns:
            client.close()
