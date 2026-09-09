"""W-49 C: полная выгрузка организации dok-export-v1."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.defaults import empty_requisites
from app.models import (
    Counterparty,
    CounterpartySource,
    CounterpartyType,
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
from app.services.org_export import (
    FORMAT_VERSION,
    build_org_export_zip,
    check_export_rate,
    consume_export_rate,
)
from conftest import csrf_from, login


def _seed(dbmod, email="exp@example.com", *, name="ExportOrg"):
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
        db.flush()
        cp = Counterparty(
            org_id=org.id,
            type=CounterpartyType.ul,
            source=CounterpartySource.manual,
            name="ООО Экспорт",
            inn="7707083893",
        )
        db.add(cp)
        db.flush()
        files = Path(get_settings().files_root) / str(org.id)
        files.mkdir(parents=True, exist_ok=True)
        doc_path = files / "demo.docx"
        doc_path.write_bytes(b"PK\x03\x04fake")
        doc = Document(
            org_id=org.id,
            counterparty_id=cp.id,
            template="demo.docx",
            number="Д-1",
            file_path=f"{org.id}/demo.docx",
            format=DocumentFormat.docx,
            context={"x": 1},
            created_by=user.id,
        )
        db.add(doc)
        db.commit()
        return org.id, user.id, email, cp.id, doc.id
    finally:
        db.close()


def test_build_zip_completeness_and_no_secrets(app):
    _, dbmod = app
    org_id, user_id, _, _, doc_id = _seed(dbmod, "zip1@example.com")
    db = dbmod.SessionLocal()
    try:
        result = build_org_export_zip(db, org_id, user_id=user_id)
        db.commit()
        assert result["format"] == FORMAT_VERSION
        path = Path(get_settings().files_root) / result["file_path"]
        assert path.is_file()
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            assert "README.txt" in names
            assert "manifest.json" in names
            assert "organization.json" in names
            assert "users.json" in names
            assert "counterparties.json" in names
            assert "documents.json" in names
            assert any(n.startswith("files/documents/") for n in names)
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["org_id"] == org_id
            assert manifest["counts"]["counterparties"] == 1
            assert manifest["counts"]["documents"] == 1
            assert manifest["counts"]["document_files"] == 1
            users = json.loads(zf.read("users.json"))
            assert users[0]["email"] == "zip1@example.com"
            blob = zf.read("users.json").decode("utf-8")
            assert "password_hash" not in blob
            assert "totp_secret" not in blob
            assert "Passw0rd" not in blob
            docs = json.loads(zf.read("documents.json"))
            assert docs[0]["id"] == doc_id
            # файл на месте
            arc = docs[0]["archive_path"]
            assert zf.read(arc)[:2] == b"PK"
    finally:
        db.close()


def test_idor_two_orgs(app):
    _, dbmod = app
    org_a, user_a, _, _, _ = _seed(dbmod, "a-exp@example.com", name="OrgA")
    org_b, user_b, _, _, _ = _seed(dbmod, "b-exp@example.com", name="OrgB")
    db = dbmod.SessionLocal()
    try:
        res_a = build_org_export_zip(db, org_a, user_id=user_a)
        db.commit()
        path = Path(get_settings().files_root) / res_a["file_path"]
        with zipfile.ZipFile(path) as zf:
            data = zf.read("counterparties.json").decode("utf-8")
            assert "OrgB" not in data
            assert str(org_b) not in json.loads(zf.read("manifest.json")).get("org_name", "")
            users = json.loads(zf.read("users.json"))
            assert all(u["email"] != "b-exp@example.com" for u in users)
            # чужой файл не в архиве A
            assert all(f"/{org_b}/" not in n for n in zf.namelist())
    finally:
        db.close()


def test_export_http_password_flow(app):
    client, dbmod = app
    org_id, _, email, _, _ = _seed(dbmod, "http-exp@example.com")
    assert login(client, email, "Passw0rd!").status_code == 303

    r = client.get("/cabinet/settings/data")
    assert r.status_code == 200
    assert "Выгрузить" in r.text
    assert "Пароль" in r.text  # нет 2FA

    csrf = csrf_from(client, "/cabinet/settings/data")
    r = client.post(
        "/cabinet/settings/data/export",
        data={"csrf_token": csrf, "password": "wrong"},
        follow_redirects=False,
    )
    assert r.status_code == 401

    csrf = csrf_from(client, "/cabinet/settings/data")
    r = client.post(
        "/cabinet/settings/data/export",
        data={"csrf_token": csrf, "password": "Passw0rd!"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/cabinet/jobs/" in r.headers["location"]
    job_id = int(r.headers["location"].rstrip("/").split("/")[-1])

    db = dbmod.SessionLocal()
    try:
        job = db.get(Job, job_id)
        assert job is not None
        assert job.org_id == org_id
        assert job.type == JobType.org_export
        assert job.status == JobStatus.succeeded
        assert job.result and job.result.get("file_path")
        # download
        dl = client.get(f"/cabinet/jobs/{job_id}/download", follow_redirects=False)
        assert dl.status_code == 200
        assert dl.content[:2] == b"PK"
    finally:
        db.close()


def test_daily_limit(app):
    _, dbmod = app
    org_id, _, _, _, _ = _seed(dbmod, "lim-exp@example.com")
    db = dbmod.SessionLocal()
    try:
        consume_export_rate(db, org_id)
        consume_export_rate(db, org_id)
        db.commit()
        try:
            check_export_rate(db, org_id)
            raise AssertionError("expected ExportError")
        except Exception as exc:
            assert "2" in str(exc) or "лимит" in str(exc).lower()
    finally:
        db.close()


def test_member_forbidden(app):
    from app.models import OrgRole

    client, dbmod = app
    org_id, _, email, _, _ = _seed(dbmod, "adm-exp@example.com")
    db = dbmod.SessionLocal()
    try:
        member = User(
            org_id=org_id,
            email="mem-exp@example.com",
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
            org_role=OrgRole.org_member,
            is_active=True,
        )
        db.add(member)
        db.commit()
    finally:
        db.close()
    assert login(client, "mem-exp@example.com", "Passw0rd!").status_code == 303
    r = client.get("/cabinet/settings/data")
    # forbidden page or redirect — не форма выгрузки
    assert "Выгрузить всё" not in r.text or r.status_code in (403, 303)
