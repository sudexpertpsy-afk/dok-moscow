"""Bootstrap НПА из seed_url / link_card."""

from __future__ import annotations

from sqlalchemy import select

from app.models import ActVersion, ActVersionStatus, LegalAct
from app.services.legal_bootstrap import (
    pull_link_card_to_draft,
    pull_seed_url_to_draft,
)
from app.services.legal_registry import ensure_legal_registry
from app.services.sources.html_page_loader import html_to_body


def test_html_to_body_extracts_paragraphs():
    raw = """
    <html><head><title>Тест</title>
    <script>var x=1</script></head>
    <body>
    <nav>Меню</nav>
    <p>Первый абзац достаточно длинный для сохранения в теле документа.</p>
    <p>Второй абзац также содержит осмысленный текст постановления пленума.</p>
    <p>Третий абзац про судебную экспертизу и специальные знания эксперта.</p>
    <p>Четвёртый абзац закрывает минимальный порог длины извлечения текста.</p>
    <p>Пятый абзац нужен, чтобы парсер не отверг страницу как пустую.</p>
    <p>cookie политика сайта</p>
    </body></html>
    """
    body, title = html_to_body(raw)
    assert title == "Тест"
    assert "Первый абзац" in body
    assert "script" not in body.casefold()
    assert "cookie" not in body.casefold()


def test_pull_seed_url_with_injected_http(app, monkeypatch):
    _, dbmod = app
    html = """
    <html><head><title>Пленум</title></head><body>
    <p>В связи с вопросами судебной экспертизы по уголовным делам пленум постановляет.</p>
    <p>Судебная экспертиза производится государственными судебными экспертами и иными лицами.</p>
    <p>Перед экспертом не могут быть поставлены правовые вопросы оценки деяния судом.</p>
    <p>Заключение эксперта оценивается судом по правилам уголовно-процессуального кодекса.</p>
    <p>Дополнительная экспертиза назначается при недостаточной ясности выводов эксперта.</p>
    </body></html>
    """

    class FakeResp:
        status_code = 200
        headers = {"content-type": "text/html"}
        text = html

        def raise_for_status(self):
            return None

    class FakeHttp:
        def get(self, url, *, use_cache=False):
            assert "rulaws" in url or "consultant" in url or url.startswith("http")
            return FakeResp()

        def close(self):
            return None

    db = dbmod.SessionLocal()
    try:
        ensure_legal_registry(db)
        act = db.scalar(select(LegalAct).where(LegalAct.slug == "plenum-vs-28-2010"))
        assert act is not None
        result = pull_seed_url_to_draft(
            db,
            act,
            http=FakeHttp(),  # type: ignore[arg-type]
            replace_draft=True,
            seed_url="https://example.test/plenum-28",
        )
        db.commit()
        assert result.ok and result.draft_id
        draft = db.get(ActVersion, result.draft_id)
        assert draft.status == ActVersionStatus.draft
        assert draft.text_origin == "seed_html"
        assert "судебной экспертизы" in (draft.body_html or "").casefold()
    finally:
        db.close()


def test_pull_link_card(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        ensure_legal_registry(db)
        act = db.scalar(select(LegalAct).where(LegalAct.slug == "mvd-511-2005"))
        result = pull_link_card_to_draft(db, act, replace_draft=True)
        db.commit()
        assert result.ok
        draft = db.get(ActVersion, result.draft_id)
        assert draft.text_origin == "link_card"
        assert "первоисточник" in (draft.body_html or "").casefold()
    finally:
        db.close()
