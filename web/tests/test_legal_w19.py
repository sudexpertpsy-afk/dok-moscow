"""W-19: админка НПА — diff, публикация, отклонение, ручная загрузка."""

from __future__ import annotations

from sqlalchemy import select

from app.models import (
    ActVersion,
    ActVersionStatus,
    Event,
    LegalAct,
    LegalActStatus,
)
from app.services.legal_admin import ActHealth, act_health, list_acts_admin
from app.services.legal_public import get_act_by_slug, published_for
from app.services.legal_registry import create_draft_version, ensure_legal_registry, publish_version
from app.services.sources.diff_text import ndiff_to_html
from conftest import csrf_from, login


def _seed_act(dbmod) -> tuple[int, str]:
    db = dbmod.SessionLocal()
    try:
        ensure_legal_registry(db)
        db.commit()
        act = db.scalar(
            select(LegalAct).where(LegalAct.slug == "73-fz-sudebno-ekspertnaya-deyatelnost")
        )
        assert act is not None
        return act.id, act.slug
    finally:
        db.close()


def test_ndiff_to_html_colors():
    html = ndiff_to_html("- старый\n+ новый\n  общий")
    assert "diff-del" in html and "старый" in html
    assert "diff-ins" in html and "новый" in html


def test_draft_not_on_public_until_publish(app):
    client, dbmod = app
    act_id, slug = _seed_act(dbmod)
    db = dbmod.SessionLocal()
    try:
        pub = create_draft_version(
            db, act_id=act_id, body_html="<p>Опубликовано</p>", change_basis="seed"
        )
        publish_version(db, pub)
        draft = create_draft_version(
            db,
            act_id=act_id,
            body_html="<p>Секретный черновик XYZ123</p>",
            change_basis="тест",
            diff_text="- Опубликовано\n+ Секретный черновик XYZ123",
        )
        db.commit()
        draft_id = draft.id
    finally:
        db.close()

    r = client.get(f"/zakon/{slug}")
    assert r.status_code == 200
    assert "Секретный черновик XYZ123" not in r.text

    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    r = client.get("/admin/legal/")
    assert r.status_code == 200
    assert "Ждёт подтверждения" in r.text

    r = client.get(f"/admin/legal/{act_id}")
    assert r.status_code == 200
    assert "diff-ins" in r.text or "Секретный черновик" in r.text

    token = csrf_from(client, f"/admin/legal/{act_id}")
    r = client.post(
        f"/admin/legal/{act_id}/publish/{draft_id}",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "published" in r.headers["location"]

    r = client.get(f"/zakon/{slug}")
    assert "Секретный черновик XYZ123" in r.text

    db = dbmod.SessionLocal()
    try:
        ev = db.scalar(
            select(Event).where(Event.type == "legal_version_published").order_by(Event.id.desc())
        )
        assert ev is not None
        assert ev.details["version_id"] == draft_id
    finally:
        db.close()


def test_reject_draft(app):
    client, dbmod = app
    act_id, _slug = _seed_act(dbmod)
    db = dbmod.SessionLocal()
    try:
        draft = create_draft_version(
            db, act_id=act_id, body_html="<p>reject-me</p>", change_basis="x"
        )
        db.commit()
        draft_id = draft.id
    finally:
        db.close()

    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    token = csrf_from(client, f"/admin/legal/{act_id}")
    r = client.post(
        f"/admin/legal/{act_id}/reject/{draft_id}",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db = dbmod.SessionLocal()
    try:
        v = db.get(ActVersion, draft_id)
        assert v is not None and v.status == ActVersionStatus.archived
        assert act_health(db, db.get(LegalAct, act_id)) != ActHealth.yellow
    finally:
        db.close()


def test_manual_upload_requires_source(app):
    client, dbmod = app
    act_id, _slug = _seed_act(dbmod)

    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    token = csrf_from(client, f"/admin/legal/{act_id}")
    r = client.post(
        f"/admin/legal/{act_id}/upload",
        data={"csrf_token": token, "body_html": "<p>x</p>", "source": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error" in r.headers["location"]

    r = client.post(
        f"/admin/legal/{act_id}/upload",
        data={
            "csrf_token": csrf_from(client, f"/admin/legal/{act_id}"),
            "body_html": "<p>Ручной текст ORDER</p>",
            "source": "https://example.test/order.pdf",
            "revision_date": "2026-01-15",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "ok=draft" in r.headers["location"]
    db = dbmod.SessionLocal()
    try:
        d = db.scalar(
            select(ActVersion).where(
                ActVersion.act_id == act_id, ActVersion.status == ActVersionStatus.draft
            )
        )
        assert d is not None
        assert d.text_origin == "manual"
        assert "example.test" in (d.change_basis or "")
    finally:
        db.close()


def test_repealed_keeps_archive_and_settings(app):
    client, dbmod = app
    act_id, slug = _seed_act(dbmod)
    db = dbmod.SessionLocal()
    try:
        v = create_draft_version(db, act_id=act_id, body_html="<p>v1</p>")
        publish_version(db, v)
        db.commit()
    finally:
        db.close()

    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    token = csrf_from(client, f"/admin/legal/{act_id}")
    r = client.post(
        f"/admin/legal/{act_id}/settings",
        data={
            "csrf_token": token,
            "mode": "full_text",
            "status": "repealed",
            "watch_enabled": "",
            "tracked_articles": "",
            "notes": "утратил силу тестом",
            "source_url": "https://pravo.gov.ru/ips/",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    db = dbmod.SessionLocal()
    try:
        act = db.get(LegalAct, act_id)
        assert act.status == LegalActStatus.repealed
        assert published_for(get_act_by_slug(db, slug)) is not None
    finally:
        db.close()


def test_list_health_yellow_when_draft(app):
    _, dbmod = app
    act_id, _slug = _seed_act(dbmod)
    db = dbmod.SessionLocal()
    try:
        create_draft_version(db, act_id=act_id, body_html="<p>d</p>")
        db.commit()
        rows = list_acts_admin(db)
        match = next(r for r in rows if r.act.id == act_id)
        assert match.health == ActHealth.yellow
    finally:
        db.close()


def test_admin_legal_nav(app):
    client, _ = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    r = client.get("/admin/")
    assert "/admin/legal/" in r.text
    assert "Законодательство" in r.text
