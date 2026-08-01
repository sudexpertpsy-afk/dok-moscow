"""W-30: фоновые jobs PDF."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.models import Document, DocumentFormat, Job, JobStatus, JobType, Organization, User, UserRole
from app.security import hash_password
from app.services.jobs import enqueue_job, process_job
from conftest import csrf_from, login


def _seed_doc(dbmod):
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="Jobs Org", requisites={})
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id,
            email="jobs30@example.com",
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
            is_active=True,
        )
        db.add(user)
        db.flush()
        root = Path(dbmod.engine.url.database).parent if False else None
        from app.config import get_settings

        files = Path(get_settings().files_root) / str(org.id)
        files.mkdir(parents=True, exist_ok=True)
        docx = files / "doc.docx"
        docx.write_bytes(b"PK\x03\x04fake-docx")
        doc = Document(
            org_id=org.id,
            template="t.docx",
            number="1",
            file_path=f"{org.id}/doc.docx",
            format=DocumentFormat.docx,
            context={},
            created_by=user.id,
        )
        db.add(doc)
        db.commit()
        return org.id, user.id, doc.id, docx
    finally:
        db.close()


def test_document_pdf_job_inline(app):
    client, dbmod = app
    org_id, user_id, doc_id, docx = _seed_doc(dbmod)

    def fake_convert(src: Path, dst: Path) -> Path:
        dst.write_bytes(b"%PDF-1.4 fake")
        return dst

    with patch("app.services.jobs.convert_docx_to_pdf", side_effect=fake_convert):
        with patch("app.services.jobs.needs_watermark", return_value=False):
            db = dbmod.SessionLocal()
            try:
                job = enqueue_job(
                    db,
                    org_id=org_id,
                    user_id=user_id,
                    job_type=JobType.document_pdf,
                    payload={"document_id": doc_id},
                )
                assert job.status == JobStatus.succeeded
                assert job.result and job.result.get("document_id")
            finally:
                db.close()


def test_document_pdf_http_enqueues(app):
    client, dbmod = app
    org_id, user_id, doc_id, docx = _seed_doc(dbmod)
    assert login(client, "jobs30@example.com", "Passw0rd!").status_code == 303

    def fake_convert(src: Path, dst: Path) -> Path:
        dst.write_bytes(b"%PDF-1.4 fake")
        return dst

    token = csrf_from(client, f"/cabinet/documents/{doc_id}")
    with patch("app.services.jobs.convert_docx_to_pdf", side_effect=fake_convert):
        with patch("app.services.jobs.needs_watermark", return_value=False):
            r = client.post(
                f"/cabinet/documents/{doc_id}/pdf",
                data={"csrf_token": token},
                follow_redirects=False,
            )
    assert r.status_code == 303
    assert "/cabinet/jobs/" in r.headers["location"]
    job_id = int(r.headers["location"].rstrip("/").split("/")[-1])
    r2 = client.get(f"/cabinet/jobs/{job_id}")
    assert r2.status_code == 200
    assert "Готово" in r2.text or "Подготовка" in r2.text


def test_failed_job_stores_error(app):
    client, dbmod = app
    org_id, user_id, doc_id, _ = _seed_doc(dbmod)

    def boom(*_a, **_k):
        raise RuntimeError("gotenberg down")

    with patch("app.services.jobs.convert_docx_to_pdf", side_effect=boom):
        db = dbmod.SessionLocal()
        try:
            job = Job(
                org_id=org_id,
                user_id=user_id,
                type=JobType.document_pdf,
                status=JobStatus.pending,
                payload={"document_id": doc_id},
            )
            db.add(job)
            db.commit()
            jid = job.id
            process_job(db, jid)
            job = db.get(Job, jid)
            assert job.status == JobStatus.failed
            assert "gotenberg" in (job.error or "").lower()
        finally:
            db.close()
