"""Транш А аудита 2026-08-02: F-01/F-02 пути, F-05 вебхук, F-07 CSRF, F-08 compare_digest."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

from app.billing.tbank import build_token, verify_token
from app.defaults import empty_requisites
from app.models import (
    Document,
    DocumentFormat,
    Job,
    JobStatus,
    JobType,
    Organization,
    User,
    UserRole,
)
from app.security import hash_password
from app.services.ops import check_alerts, ops_dir, read_marker, record_webhook_fail
from app.services.safe_paths import resolve_under, resolve_under_org
from app.services.templates import absolute_file
from conftest import csrf_from, login


def _two_orgs(dbmod):
    db = dbmod.SessionLocal()
    try:
        org_a = Organization(name="Org A", requisites=empty_requisites())
        org_b = Organization(name="Org B", requisites=empty_requisites())
        db.add_all([org_a, org_b])
        db.flush()
        user_a = User(
            org_id=org_a.id,
            email="a-tranche@example.com",
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
            is_active=True,
        )
        user_b = User(
            org_id=org_b.id,
            email="b-tranche@example.com",
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
            is_active=True,
        )
        db.add_all([user_a, user_b])
        db.commit()
        return org_a.id, org_b.id, user_a.id, user_b.id
    finally:
        db.close()


def test_resolve_under_blocks_prefix_bypass(tmp_path, monkeypatch):
    files = tmp_path / "files"
    files.mkdir()
    evil = tmp_path / "files_evil"
    evil.mkdir()
    secret = evil / "secret.txt"
    secret.write_text("leak", encoding="utf-8")

    monkeypatch.setenv("FILES_ROOT", str(files))
    from app.config import get_settings

    get_settings.cache_clear()
    object.__setattr__(get_settings(), "files_root", str(files))

    # классический startswith ложно принимает sibling FILES_ROOT_evil
    assert str(evil.resolve()).startswith(str(files.resolve()))
    try:
        resolve_under(files, Path("..") / "files_evil" / "secret.txt")
        raised = False
    except FileNotFoundError:
        raised = True
    assert raised

    org = files / "1"
    org.mkdir()
    good = org / "ok.txt"
    good.write_text("ok", encoding="utf-8")
    assert resolve_under_org(1, "1/ok.txt") == good.resolve()

    try:
        resolve_under_org(1, str(secret))
        raised = False
    except FileNotFoundError:
        raised = True
    assert raised
    get_settings.cache_clear()


def test_absolute_file_rejects_traversal_and_foreign_org(app, tmp_path, monkeypatch):
    client, dbmod = app
    from app.config import get_settings

    files = Path(get_settings().files_root)
    org_a, org_b, user_a, _ = _two_orgs(dbmod)

    (files / str(org_a)).mkdir(parents=True, exist_ok=True)
    (files / str(org_b)).mkdir(parents=True, exist_ok=True)
    own = files / str(org_a) / "own.docx"
    own.write_bytes(b"PK\x03\x04own")
    foreign = files / str(org_b) / "foreign.docx"
    foreign.write_bytes(b"PK\x03\x04foreign")

    # prefix-bypass sibling
    evil_root = Path(str(files) + "_evil")
    evil_root.mkdir(exist_ok=True)
    leak = evil_root / "leak.docx"
    leak.write_bytes(b"PK\x03\x04leak")

    db = dbmod.SessionLocal()
    try:
        doc_ok = Document(
            org_id=org_a,
            template="t.docx",
            number="1",
            file_path=f"{org_a}/own.docx",
            format=DocumentFormat.docx,
            context={},
            created_by=user_a,
        )
        doc_trav = Document(
            org_id=org_a,
            template="t.docx",
            number="2",
            file_path=f"{org_a}/../{org_b}/foreign.docx",
            format=DocumentFormat.docx,
            context={},
            created_by=user_a,
        )
        doc_evil = Document(
            org_id=org_a,
            template="t.docx",
            number="3",
            file_path=str(leak),
            format=DocumentFormat.docx,
            context={},
            created_by=user_a,
        )
        db.add_all([doc_ok, doc_trav, doc_evil])
        db.commit()
        db.refresh(doc_ok)
        db.refresh(doc_trav)
        db.refresh(doc_evil)

        assert absolute_file(doc_ok) == own.resolve()
        for bad in (doc_trav, doc_evil):
            try:
                absolute_file(bad)
                ok = False
            except FileNotFoundError:
                ok = True
            assert ok
    finally:
        db.close()


def test_symlink_outside_org_rejected(app):
    _, dbmod = app
    from app.config import get_settings

    files = Path(get_settings().files_root)
    org_a, org_b, user_a, _ = _two_orgs(dbmod)
    (files / str(org_a)).mkdir(parents=True, exist_ok=True)
    (files / str(org_b)).mkdir(parents=True, exist_ok=True)
    target = files / str(org_b) / "secret.docx"
    target.write_bytes(b"PK\x03\x04secret")
    link = files / str(org_a) / "link.docx"
    try:
        link.symlink_to(target)
    except OSError:
        return  # окружение без symlink — пропускаем

    db = dbmod.SessionLocal()
    try:
        doc = Document(
            org_id=org_a,
            template="t.docx",
            number="L",
            file_path=f"{org_a}/link.docx",
            format=DocumentFormat.docx,
            context={},
            created_by=user_a,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        try:
            absolute_file(doc)
            ok = False
        except FileNotFoundError:
            ok = True
        assert ok
    finally:
        db.close()


def test_job_download_idor_and_path_escape(app):
    client, dbmod = app
    from app.config import get_settings
    from app.services.jobs import job_download_path

    files = Path(get_settings().files_root)
    org_a, org_b, user_a, user_b = _two_orgs(dbmod)
    (files / str(org_a)).mkdir(parents=True, exist_ok=True)
    (files / str(org_b)).mkdir(parents=True, exist_ok=True)

    good = files / str(org_a) / "pack.zip"
    good.write_bytes(b"PK\x03\x04zip")
    foreign = files / str(org_b) / "other.zip"
    foreign.write_bytes(b"PK\x03\x04other")

    db = dbmod.SessionLocal()
    try:
        job_ok = Job(
            org_id=org_a,
            user_id=user_a,
            type=JobType.package_zip,
            status=JobStatus.succeeded,
            payload={},
            result={"file_path": str(good), "filename": "pack.zip"},
            progress=100,
        )
        job_escape = Job(
            org_id=org_a,
            user_id=user_a,
            type=JobType.package_zip,
            status=JobStatus.succeeded,
            payload={},
            result={"file_path": str(foreign), "filename": "other.zip"},
            progress=100,
        )
        job_b = Job(
            org_id=org_b,
            user_id=user_b,
            type=JobType.package_zip,
            status=JobStatus.succeeded,
            payload={},
            result={"file_path": str(foreign), "filename": "other.zip"},
            progress=100,
        )
        db.add_all([job_ok, job_escape, job_b])
        db.commit()
        j_ok, j_esc, j_b = job_ok.id, job_escape.id, job_b.id
        assert job_download_path(job_ok) == good.resolve()
        assert job_download_path(job_escape) is None
    finally:
        db.close()

    assert login(client, "a-tranche@example.com", "Passw0rd!").status_code == 303
    r = client.get(f"/cabinet/jobs/{j_ok}/download")
    assert r.status_code == 200
    r = client.get(f"/cabinet/jobs/{j_esc}/download")
    assert r.status_code == 404
    r = client.get(f"/cabinet/jobs/{j_b}/download")
    assert r.status_code == 404


def test_document_download_foreign_org_404(app):
    client, dbmod = app
    from app.config import get_settings

    files = Path(get_settings().files_root)
    org_a, org_b, user_a, _ = _two_orgs(dbmod)
    (files / str(org_b)).mkdir(parents=True, exist_ok=True)
    path = files / str(org_b) / "only_b.docx"
    path.write_bytes(b"PK\x03\x04b")
    db = dbmod.SessionLocal()
    try:
        doc = Document(
            org_id=org_b,
            template="t.docx",
            number="B1",
            file_path=f"{org_b}/only_b.docx",
            format=DocumentFormat.docx,
            context={},
            created_by=user_a,
        )
        db.add(doc)
        db.commit()
        doc_id = doc.id
    finally:
        db.close()

    assert login(client, "a-tranche@example.com", "Passw0rd!").status_code == 303
    r = client.get(f"/cabinet/documents/{doc_id}/download")
    assert r.status_code == 404


def test_verify_token_uses_compare_digest():
    params = {
        "TerminalKey": "T",
        "OrderId": "1",
        "Success": True,
        "Status": "CONFIRMED",
        "PaymentId": "9",
        "Amount": 100,
    }
    password = "secret-password"
    params["Token"] = build_token(params, password)
    assert verify_token(params, password)
    params["Token"] = "0" * len(params["Token"])
    assert not verify_token(params, password)
    with patch("app.billing.tbank.hmac.compare_digest", return_value=True) as spy:
        params["Token"] = build_token(params, password)
        assert verify_token(params, password)
        spy.assert_called_once()


def test_ops_agent_bearer_compare_digest(monkeypatch):
    from fastapi.testclient import TestClient

    from app.ops_agent.app import create_ops_agent_app

    token = "z" * 40
    monkeypatch.setenv("OPS_AGENT_TOKEN", token)
    app = create_ops_agent_app()
    client = TestClient(app)
    assert client.get("/v1/health", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.get("/v1/health", headers={"Authorization": "Bearer " + ("y" * 40)}).status_code == 401
    with patch("app.ops_agent.app.hmac.compare_digest", return_value=False) as spy:
        r = client.get("/v1/health", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
        spy.assert_called()


def test_cms_preview_requires_csrf(app):
    client, _ = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    r = client.post("/admin/cms/content/preview", data={"body_md": "# hi"})
    assert r.status_code == 403
    token = csrf_from(client, "/admin/cms/content/")
    r = client.post(
        "/admin/cms/content/preview",
        data={"body_md": "**ok**", "csrf_token": token},
    )
    assert r.status_code == 200
    assert "ok" in r.text


def test_webhook_bad_token_logged_and_hourly_alert(app, caplog):
    from app.routers import billing as billing_router

    client, dbmod = app
    billing_router.webhook_limiter.clear()
    for p in ops_dir().glob("*.json"):
        p.unlink()

    with caplog.at_level(logging.WARNING, logger="dok.billing.webhook"):
        r = client.post(
            "/billing/webhook",
            json={
                "TerminalKey": "T",
                "OrderId": "missing",
                "Success": True,
                "Status": "CONFIRMED",
                "PaymentId": "1",
                "Amount": 100,
                "Token": "0" * 64,
            },
        )
    assert r.status_code == 200
    assert any("webhook rejected" in m and "ip=" in m for m in caplog.messages)
    marker = read_marker("webhook_fail") or {}
    assert int(marker.get("hour_count") or 0) >= 1
    assert marker.get("last_ip")

    for _ in range(11):
        record_webhook_fail("flood", ip="9.9.9.9")
    db = dbmod.SessionLocal()
    try:
        fired = check_alerts(db)
        assert "webhook_fail_rate" in fired
    finally:
        db.close()
