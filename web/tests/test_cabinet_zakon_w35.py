"""W-35: законодательство в кабинете — паритет, изоляция, upsell, письма."""

from __future__ import annotations

import re
from datetime import date, timedelta
from types import SimpleNamespace

from sqlalchemy import select

from app.defaults import empty_requisites
from app.models import (
    ActFragment,
    ActVersionStatus,
    LawBookmark,
    LawNote,
    LawWatch,
    LegalAct,
    Organization,
    Subscription,
    SubscriptionPeriod,
    SubscriptionStatus,
    Tariff,
    TariffCode,
    User,
    UserRole,
    utcnow,
)
from app.security import hash_password
from app.services.cabinet_zakon import (
    flush_watch_digests,
    notify_watchers_after_publish,
    quote_for_fragment,
)
from app.services.legal_admin import do_publish
from app.services.legal_bootstrap import fill_fragments_from_html
from app.services.legal_registry import create_draft_version, ensure_legal_registry
from app.models import ActVersionStatus
from conftest import login


def _make_user(dbmod, email: str, *, tariff: TariffCode = TariffCode.guest):
    from app.services.billing import ensure_tariffs

    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        org = Organization(name=f"Org {email}", requisites=empty_requisites())
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id,
            email=email,
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
            is_active=True,
        )
        db.add(user)
        db.flush()
        if tariff != TariffCode.guest:
            t = db.scalar(select(Tariff).where(Tariff.code == tariff))
            assert t is not None
            db.add(
                Subscription(
                    org_id=org.id,
                    tariff_id=t.id,
                    period=SubscriptionPeriod.month,
                    starts_at=utcnow() - timedelta(days=1),
                    ends_at=utcnow() + timedelta(days=30),
                    status=SubscriptionStatus.active,
                    auto_renew=False,
                    is_beta=False,
                )
            )
        db.commit()
        return org.id, user.id, email
    finally:
        db.close()


def _publish_sample_act(dbmod, slug: str = "gpk-ekspertiza"):
    db = dbmod.SessionLocal()
    try:
        ensure_legal_registry(db)
        act = db.scalar(select(LegalAct).where(LegalAct.slug == slug))
        assert act is not None
        # очистить старый published
        for v in list(act.versions or []):
            if v.status == ActVersionStatus.published:
                v.status = ActVersionStatus.archived
        body = (
            "<p>Статья 79. Назначение экспертизы</p>"
            "<p>Суд назначает экспертизу при возникновении вопросов.</p>"
            "<p>Статья 80. Содержание</p>"
            "<p>В определении указываются.</p>"
        )
        draft = create_draft_version(
            db,
            act_id=act.id,
            body_html=body,
            change_basis="w35 fixture",
        )
        draft.revision_date = date(2026, 6, 1)
        fill_fragments_from_html(db, act, body)
        published = do_publish(db, draft.id, user_id=None)
        db.commit()
        return act.id, published.id
    finally:
        db.close()


def test_public_zakon_html_unchanged_smoke(app):
    """Публичный /zakon остаётся доступен и без кабинетных контролов."""
    client, dbmod = app
    _publish_sample_act(dbmod)
    r = client.get("/zakon/gpk-ekspertiza")
    assert r.status_code == 200
    assert "Суд назначает экспертизу" in r.text
    assert "/cabinet/zakon/" not in r.text or "Войти в кабинет" in r.text
    assert "Мои закладки" not in r.text
    assert "data-quote-act" not in r.text


def test_content_parity_cabinet_vs_public(app):
    client, dbmod = app
    _publish_sample_act(dbmod)
    _make_user(dbmod, "parity@example.com")
    pub = client.get("/zakon/gpk-ekspertiza")
    assert pub.status_code == 200
    assert login(client, "parity@example.com", "Passw0rd!").status_code == 303
    cab = client.get("/cabinet/zakon/gpk-ekspertiza")
    assert cab.status_code == 200
    # общий текст редакции
    assert "Суд назначает экспертизу" in pub.text
    assert "Суд назначает экспертизу" in cab.text
    assert "В определении указываются" in pub.text
    assert "В определении указываются" in cab.text
    # кабинетные функции есть только в кабинете
    assert "data-quote-act" in cab.text
    assert "☆" in cab.text or "★" in cab.text


def test_nav_zakon_points_to_cabinet(app):
    client, dbmod = app
    _make_user(dbmod, "navz@example.com")
    assert login(client, "navz@example.com", "Passw0rd!").status_code == 303
    r = client.get("/cabinet/")
    assert r.status_code == 200
    assert 'href="/cabinet/zakon/"' in r.text
    assert 'target="_blank"' not in r.text or "/cabinet/zakon/" in r.text
    # футер
    assert re.search(r'href="/cabinet/zakon/"[^>]*>Нормативная база', r.text)


def test_guest_bookmark_and_upsell(app):
    client, dbmod = app
    _publish_sample_act(dbmod)
    _make_user(dbmod, "guestz@example.com", tariff=TariffCode.guest)
    assert login(client, "guestz@example.com", "Passw0rd!").status_code == 303
    r = client.get("/cabinet/zakon/gpk-ekspertiza")
    assert r.status_code == 200
    assert "Слежение и экспорт — в тарифе" in r.text or "billing" in r.text
    # закладка доступна
    from app.security import get_csrf_token
    # csrf из cookie/session через форму — достанем токен из HTML
    m = re.search(r'name="csrf_token" value="([^"]+)"', r.text)
    assert m
    csrf = m.group(1)
    r2 = client.post(
        "/cabinet/zakon/gpk-ekspertiza/bookmark",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert r2.status_code == 303
    db = dbmod.SessionLocal()
    try:
        n = db.scalar(select(LawBookmark).where(LawBookmark.user_id.is_not(None)))
        assert n is not None
    finally:
        db.close()
    # watch → billing
    r3 = client.post(
        "/cabinet/zakon/gpk-ekspertiza/watch",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert r3.status_code == 303
    assert "/cabinet/billing/" in r3.headers.get("location", "")


def test_notes_isolated_between_users(app):
    client, dbmod = app
    _publish_sample_act(dbmod)
    _, uid_a, email_a = _make_user(dbmod, "note-a@example.com", tariff=TariffCode.specialist)
    _, uid_b, email_b = _make_user(dbmod, "note-b@example.com", tariff=TariffCode.specialist)
    db = dbmod.SessionLocal()
    try:
        frag = db.scalar(
            select(ActFragment).where(
                ActFragment.article_ref == "ст. 79",
                ActFragment.act_id == select(LegalAct.id)
                .where(LegalAct.slug == "gpk-ekspertiza")
                .scalar_subquery(),
            )
        )
        assert frag is not None
        frag_id = frag.id
        act_slug = "gpk-ekspertiza"
    finally:
        db.close()

    assert login(client, email_a, "Passw0rd!").status_code == 303
    r = client.get(f"/cabinet/zakon/{act_slug}")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)
    client.post(
        f"/cabinet/zakon/{act_slug}/note/{frag_id}",
        data={"csrf_token": csrf, "body": "Секретная заметка А"},
        follow_redirects=False,
    )
    client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)

    assert login(client, email_b, "Passw0rd!").status_code == 303
    r = client.get(f"/cabinet/zakon/{act_slug}")
    assert "Секретная заметка А" not in r.text
    db = dbmod.SessionLocal()
    try:
        notes = db.scalars(select(LawNote).where(LawNote.fragment_id == frag_id)).all()
        assert len(notes) == 1
        assert notes[0].user_id == uid_a
        assert notes[0].body == "Секретная заметка А"
    finally:
        db.close()


def test_watch_email_only_after_publish(app, monkeypatch):
    client, dbmod = app
    sent: list[dict] = []

    def fake_send(settings, *, to_addr, subject, body):
        sent.append({"to": to_addr, "subject": subject, "body": body})
        return True

    monkeypatch.setattr("app.services.cabinet_zakon.send_email", fake_send)
    monkeypatch.setattr("app.services.mail.send_email", fake_send)

    _make_user(dbmod, "watch@example.com", tariff=TariffCode.specialist)
    db = dbmod.SessionLocal()
    try:
        ensure_legal_registry(db)
        act = db.scalar(select(LegalAct).where(LegalAct.slug == "gpk-ekspertiza"))
        user = db.scalar(select(User).where(User.email == "watch@example.com"))
        db.add(LawWatch(user_id=user.id, act_id=act.id))
        # черновик — писем быть не должно
        draft = create_draft_version(
            db, act_id=act.id, body_html="<p>Черновик достаточно длинный для публикации теста W35.</p>"
        )
        draft.revision_date = date(2026, 7, 1)
        db.commit()
        draft_id = draft.id
        act_id = act.id
        assert not sent
        # публикация → письмо
        do_publish(db, draft_id, user_id=user.id)
        db.commit()
    finally:
        db.close()

    assert sent, "ожидалось письмо после публикации"
    assert "gpk" in sent[0]["body"].casefold() or "ГПК" in sent[0]["body"] or "кабинет" in sent[0]["body"].casefold()
    # повторный flush в тот же день не шлёт второе письмо по уже sent
    before = len(sent)
    db = dbmod.SessionLocal()
    try:
        flush_watch_digests(db)
        db.commit()
    finally:
        db.close()
    assert len(sent) == before


def test_quote_format():
    act = SimpleNamespace(number="ГПК РФ", title="ГПК")
    frag = SimpleNamespace(
        article_ref="ст. 79",
        body_html="<p>Суд назначает экспертизу.</p>",
    )
    ver = SimpleNamespace(revision_date=date(2026, 6, 1), body_html="")
    text = quote_for_fragment(act, frag, ver)
    assert "ст. 79 ГПК РФ (ред. от 01.06.2026)" in text
    assert "Официальный интернет-портал правовой информации" in text
    assert "Суд назначает экспертизу" in text


def test_catalog_and_search_routes(app):
    client, dbmod = app
    _make_user(dbmod, "cat@example.com")
    assert login(client, "cat@example.com", "Passw0rd!").status_code == 303
    r = client.get("/cabinet/zakon/")
    assert r.status_code == 200
    assert "Законодательство" in r.text
    r = client.get("/cabinet/zakon/search", params={"q": "экспертиза"}, follow_redirects=False)
    assert r.status_code == 303
    assert "/cabinet/zakon/" in r.headers.get("location", "")
