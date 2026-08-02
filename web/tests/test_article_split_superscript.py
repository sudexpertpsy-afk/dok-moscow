"""Разбиение статей: «87 1» (надстрочный индекс ИПС) → ст. 87.1."""

from __future__ import annotations

from sqlalchemy import select

from app.models import ActFragment, LegalAct, LegalActMode
from app.services.legal_bootstrap import fill_fragments_from_html
from app.services.legal_registry import ensure_legal_registry
from app.services.legal_search import split_html_articles


def test_split_superscript_article_num():
    html = """
    <p>Статья 87. Дополнительная экспертиза</p>
    <p>Текст восемьдесят семь.</p>
    <p>Статья 87 1 . Консультация специалиста</p>
    <p>Специалист даёт консультацию по вопросам.</p>
    <p>Статья 88. Оценка доказательств</p>
    <p>Текст восемьдесят восемь.</p>
    """
    parts = split_html_articles(html)
    refs = {ref for ref, _, _ in parts if ref}
    assert "ст. 87" in refs
    assert "ст. 87.1" in refs
    assert "ст. 88" in refs
    body_871 = next(b for r, _, b in parts if r == "ст. 87.1")
    assert "консультацию" in body_871.casefold()
    # не склеивать 87.1 с телом 87
    body_87 = next(b for r, _, b in parts if r == "ст. 87")
    assert "консультацию" not in body_87.casefold()


def test_split_dot_and_middot_article_num():
    html = "<p>Статья 55.1. Особенность</p><p>Текст.</p><p>Статья 55·2 Текст</p><p>Ещё.</p>"
    parts = split_html_articles(html)
    refs = {ref for ref, _, _ in parts if ref}
    assert "ст. 55.1" in refs
    assert "ст. 55.2" in refs


def test_fill_apk_87_1_from_ips_style_html(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        ensure_legal_registry(db)
        act = db.scalar(select(LegalAct).where(LegalAct.slug == "apk-ekspertiza"))
        assert act and act.mode == LegalActMode.fragments
        frag = db.scalar(
            select(ActFragment).where(
                ActFragment.act_id == act.id, ActFragment.article_ref == "ст. 87.1"
            )
        )
        assert frag is not None
        frag.body_html = ""
        db.flush()
        html = """
        <p>Статья 87. Дополнительная и повторная экспертиза</p>
        <p>Арбитражный суд может назначить.</p>
        <p>Статья 87 1 . Консультация специалиста</p>
        <p>Специалист даёт консультацию в устной или письменной форме.</p>
        """
        n = fill_fragments_from_html(db, act, html)
        db.commit()
        assert n >= 1
        db.refresh(frag)
        assert frag.body_html
        assert "консультацию" in frag.body_html.casefold()
    finally:
        db.close()
