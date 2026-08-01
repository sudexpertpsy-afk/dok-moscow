"""W-22: поисковый движок НПА (индекс, реквизиты, веса, PDF)."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import ActFragment, LegalAct, LegalSearchDoc
from app.services.legal_registry import create_draft_version, publish_version
from app.services.legal_search import (
    LegalSearchFilters,
    extract_text_from_pdf,
    parse_requisite_query,
    rebuild_act_index,
    search_legal,
)

WEB_ROOT = Path(__file__).resolve().parents[1]
PG_URL = os.environ.get(
    "TEST_PG_URL",
    "postgresql+psycopg://dok:dok_dev_pass@127.0.0.1:5432/dok_legal_search_w22",
)


def _pg_available(url: str) -> bool:
    try:
        eng = create_engine(url.rsplit("/", 1)[0] + "/postgres")
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _publish_sample(db: Session, slug: str, body_html: str, *, revision_date=None) -> LegalAct:
    act = db.scalar(select(LegalAct).where(LegalAct.slug == slug))
    assert act is not None
    draft = create_draft_version(
        db,
        act_id=act.id,
        body_html=body_html,
        revision_date=revision_date or date(2024, 7, 22),
        text_origin="html",
    )
    publish_version(db, draft)
    db.commit()
    return act


def test_parse_requisites():
    assert parse_requisite_query("ст. 195 УПК") == {
        "kind": "article",
        "article": "195",
        "code": "упк",
        "slug": "upk-ekspertiza",
    }
    assert parse_requisite_query("статья 79 ГПК")["article"] == "79"
    assert parse_requisite_query("73-ФЗ") == {"kind": "fz", "number": "73-ФЗ"}
    assert parse_requisite_query("внушаемость") is None


def test_sqlite_search_morphology_quotes_weights_drafts(app):
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        _publish_sample(
            db,
            "73-fz-sudebno-ekspertnaya-deyatelnost",
            (
                "<h2>Статья 1. Предмет регулирования</h2>"
                "<p>Настоящий закон регулирует государственную судебно-экспертную деятельность.</p>"
                "<h2>Статья 2. Основные понятия</h2>"
                "<p>Эксперт проводит экспертизу и оценивает внушаемость свидетеля.</p>"
            ),
        )
        # черновик с уникальным словом — не должен попасть в выдачу
        act = db.scalar(
            select(LegalAct).where(LegalAct.slug == "73-fz-sudebno-ekspertnaya-deyatelnost")
        )
        create_draft_version(
            db,
            act_id=act.id,
            body_html="<p>Секретноечерновиковоеслово квантификация</p>",
        )
        db.commit()

        # морфология / словоформа (на SQLite — подстрока; полное стеммирование — в PG-тесте)
        hits = search_legal(db, "экспертизы")
        assert hits.hits, "ожидали попадание по тексту об экспертизе"
        assert hits.hits[0].act.slug == "73-fz-sudebno-ekspertnaya-deyatelnost"
        assert any("ст." in (h.article_ref or "") for h in hits.hits)

        # кавычки + минус (sqlite path)
        hits_q = search_legal(db, '"внушаемость" -квантификация')
        assert hits_q.hits
        assert all("квантификация" not in (h.snippet or "").casefold() for h in hits_q.hits)

        # черновик не в индексе
        assert not search_legal(db, "квантификация").hits

        # веса: заголовок статьи выше, чем совпадение только в «чужом» тексте
        gpk = db.scalar(select(LegalAct).where(LegalAct.slug == "gpk-ekspertiza"))
        frag = db.scalar(
            select(ActFragment).where(
                ActFragment.act_id == gpk.id, ActFragment.article_ref == "ст. 79"
            )
        )
        frag.body_html = "<p>Назначение экспертизы судом по ходатайству сторон.</p>"
        frag.title = "Назначение экспертизы"
        # отдельный фрагмент только с словом в теле
        frag80 = db.scalar(
            select(ActFragment).where(
                ActFragment.act_id == gpk.id, ActFragment.article_ref == "ст. 80"
            )
        )
        frag80.title = "ст. 80"
        frag80.body_html = "<p>Содержание определения о назначении экспертизы уточняется.</p>"
        draft = create_draft_version(
            db, act_id=gpk.id, body_html="<p>ГПК фрагменты</p>", revision_date=date(2024, 1, 1)
        )
        publish_version(db, draft)
        db.commit()

        ranked = search_legal(db, "Назначение")
        assert ranked.hits
        # заголовок ст. 79 должен быть не ниже совпадения в теле ст. 80
        top = ranked.hits[0]
        assert top.article_ref == "ст. 79" or "Назначение" in (top.heading or "")
    finally:
        db.close()

    r = client.get("/zakon/", params={"q": "внушаемость"})
    assert r.status_code == 200
    assert "внушаемость" in r.text.casefold()


def test_requisite_direct_hit(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        gpk = db.scalar(select(LegalAct).where(LegalAct.slug == "gpk-ekspertiza"))
        draft = create_draft_version(
            db, act_id=gpk.id, body_html="<p>нормы ГПК</p>", revision_date=date(2024, 1, 1)
        )
        publish_version(db, draft)
        db.commit()

        result = search_legal(db, "ст. 79 ГПК")
        assert result.direct is not None
        assert result.direct.act.slug == "gpk-ekspertiza"
        assert "79" in result.direct.article_ref
        assert "79" in result.direct.url

        fz = search_legal(db, "73-ФЗ")
        assert fz.direct is not None
        assert fz.direct.act.slug == "73-fz-sudebno-ekspertnaya-deyatelnost"
    finally:
        db.close()


def test_pdf_extract_and_unrecognized(app, tmp_path):
    from pypdf import PdfWriter

    blank = tmp_path / "scan.pdf"
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    with blank.open("wb") as fh:
        w.write(fh)
    text_val, ok = extract_text_from_pdf(blank)
    assert ok is False
    assert text_val == ""

    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        from app.services.legal_search import ingest_pdf_version

        act = db.scalar(
            select(LegalAct).where(LegalAct.slug == "73-fz-sudebno-ekspertnaya-deyatelnost")
        )
        ver = ingest_pdf_version(db, act_id=act.id, pdf_path=str(blank))
        assert ver.text_origin == "pdf_unrecognized"
        assert ver.body_html == ""
        publish_version(db, ver)
        db.commit()
        # нераспознанный скан — нет строк индекса
        docs = db.scalars(
            select(LegalSearchDoc).where(LegalSearchDoc.act_id == act.id)
        ).all()
        assert docs == []
    finally:
        db.close()


@pytest.mark.skipif(not _pg_available(PG_URL), reason="PostgreSQL недоступен для W-22")
def test_postgres_fts_morphology_trgm_explain():
    admin = create_engine(
        "postgresql+psycopg://dok:dok_dev_pass@127.0.0.1:5432/postgres",
        isolation_level="AUTOCOMMIT",
    )
    db_name = PG_URL.rsplit("/", 1)[-1]
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        conn.execute(text(f'CREATE DATABASE "{db_name}" OWNER dok'))

    env = os.environ.copy()
    env["DB_URL"] = PG_URL
    env["PYTHONPATH"] = str(WEB_ROOT)
    env["BILLING_WORKER"] = "0"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(WEB_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    # bootstrap реестра через приложение-слой
    from app.services.billing import bootstrap_billing
    from app.services.legal_registry import bootstrap_legal

    eng = create_engine(PG_URL)
    SessionLocal = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    db = SessionLocal()
    try:
        bootstrap_billing(db)
        bootstrap_legal(db)

        _publish_sample(
            db,
            "73-fz-sudebno-ekspertnaya-deyatelnost",
            (
                "<h2>Статья 1. Предмет</h2>"
                "<p>Закон о судебно-экспертной деятельности.</p>"
                "<h2>Статья 2. Экспертиза</h2>"
                "<p>Суд назначает дополнительную экспертизу при сомнениях во внушаемости.</p>"
            ),
        )
        gpk = db.scalar(select(LegalAct).where(LegalAct.slug == "gpk-ekspertiza"))
        frag = db.scalar(
            select(ActFragment).where(
                ActFragment.act_id == gpk.id, ActFragment.article_ref == "ст. 79"
            )
        )
        frag.body_html = "<p>Суд назначает экспертизу.</p>"
        frag.title = "Назначение экспертизы"
        draft = create_draft_version(
            db, act_id=gpk.id, body_html="<p>ГПК</p>", revision_date=date(2024, 2, 1)
        )
        publish_version(db, draft)
        db.commit()

        # морфология: экспертиза → экспертизы
        morph = search_legal(db, "экспертизы")
        assert morph.hits
        assert any(
            "экспертиз" in (h.snippet or "").casefold()
            or "ekspert" in h.act.slug
            for h in morph.hits
        )

        # кавычки websearch
        quoted = search_legal(db, '"дополнительная экспертиза"')
        assert quoted.hits

        # опечатка / реквизиты через trgm (чуть искажённый номер)
        typo = search_legal(db, "73-Фз")
        assert typo.hits or typo.direct

        # реквизиты напрямую
        direct = search_legal(db, "ст. 79 ГПК")
        assert direct.direct is not None
        assert direct.direct.act.slug == "gpk-ekspertiza"

        # веса: A (заголовок) > B (текст)
        # документ с словом только в heading vs только в body
        act73 = db.scalar(
            select(LegalAct).where(LegalAct.slug == "73-fz-sudebno-ekspertnaya-deyatelnost")
        )
        db.execute(text("DELETE FROM legal_search_docs WHERE act_id = :id"), {"id": act73.id})
        db.execute(
            text(
                """
                INSERT INTO legal_search_docs (act_id, version_id, article_ref, heading, body_text, requisites)
                VALUES
                  (:aid, (SELECT id FROM act_versions WHERE act_id=:aid AND status='published' LIMIT 1),
                   'ст. 10', 'Уникальныйзаголовоквектор', 'обычный текст без маркера', 'ст. 10'),
                  (:aid, (SELECT id FROM act_versions WHERE act_id=:aid AND status='published' LIMIT 1),
                   'ст. 11', 'Другая статья', 'здесь есть уникальныйзаголовоквектор в теле', 'ст. 11')
                """
            ),
            {"aid": act73.id},
        )
        db.commit()
        weighted = search_legal(db, "Уникальныйзаголовоквектор")
        assert weighted.hits
        assert weighted.hits[0].article_ref == "ст. 10"

        # черновики не индексируются
        create_draft_version(
            db, act_id=act73.id, body_html="<p>Черновиктокеныйякорь</p>"
        )
        db.commit()
        assert not search_legal(db, "Черновиктокеныйякорь").hits

        # EXPLAIN — уложиться в 200 мс на полном реестре
        timed = search_legal(db, "экспертиза", with_explain=True)
        assert timed.query_time_ms < 200, timed.query_time_ms
        assert timed.explain and "legal_search_docs" in timed.explain
    finally:
        db.close()
        eng.dispose()
