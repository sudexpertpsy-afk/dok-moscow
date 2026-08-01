"""Селекторы и параметры загрузки ИПС «Законодательство России».

При смене вёрстки ИПС правки — только здесь.
"""

from __future__ import annotations

import os

IPS_BASE = os.environ.get("PRAVO_IPS_BASE", "http://pravo.gov.ru/proxy/ips").rstrip("/")

# Страница текста редакции
DOC_ITSELF_PATH = "/"
DOC_ITSELF_QUERY = {
    "doc_itself": "",
    "page": "1",
    "link_id": "0",
}

# Карточка / список редакций
DOC_BODY_QUERY = {
    "docbody": "",
}

# CSS/HTML-маркеры контента (в порядке предпочтения)
CONTENT_SELECTORS = (
    {"attr": "class", "value": "doc_content"},
    {"attr": "id", "value": "xdoc_content"},
)

# Кодировка ответов классической ИПС
RESPONSE_ENCODING = "cp1251"

# Теги, удаляемые при нормализации
STRIP_TAGS = (
    "script",
    "style",
    "noscript",
    "iframe",
    "link",
    "meta",
    "svg",
)
