"""Журнал документов."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Document
from app.services.audit import record_event
from app.services.templates import absolute_file

log = logging.getLogger("dok.journal")


def _day_start(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


def _day_end(d: date) -> datetime:
    return datetime.combine(d, time.max, tzinfo=timezone.utc)


def parse_date(raw: str | None) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def list_journal(
    db: Session,
    org_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    template: str | None = None,
    counterparty_id: int | None = None,
    facsimile: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[Document], int]:
    page = max(1, page)
    per_page = min(100, max(1, per_page))
    filters = [Document.org_id == org_id]
    if date_from:
        filters.append(Document.created_at >= _day_start(date_from))
    if date_to:
        filters.append(Document.created_at <= _day_end(date_to))
    if template:
        filters.append(Document.template == template)
    if counterparty_id:
        filters.append(Document.counterparty_id == counterparty_id)
    if facsimile in ("yes", "no"):
        # JSON path: context._facsimile_pdf
        flag = Document.context["_facsimile_pdf"].as_boolean()
        if facsimile == "yes":
            filters.append(flag.is_(True))
        else:
            filters.append(or_(flag.is_(False), flag.is_(None)))

    total = int(db.scalar(select(func.count()).select_from(Document).where(*filters)) or 0)
    rows = list(
        db.scalars(
            select(Document)
            .where(*filters)
            .order_by(Document.created_at.desc(), Document.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        ).all()
    )
    return rows, total


def distinct_templates(db: Session, org_id: int) -> list[str]:
    rows = db.scalars(
        select(Document.template).where(Document.org_id == org_id).distinct().order_by(Document.template)
    ).all()
    return list(rows)


def delete_org_document(
    db: Session,
    *,
    org_id: int,
    doc: Document,
    user_id: int | None,
) -> None:
    """Удалить файл документа (если есть) и запись Document в пределах org."""
    if doc.org_id != org_id:
        raise PermissionError("Документ другой организации")
    doc_id = doc.id
    template = doc.template
    number = doc.number
    rel_path = doc.file_path
    try:
        path = absolute_file(doc)
        if path.is_file():
            path.unlink()
    except FileNotFoundError:
        log.info("Файл документа #%s уже отсутствует: %s", doc_id, rel_path)
    except OSError:
        log.exception("Не удалось удалить файл документа #%s", doc_id)
        raise
    db.delete(doc)
    record_event(
        db,
        type="document_deleted",
        org_id=org_id,
        user_id=user_id,
        details={
            "document_id": doc_id,
            "template": template,
            "number": number or "",
        },
        commit=False,
    )
