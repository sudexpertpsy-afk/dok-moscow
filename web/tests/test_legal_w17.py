"""W-17: загрузчики publication/IPS, троттлинг, мониторинг с diff."""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.models import ActVersion, ActVersionStatus, ActWatchLog, ActWatchResult, LegalAct
from app.services.legal_monitor import check_act, consecutive_source_errors, run_daily_watch
from app.services.legal_registry import create_draft_version, publish_version
from app.services.sources.diff_text import paragraph_diff
from app.services.sources.http_client import ThrottledClient
from app.services.sources.ips_loader import load_ips_document, normalize_ips_html
from app.services.sources.publication_api import PublicationClient

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "legal"


class _RouterTransport(httpx.BaseTransport):
    def __init__(self, routes: dict[str, httpx.Response], hits: list[str] | None = None):
        self.routes = routes
        self.hits = hits if hits is not None else []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.hits.append(url)
        for prefix, response in self.routes.items():
            if url.startswith(prefix) or prefix in url:
                return httpx.Response(
                    response.status_code,
                    content=response.content,
                    headers=response.headers,
                    request=request,
                )
        return httpx.Response(404, json={"message": f"no fixture for {url}"}, request=request)


def test_throttle_min_interval():
    transport = _RouterTransport(
        {"http://example.test/a": httpx.Response(200, content=b"ok")}
    )
    clock = {"t": 0.0}

    def fake_clock():
        return clock["t"]

    sleeps: list[float] = []

    real_sleep = time.sleep

    def fake_sleep(sec):
        sleeps.append(sec)
        clock["t"] += sec

    time.sleep = fake_sleep  # type: ignore[assignment]
    try:
        client = ThrottledClient(
            min_interval=1.0,
            cache_ttl=0,
            transport=transport,
            clock=fake_clock,
        )
        client.get("http://example.test/a")
        clock["t"] += 0.2
        client.get("http://example.test/a")
        assert client.request_count == 2
        client.close()
    finally:
        time.sleep = real_sleep  # type: ignore[assignment]
    assert sleeps and sleeps[0] == pytest.approx(0.8, abs=0.05)


def test_get_cache_avoids_second_network_call():
    hits: list[str] = []
    transport = _RouterTransport(
        {"http://example.test/cached": httpx.Response(200, content=b"v1")},
        hits=hits,
    )
    client = ThrottledClient(min_interval=0, cache_ttl=60, transport=transport)
    assert client.get("http://example.test/cached").content == b"v1"
    assert client.get("http://example.test/cached").content == b"v1"
    client.close()
    assert len(hits) == 1
    assert client.request_count == 1


def test_publication_search_from_fixture():
    payload = (FIXTURES / "documents_search_73fz.json").read_bytes()
    transport = _RouterTransport(
        {
            "http://publication.test/api/Documents": httpx.Response(
                200, content=payload, headers={"content-type": "application/json"}
            )
        }
    )
    http = ThrottledClient(min_interval=0, cache_ttl=0, transport=transport)
    client = PublicationClient(base_url="http://publication.test", http=http)
    docs = client.list_documents(name="государственной судебно-экспертной", page_size=10)
    assert docs
    assert any("191-ФЗ" in (d.number or "") or "судебно-экспертной" in d.name for d in docs)
    client.close()


def test_ips_normalize_and_load_fixture():
    tiny = (FIXTURES / "ips_tiny.html").read_text(encoding="utf-8")
    cleaned = normalize_ips_html(
        '<div class="doc_content"><script>x()</script><p>Статья 1</p></div>'
    )
    assert "script" not in cleaned.lower() or "<script" not in cleaned.lower()
    assert "Статья 1" in cleaned

    html = (FIXTURES / "ips_73fz_doc_itself.html").read_bytes()
    transport = _RouterTransport(
        {
            "http://ips.test/?doc_itself=": httpx.Response(
                200,
                content=html,
                headers={"content-type": "text/html; charset=windows-1251"},
            )
        }
    )
    http = ThrottledClient(min_interval=0, cache_ttl=0, transport=transport)
    import app.services.sources.ips_config as ips_cfg

    old = ips_cfg.IPS_BASE
    ips_cfg.IPS_BASE = "http://ips.test"
    try:
        doc = load_ips_document("102071320", rdk="11", http=http)
    finally:
        ips_cfg.IPS_BASE = old
        http.close()
    assert "судебно" in doc.title.casefold() or "Статья" in doc.body_html
    assert len(doc.body_html) > 200


def test_paragraph_diff_marks_changes():
    old = "<p>Статья 1. Было</p><p>Статья 2. Стабильно</p>"
    new = "<p>Статья 1. Стало</p><p>Статья 2. Стабильно</p>"
    diff = paragraph_diff(old, new)
    assert "- Статья 1. Было" in diff
    assert "+ Статья 1. Стало" in diff


def test_monitor_creates_draft_with_diff(app, monkeypatch):
    client_app, dbmod = app
    db = dbmod.SessionLocal()
    try:
        act = db.scalar(
            select(LegalAct).where(LegalAct.slug == "73-fz-sudebno-ekspertnaya-deyatelnost")
        )
        assert act is not None
        assert act.watch_enabled is True
        published = create_draft_version(
            db,
            act_id=act.id,
            body_html="<p>Статья 1. Старая редакция</p><p>Статья 2. Без изменений</p>",
            revision_date=date(2021, 7, 1),
        )
        publish_version(db, published)
        db.commit()

        search = json.loads((FIXTURES / "documents_search_73fz.json").read_text(encoding="utf-8"))
        # гарантируем «новый» eo
        search["items"][0]["eoNumber"] = "TESTEO0001"
        search["items"][0]["name"] = (
            'О внесении изменений в Федеральный закон «О государственной судебно-экспертной деятельности»'
        )
        search["items"][0]["complexName"] = search["items"][0]["name"]
        ips_html = (
            '<html><head><title>73-ФЗ тест</title></head><body>'
            '<div class="doc_content">'
            "<p>Статья 1. Новая редакция закона о судебно-экспертной деятельности</p>"
            "<p>Статья 2. Без изменений в этой части текста документа</p>"
            "</div></body></html>"
        ).encode("cp1251")

        transport = _RouterTransport(
            {
                "http://publication.test/api/Documents": httpx.Response(
                    200,
                    content=json.dumps(search).encode("utf-8"),
                    headers={"content-type": "application/json"},
                ),
                "http://ips.test/": httpx.Response(
                    200,
                    content=ips_html,
                    headers={"content-type": "text/html"},
                ),
            }
        )
        http = ThrottledClient(min_interval=0, cache_ttl=0, transport=transport)
        pub = PublicationClient(base_url="http://publication.test", http=http)
        import app.services.sources.ips_config as ips_cfg

        old = ips_cfg.IPS_BASE
        ips_cfg.IPS_BASE = "http://ips.test"
        mailed: list[str] = []

        def fake_mail(**kwargs):
            mailed.append(kwargs.get("act_slug") or "")
            return True

        monkeypatch.setattr(
            "app.services.legal_monitor.notify_legal_change", fake_mail
        )
        try:
            row = check_act(
                db,
                act,
                pub=pub,
                http=http,
                since=date(2020, 1, 1),
                send_mail=True,
            )
            db.commit()
        finally:
            ips_cfg.IPS_BASE = old
            pub.close()

        assert row.result == ActWatchResult.change_found
        draft = db.scalar(
            select(ActVersion).where(
                ActVersion.act_id == act.id,
                ActVersion.status == ActVersionStatus.draft,
            )
        )
        assert draft is not None
        assert draft.diff_text and "- Статья 1. Старая редакция" in draft.diff_text
        assert "+ Статья 1. Новая редакция" in draft.diff_text
        assert "TESTEO0001" in (draft.change_basis or "")
        assert mailed == [act.slug]
    finally:
        db.close()


def test_monitor_source_error_three_times_notifies(app, monkeypatch):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        act = db.scalar(select(LegalAct).where(LegalAct.slug == "gpk-ekspertiza"))
        assert act is not None
        transport = _RouterTransport({})  # always 404
        http = ThrottledClient(min_interval=0, cache_ttl=0, transport=transport)
        pub = PublicationClient(base_url="http://publication.test", http=http)
        errors_mail: list[int] = []

        def fake_err(**kwargs):
            errors_mail.append(kwargs.get("error_count") or 0)
            return True

        monkeypatch.setattr(
            "app.services.legal_monitor.notify_legal_source_errors", fake_err
        )
        for _ in range(3):
            check_act(db, act, pub=pub, http=http, send_mail=True)
        db.commit()
        assert consecutive_source_errors(db, act.id) >= 3
        assert errors_mail and errors_mail[-1] >= 3
        pub.close()
    finally:
        db.close()


def test_run_daily_watch_skips_disabled(app, monkeypatch):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        # глушим сеть полностью
        transport = _RouterTransport(
            {
                "http://publication.test/api/Documents": httpx.Response(
                    200,
                    json={
                        "items": [],
                        "itemsTotalCount": 0,
                        "itemsPerPage": 10,
                        "currentPage": 1,
                        "pagesTotalCount": 0,
                    },
                )
            }
        )
        http = ThrottledClient(min_interval=0, cache_ttl=0, transport=transport)
        pub = PublicationClient(base_url="http://publication.test", http=http)
        stats = run_daily_watch(db, pub=pub, http=http, send_mail=False)
        assert stats["skipped"] >= 1  # ГОСТы / пленумы
        assert "unchanged" in stats
        pub.close()
        # журнал не пуст для включённых
        n = db.scalar(select(ActWatchLog).limit(1))
        assert n is not None
    finally:
        db.close()
