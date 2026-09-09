"""Очередь фоновых задач (W-30)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Document,
    DocumentFormat,
    Job,
    JobStatus,
    JobType,
    utcnow,
)
from app.services.gotenberg import GotenbergError, convert_docx_to_pdf
from app.services.limits import needs_watermark
from app.services.package_generate import build_merged_pdf, build_zip, package_export_dir
from app.services.templates import absolute_file
from app.services.watermark import apply_guest_watermark

log = logging.getLogger("dok.jobs")


def enqueue_job(
    db: Session,
    *,
    org_id: int,
    user_id: int | None,
    job_type: JobType,
    payload: dict[str, Any],
) -> Job:
    job = Job(
        org_id=org_id,
        user_id=user_id,
        type=job_type,
        status=JobStatus.pending,
        payload=payload,
        progress=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    if get_settings().jobs_inline:
        process_job(db, job.id)
        db.refresh(job)
    return job


def claim_next_job(db: Session) -> Job | None:
    dialect = db.bind.dialect.name if db.bind is not None else ""
    stmt = (
        select(Job)
        .where(Job.status == JobStatus.pending)
        .order_by(Job.id.asc())
        .limit(1)
    )
    if dialect == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    job = db.scalar(stmt)
    if job is None:
        return None
    job.status = JobStatus.running
    job.started_at = utcnow()
    job.attempts = int(job.attempts or 0) + 1
    job.progress = 5
    db.commit()
    db.refresh(job)
    return job


def process_job(db: Session, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise ValueError(f"job {job_id} not found")
    if job.status == JobStatus.pending:
        job.status = JobStatus.running
        job.started_at = utcnow()
        job.attempts = int(job.attempts or 0) + 1
        db.commit()
    try:
        result = _execute(db, job)
        job.result = result
        job.status = JobStatus.succeeded
        job.progress = 100
        job.error = None
        job.finished_at = utcnow()
        db.commit()
    except Exception as exc:
        log.exception("job %s failed", job_id)
        job.status = JobStatus.failed
        job.error = str(exc)[:2000]
        job.finished_at = utcnow()
        job.progress = max(int(job.progress or 0), 1)
        db.commit()
    db.refresh(job)
    return job


def process_pending_batch(db: Session, *, limit: int = 5) -> int:
    done = 0
    for _ in range(limit):
        job = claim_next_job(db)
        if job is None:
            break
        process_job(db, job.id)
        done += 1
    return done


def _execute(db: Session, job: Job) -> dict[str, Any]:
    payload = job.payload or {}
    if job.type == JobType.document_pdf:
        return _run_document_pdf(db, job, payload)
    if job.type == JobType.package_pdf:
        return _run_package_pdf(db, job, payload)
    if job.type == JobType.package_zip:
        return _run_package_zip(db, job, payload)
    if job.type == JobType.counterparty_import:
        return _run_counterparty_import(db, job, payload)
    raise ValueError(f"unknown job type {job.type}")


def _run_counterparty_import(db: Session, job: Job, payload: dict) -> dict[str, Any]:
    from sqlalchemy import select

    from app.models import Counterparty, CounterpartyType
    from app.services.cp_import import (
        DuplicateMode,
        auto_map_columns,
        classify_rows,
        commit_rows,
        load_meta,
        load_table_snapshot,
    )

    token = str(payload.get("token") or "")
    table, mapping = load_table_snapshot(job.org_id, token)
    meta = load_meta(job.org_id, token)
    mapping = mapping or auto_map_columns(table.headers)
    default_raw = str(meta.get("default_type") or "auto")
    default_type = (
        CounterpartyType(default_raw) if default_raw in ("fl", "ul", "expert") else None
    )
    mode = DuplicateMode(str(meta.get("duplicate_mode") or DuplicateMode.fill))
    enrich = bool(meta.get("enrich"))
    require_zero = bool(meta.get("require_zero_errors"))
    filename = str(meta.get("filename") or "import")
    job.progress = 15
    db.commit()
    existing = list(
        db.scalars(select(Counterparty).where(Counterparty.org_id == job.org_id)).all()
    )
    rows = classify_rows(table, mapping, default_type=default_type, existing=existing)
    job.progress = 40
    db.commit()
    result = commit_rows(
        db,
        job.org_id,
        rows,
        mode=mode,
        require_zero_errors=require_zero,
        user_id=job.user_id,
        filename=filename,
        token=token,
        enrich=enrich,
    )
    job.progress = 90
    db.commit()
    return {
        "created": result.created,
        "updated": result.updated,
        "skipped": result.skipped,
        "errors": result.errors,
        "file_path": result.report_rel,
        "download_url": f"/cabinet/jobs/{job.id}/download" if result.report_rel else None,
        "view_url": "/cabinet/counterparties/?ok=imported",
        "summary": (
            f"Создано {result.created}, обновлено {result.updated}, "
            f"пропущено {result.skipped}, ошибок {result.errors}"
        ),
    }


def _run_document_pdf(db: Session, job: Job, payload: dict) -> dict[str, Any]:
    import tempfile

    from app.models import Organization
    from app.services.facsimile import should_use_images_for_pdf
    from app.services.templates import fill_docx_with_facsimile

    doc_id = int(payload["document_id"])
    doc = db.get(Document, doc_id)
    if doc is None or doc.org_id != job.org_id:
        raise ValueError("Документ не найден")
    docx_path = absolute_file(doc)
    pdf_path = docx_path.with_suffix(".pdf")
    job.progress = 30
    db.commit()

    convert_src = docx_path
    tmp_path = None
    if should_use_images_for_pdf(doc.context):
        org = db.get(Organization, job.org_id)
        if org is not None:
            tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
            tmp.close()
            tmp_path = Path(tmp.name)
            fill_docx_with_facsimile(
                org=org,
                template_name=doc.template,
                context=doc.context or {},
                output_path=tmp_path,
            )
            convert_src = tmp_path

    try:
        convert_docx_to_pdf(convert_src, pdf_path)
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    job.progress = 70
    db.commit()
    if needs_watermark(db, job.org_id):
        apply_guest_watermark(pdf_path)
    settings = get_settings()
    rel = str(pdf_path.relative_to(settings.files_root))
    pdf_doc = Document(
        org_id=job.org_id,
        contract_id=doc.contract_id,
        counterparty_id=doc.counterparty_id,
        template=doc.template,
        number=doc.number,
        file_path=rel,
        format=DocumentFormat.pdf,
        context=doc.context,
        created_by=job.user_id,
    )
    db.add(pdf_doc)
    db.flush()
    return {
        "document_id": pdf_doc.id,
        "download_url": f"/cabinet/documents/{pdf_doc.id}/download",
        "view_url": f"/cabinet/documents/{pdf_doc.id}",
    }


def _run_package_pdf(db: Session, job: Job, payload: dict) -> dict[str, Any]:
    from app.org_scope import get_document_for_org

    ids = [int(i) for i in payload.get("document_ids") or []]
    if not ids:
        raise ValueError("Пустой комплект")
    docs = [get_document_for_org(db, job.org_id, i) for i in ids]
    pdf_path = package_export_dir(job.org_id) / f"комплект_{ids[0]}.pdf"
    job.progress = 20
    db.commit()
    build_merged_pdf(docs, pdf_path)
    rel = str(pdf_path)
    return {
        "file_path": rel,
        "download_url": f"/cabinet/jobs/{job.id}/download",
        "filename": pdf_path.name,
    }


def _run_package_zip(db: Session, job: Job, payload: dict) -> dict[str, Any]:
    from app.org_scope import get_document_for_org

    ids = [int(i) for i in payload.get("document_ids") or []]
    if not ids:
        raise ValueError("Пустой комплект")
    docs = [get_document_for_org(db, job.org_id, i) for i in ids]
    zip_path = package_export_dir(job.org_id) / f"комплект_{ids[0]}.zip"
    job.progress = 20
    db.commit()
    build_zip(docs, zip_path)
    return {
        "file_path": str(zip_path),
        "download_url": f"/cabinet/jobs/{job.id}/download",
        "filename": zip_path.name,
    }


def job_download_path(job: Job) -> Path | None:
    """Путь к результату job только внутри FILES_ROOT/{job.org_id} (F-01)."""
    from app.services.safe_paths import resolve_under_org

    if job.status != JobStatus.succeeded or not job.result:
        return None
    path = job.result.get("file_path")
    if not path:
        return None
    try:
        p = resolve_under_org(job.org_id, path)
    except FileNotFoundError:
        return None
    return p if p.is_file() else None
