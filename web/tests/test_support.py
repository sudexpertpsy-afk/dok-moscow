"""Поддержка и предложения улучшений: кабинет + админка."""

from __future__ import annotations

import io
import re
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.defaults import empty_requisites
from app.models import (
    Organization,
    SupportTicket,
    SupportTicketKind,
    SupportTicketStatus,
    User,
    UserRole,
)
from app.navigation import NAV_REGISTRY, match_registry, resolve_nav, roles_for_user
from app.security import hash_password
from conftest import csrf_from, login

PARTIAL = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "templates"
    / "partials"
    / "user_menu.html"
)


def _make_png(size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (40, 120, 80)).save(buf, format="PNG")
    return buf.getvalue()


def _org_user(dbmod, email: str, *, name: str = "ООО Тест"):
    db = dbmod.SessionLocal()
    try:
        org = Organization(name=name, requisites=empty_requisites())
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
        db.commit()
        return email, org.id, user.id
    finally:
        db.close()


def test_support_nav_registered():
    keys = {item.key for item in NAV_REGISTRY}
    assert "support" in keys
    assert "admin_support" in keys
    item = next(i for i in NAV_REGISTRY if i.key == "support")
    assert item.url == "/cabinet/support/"
    assert item.group == "Справка"
    admin = next(i for i in NAV_REGISTRY if i.key == "admin_support")
    assert admin.url == "/admin/support/"


def test_support_synonyms_cmdk():
    roles = roles_for_user(is_service_admin=False, has_org=True, is_org_admin=True)
    resolved = resolve_nav(roles=roles, tariff=None, area="search", menu_only=False)
    hits = match_registry("тикет", resolved)
    assert any(h.item.key == "support" for h in hits)


def test_user_menu_links_resolve(app):
    client, dbmod = app
    email, _oid, _uid = _org_user(dbmod, "menu-user@example.com")
    assert login(client, email, "Passw0rd!").status_code == 303

    text = PARTIAL.read_text(encoding="utf-8")
    cabinet_hrefs = re.findall(
        r'href="(/cabinet/[^"]+)"',
        text.split("{% else %}")[1].split("{% endif %}")[0],
    )
    assert "/cabinet/support/" in cabinet_hrefs
    assert "/cabinet/support/improve" in cabinet_hrefs
    for href in cabinet_hrefs:
        r = client.get(href)
        assert r.status_code in (200, 303), href

    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    admin_block = text.split("{% if area == 'admin' %}")[1].split("{% else %}")[0]
    admin_hrefs = re.findall(r'href="(/admin/[^"]+)"', admin_block)
    assert "/admin/support/" in admin_hrefs
    assert "/admin/security/" in admin_hrefs
    for href in admin_hrefs:
        r = client.get(href)
        assert r.status_code in (200, 303), href


def test_create_ticket_and_improve_no_id_conflict(app):
    client, dbmod = app
    email, oid, _uid = _org_user(dbmod, "sup1@example.com")
    assert login(client, email, "Passw0rd!").status_code == 303

    r = client.get("/cabinet/support/improve")
    assert r.status_code == 200
    assert "Предложить улучшение" in r.text

    token = csrf_from(client, "/cabinet/support/")
    with patch("app.services.support.send_email", return_value=True) as mail:
        r = client.post(
            "/cabinet/support/",
            data={
                "csrf_token": token,
                "kind": "support",
                "subject": "Не открывается PDF",
                "body": "После генерации кнопка PDF ничего не делает.",
                "page_url": "/cabinet/documents/1",
            },
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert "/cabinet/support/" in r.headers["location"]
    assert mail.called

    token = csrf_from(client, "/cabinet/support/improve")
    with patch("app.services.support.send_email", return_value=True):
        r = client.post(
            "/cabinet/support/",
            data={
                "csrf_token": token,
                "kind": "improvement",
                "subject": "Фильтр в журнале",
                "body": "Хочу фильтр по шаблону на главной.",
                "page_url": "/cabinet/journal",
            },
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert "improve" in r.headers["location"]

    db = dbmod.SessionLocal()
    try:
        rows = db.query(SupportTicket).filter(SupportTicket.org_id == oid).all()
        assert len(rows) == 2
        kinds = {t.kind for t in rows}
        assert SupportTicketKind.support in kinds
        assert SupportTicketKind.improvement in kinds
        tid = next(t.id for t in rows if t.kind == SupportTicketKind.support)
    finally:
        db.close()

    r = client.get(f"/cabinet/support/{tid}")
    assert r.status_code == 200
    assert "Не открывается PDF" in r.text


def test_idor_ticket_and_attachment(app):
    client, dbmod = app
    email_a, oid_a, uid_a = _org_user(dbmod, "a@example.com", name="Org A")
    email_b, _oid_b, _uid_b = _org_user(dbmod, "b@example.com", name="Org B")
    assert login(client, email_a, "Passw0rd!").status_code == 303
    token = csrf_from(client, "/cabinet/support/")
    png = _make_png()
    with patch("app.services.support.send_email", return_value=True):
        r = client.post(
            "/cabinet/support/",
            data={
                "csrf_token": token,
                "kind": "support",
                "subject": "Секрет",
                "body": "Только для org A",
                "page_url": "/cabinet/",
            },
            files={"attachment": ("shot.png", png, "image/png")},
            follow_redirects=False,
        )
    assert r.status_code == 303
    loc = r.headers["location"]
    tid = int(re.search(r"/cabinet/support/(\d+)", loc).group(1))

    r = client.get(f"/cabinet/support/{tid}/attachment")
    assert r.status_code == 200

    assert login(client, email_b, "Passw0rd!").status_code == 303
    assert client.get(f"/cabinet/support/{tid}").status_code == 404
    assert client.get(f"/cabinet/support/{tid}/attachment").status_code == 404


def test_rate_limit_429(app):
    client, dbmod = app
    email, _oid, _uid = _org_user(dbmod, "rate@example.com")
    assert login(client, email, "Passw0rd!").status_code == 303
    with patch("app.services.support.send_email", return_value=True):
        for i in range(5):
            token = csrf_from(client, "/cabinet/support/")
            r = client.post(
                "/cabinet/support/",
                data={
                    "csrf_token": token,
                    "kind": "support",
                    "subject": f"Тема {i}",
                    "body": "Текст обращения достаточно длинный.",
                    "page_url": "/cabinet/",
                },
                follow_redirects=False,
            )
            assert r.status_code == 303, i
        token = csrf_from(client, "/cabinet/support/")
        r = client.post(
            "/cabinet/support/",
            data={
                "csrf_token": token,
                "kind": "support",
                "subject": "Лишняя",
                "body": "Должна получить 429.",
                "page_url": "/cabinet/",
            },
            follow_redirects=False,
        )
    assert r.status_code == 429


def test_admin_status_sends_user_mail(app):
    client, dbmod = app
    email, oid, uid = _org_user(dbmod, "notify@example.com")
    db = dbmod.SessionLocal()
    try:
        t = SupportTicket(
            org_id=oid,
            user_id=uid,
            kind=SupportTicketKind.support,
            subject="Сбой входа",
            body="Не проходит 2FA",
            status=SupportTicketStatus.new,
        )
        db.add(t)
        db.commit()
        tid = t.id
    finally:
        db.close()

    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    r = client.get("/admin/support/")
    assert r.status_code == 200
    assert "Сбой входа" in r.text

    token = csrf_from(client, f"/admin/support/{tid}")
    with patch("app.services.support.send_email", return_value=True) as mail:
        r = client.post(
            f"/admin/support/{tid}",
            data={
                "csrf_token": token,
                "status": "done",
                "admin_reply": "Проблема устранена, обновите страницу.",
                "admin_note": "внутр",
            },
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert mail.called
    args, kwargs = mail.call_args
    assert kwargs.get("to_addr") == email or (
        len(args) >= 1 and "notify@example.com" in str(mail.call_args)
    )
    body = kwargs.get("body") or ""
    assert "Проблема устранена" in body
    assert f"/cabinet/support/{tid}" in body

    assert login(client, email, "Passw0rd!").status_code == 303
    r = client.get(f"/cabinet/support/{tid}")
    assert r.status_code == 200
    assert "Проблема устранена" in r.text
