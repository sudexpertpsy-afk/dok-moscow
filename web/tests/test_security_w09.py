"""W-09: пароли, сброс, журнал входов, retention."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from app.defaults import empty_requisites
from app.models import Document, DocumentFormat, Event, Organization, PasswordResetToken, User, UserRole
from app.passwords import validate_password
from app.security import hash_password
from app.services.retention import get_retention_days, purge_expired_documents, set_retention_days
from conftest import csrf_from, login


def test_password_policy():
    assert validate_password("short1") is not None
    assert validate_password("onlyletters") is not None
    assert validate_password("1234567890") is not None
    assert validate_password("SolidPass12", email="good@example.com") is None
    assert validate_password("xxgoodxx99", email="good@example.com") is not None


def test_login_writes_event(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    db = dbmod.SessionLocal()
    try:
        ev = db.scalar(
            select(Event).where(Event.type == "login_success").order_by(Event.id.desc())
        )
        assert ev is not None
        assert ev.user_id is not None
    finally:
        db.close()


def test_failed_login_event(app):
    client, dbmod = app
    token = csrf_from(client, "/login")
    client.post(
        "/login",
        data={"email": "admin@dok.moscow", "password": "wrong-pass", "csrf_token": token},
        follow_redirects=False,
    )
    db = dbmod.SessionLocal()
    try:
        ev = db.scalar(select(Event).where(Event.type == "login_failure"))
        assert ev is not None
        assert ev.details.get("email") == "admin@dok.moscow"
    finally:
        db.close()


def test_forgot_and_reset_password(app):
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="ResetOrg", requisites=empty_requisites())
        db.add(org)
        db.flush()
        db.add(
            User(
                org_id=org.id,
                email="resetme@example.com",
                password_hash=hash_password("OldPassw0rd"),
                role=UserRole.user,
            )
        )
        db.commit()
    finally:
        db.close()

    token = csrf_from(client, "/forgot-password")
    with patch("app.routers.auth.send_email", return_value=True) as mail:
        r = client.post(
            "/forgot-password",
            data={"email": "resetme@example.com", "csrf_token": token},
            follow_redirects=False,
        )
    assert r.status_code == 200
    assert "отправили ссылку" in r.text
    assert mail.called
    body = mail.call_args.kwargs["body"]
    raw = body.split("/reset-password/", 1)[1].split()[0].strip()

    r = client.get(f"/reset-password/{raw}")
    assert r.status_code == 200
    csrf = csrf_from(client, f"/reset-password/{raw}")
    r = client.post(
        f"/reset-password/{raw}",
        data={
            "password": "NewPassw0rd!",
            "password2": "NewPassw0rd!",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "Пароль обновлён" in r.text

    assert login(client, "resetme@example.com", "OldPassw0rd").status_code == 401
    assert login(client, "resetme@example.com", "NewPassw0rd!").status_code == 303

    db = dbmod.SessionLocal()
    try:
        row = db.scalar(select(PasswordResetToken))
        assert row is not None and row.used_at is not None
        assert db.scalar(select(Event).where(Event.type == "password_reset")) is not None
    finally:
        db.close()


def test_retention_purge(app, tmp_path):
    client, dbmod = app
    files = tmp_path / "files"
    files.mkdir()
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="RetOrg", requisites=empty_requisites())
        db.add(org)
        db.flush()
        set_retention_days(org, 30)
        old_rel = f"{org.id}/old.docx"
        new_rel = f"{org.id}/new.docx"
        (files / str(org.id)).mkdir(parents=True, exist_ok=True)
        (files / old_rel).write_text("old")
        (files / new_rel).write_text("new")
        old_ts = datetime.now(timezone.utc) - timedelta(days=60)
        new_ts = datetime.now(timezone.utc) - timedelta(days=5)
        d_old = Document(
            org_id=org.id,
            template="Договор.docx",
            number="1",
            file_path=old_rel,
            format=DocumentFormat.docx,
            context={},
        )
        d_new = Document(
            org_id=org.id,
            template="Договор.docx",
            number="2",
            file_path=new_rel,
            format=DocumentFormat.docx,
            context={},
        )
        db.add_all([d_old, d_new])
        db.commit()
        # SQLAlchemy server_default may override — force timestamps
        d_old.created_at = old_ts
        d_new.created_at = new_ts
        db.commit()
        org_id = org.id
    finally:
        db.close()

    db = dbmod.SessionLocal()
    try:
        stats = purge_expired_documents(db, files, dry_run=False)
        assert stats.deleted_rows >= 1
        assert not (files / f"{org_id}/old.docx").exists()
        assert (files / f"{org_id}/new.docx").exists()
        left = db.scalars(select(Document).where(Document.org_id == org_id)).all()
        assert len(left) == 1
        assert left[0].file_path.endswith("new.docx")
    finally:
        db.close()


def test_settings_security_page(app):
    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="SecOrg", requisites=empty_requisites())
        db.add(org)
        db.flush()
        db.add(
            User(
                org_id=org.id,
                email="sec@example.com",
                password_hash=hash_password("SecPassw0rd"),
                role=UserRole.user,
            )
        )
        db.commit()
    finally:
        db.close()

    assert login(client, "sec@example.com", "SecPassw0rd").status_code == 303
    r = client.get("/cabinet/settings/security")
    assert r.status_code == 200
    assert "Журнал входов" in r.text
    assert "срок хранения" in r.text.lower() or "Срок хранения" in r.text

    token = csrf_from(client, "/cabinet/settings/security")
    r = client.post(
        "/cabinet/settings/security",
        data={"csrf_token": token, "срок_дней_файлов": "365"},
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "сохранён" in r.text

    db = dbmod.SessionLocal()
    try:
        org = db.scalar(select(Organization).where(Organization.name == "SecOrg"))
        assert get_retention_days(org) == 365
    finally:
        db.close()


def test_invite_rejects_weak_password(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    token = csrf_from(client, "/admin/")
    client.post(
        "/admin/organizations",
        data={"name": "WeakOrg", "csrf_token": token},
        follow_redirects=False,
    )
    db = dbmod.SessionLocal()
    try:
        org = db.scalar(select(Organization).where(Organization.name == "WeakOrg"))
        org_id = org.id
    finally:
        db.close()
    token = csrf_from(client, "/admin/")
    client.post(
        "/admin/invites",
        data={"org_id": org_id, "email": "weak@example.com", "csrf_token": token},
        follow_redirects=True,
    )
    db = dbmod.SessionLocal()
    try:
        from app.models import Invite

        inv = db.scalar(select(Invite).where(Invite.email == "weak@example.com"))
        inv_token = inv.token
    finally:
        db.close()

    csrf = csrf_from(client, "/admin/")
    client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)

    csrf = csrf_from(client, f"/invite/{inv_token}")
    r = client.post(
        f"/invite/{inv_token}",
        data={"password": "short", "password2": "short", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "Пароль" in r.text
