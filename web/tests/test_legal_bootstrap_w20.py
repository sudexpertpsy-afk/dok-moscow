"""Первичное наполнение НПА из ИПС / HTML (после W-19)."""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select

from app.models import ActFragment, ActVersion, ActVersionStatus, LegalAct, LegalActMode
from app.services.legal_bootstrap import (
    bootstrap_missing,
    fill_fragments_from_html,
    pull_ips_to_draft,
    pull_publication_pdf_to_draft,
)
from app.services.legal_registry import ensure_legal_registry, publish_version
from app.services.sources.ips_loader import normalize_ips_html
from conftest import csrf_from, login

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "legal" / "ips_73fz_doc_itself.html"


def _body_73() -> str:
    raw = FIXTURE.read_text(encoding="utf-8", errors="replace")
    # как в loader: после normalize
    from app.services.sources.ips_loader import _ContentExtractor

    ex = _ContentExtractor()
    ex.feed(raw)
    ex.close()
    return normalize_ips_html(ex.content)


def test_pull_html_creates_draft_not_public(app):
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        ensure_legal_registry(db)
        act = db.scalar(
            select(LegalAct).where(LegalAct.slug == "73-fz-sudebno-ekspertnaya-deyatelnost")
        )
        assert act is not None
        body = _body_73()
        assert len(body) > 100
        result = pull_ips_to_draft(
            db,
            act,
            body_html=body,
            source_url="fixture://ips_73fz",
            user_id=1,
        )
        db.commit()
        assert result.ok and result.draft_id
        draft = db.get(ActVersion, result.draft_id)
        assert draft.status == ActVersionStatus.draft
        assert draft.text_origin == "ips_bootstrap"
        slug = act.slug
    finally:
        db.close()

    r = client.get(f"/zakon/{slug}")
    assert r.status_code == 200
    # черновик не на сайте — ищем уникальный маркер только после publish
    assert "ips_bootstrap" not in r.text


def test_fill_fragments_from_html(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        ensure_legal_registry(db)
        act = db.scalar(select(LegalAct).where(LegalAct.slug == "gpk-ekspertiza"))
        assert act and act.mode == LegalActMode.fragments
        html = """
        <p>Преамбула кодекса.</p>
        <p>Статья 79. Назначение экспертизы</p>
        <p>Суд назначает экспертизу.</p>
        <p>Статья 80. Содержание</p>
        <p>В определении указываются.</p>
        <p>Статья 81. Другое</p>
        <p>Текст 81.</p>
        """
        n = fill_fragments_from_html(db, act, html)
        db.commit()
        assert n >= 2
        f79 = db.scalar(
            select(ActFragment).where(
                ActFragment.act_id == act.id, ActFragment.article_ref == "ст. 79"
            )
        )
        assert f79 is not None
        assert "назначает экспертизу" in f79.body_html.casefold() or "Суд" in f79.body_html
    finally:
        db.close()


def test_bootstrap_skips_without_source(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        ensure_legal_registry(db)
        db.commit()
        # без сети: акты с источником упадут/skip; без ips_nd и eo — skip
        report = bootstrap_missing(db, only_without_published=True, limit=100)
        assert report.results
        assert any(
            (r.skipped or "").startswith("нет ips_nd") for r in report.results
        )
    finally:
        db.close()


def test_pull_publication_pdf_to_draft(app, monkeypatch, tmp_path):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        ensure_legal_registry(db)
        act = db.scalar(select(LegalAct).where(LegalAct.slug == "minzdrav-3n-2017"))
        assert act and act.eo_number

        class FakePub:
            def __init__(self, *a, **k):
                pass

            def download_pdf(self, eo_number: str) -> bytes:
                assert eo_number == act.eo_number
                return b"%PDF-1.4 fake"

            def get_document(self, eo_number: str) -> dict:
                return {"name": "Тестовый приказ 3н", "complexName": ""}

            def pdf_url(self, eo_number: str) -> str:
                return f"http://publication.pravo.gov.ru/file/pdf?eoNumber={eo_number}"

        monkeypatch.setattr(
            "app.services.legal_bootstrap.PublicationClient", FakePub
        )
        monkeypatch.setattr(
            "app.services.legal_bootstrap.get_settings",
            lambda: type("S", (), {"files_root": str(tmp_path)})(),
        )
        result = pull_publication_pdf_to_draft(db, act, user_id=1)
        db.commit()
        assert result.ok and result.draft_id
        draft = db.get(ActVersion, result.draft_id)
        assert draft.status == ActVersionStatus.draft
        assert draft.pdf_path and Path(draft.pdf_path).is_file()
        assert "eoNumber=" in (draft.change_basis or "")
        assert draft.text_origin == "pdf_unrecognized"
        assert "скан без текстового слоя" in (draft.body_html or "").casefold()
        assert len(re.sub(r"<[^>]+>", " ", draft.body_html or "")) >= 40
    finally:
        db.close()


def test_admin_pull_ips_with_injected_body_via_manual_path(app):
    """UI pull-ips ходит в сеть; здесь проверяем кнопку bootstrap CSRF и список."""
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        ensure_legal_registry(db)
        db.commit()
    finally:
        db.close()
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    r = client.get("/admin/legal/")
    assert r.status_code == 200
    assert "Наполнить из ИПС" in r.text
    token = csrf_from(client, "/admin/legal/")
    # batch без сети: ок=bootstrap, ошибки IPS допустимы
    r = client.post(
        "/admin/legal/bootstrap",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "ok=bootstrap" in r.headers["location"]


def test_publish_after_bootstrap_html(app):
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        ensure_legal_registry(db)
        act = db.scalar(
            select(LegalAct).where(LegalAct.slug == "73-fz-sudebno-ekspertnaya-deyatelnost")
        )
        result = pull_ips_to_draft(db, act, body_html=_body_73(), source_url="fixture")
        assert result.ok
        publish_version(db, db.get(ActVersion, result.draft_id), reviewed_by_user_id=1)
        db.commit()
        slug = act.slug
        marker = "судебно-экспертн"
    finally:
        db.close()
    r = client.get(f"/zakon/{slug}")
    assert r.status_code == 200
    assert marker in r.text.casefold()
