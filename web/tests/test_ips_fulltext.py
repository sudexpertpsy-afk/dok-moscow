"""ИПС: fulltext=1 обязателен для полных текстов кодексов."""

from __future__ import annotations

from app.services.sources.ips_config import DOC_ITSELF_QUERY
from app.services.sources.ips_loader import build_doc_itself_url


def test_doc_itself_query_requests_fulltext():
    assert DOC_ITSELF_QUERY.get("fulltext") == "1"
    url = build_doc_itself_url("102041891")
    assert "fulltext=1" in url
    assert "nd=102041891" in url
