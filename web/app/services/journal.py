"""Журнал документов и сквозной поиск."""

from __future__ import annotations

from datetime import date, datetime, time, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models import Counterparty, Document
from app.privacy import mask_address, mask_passport


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


def search_all(db: Session, org_id: int, query: str, *, limit: int = 30) -> dict:
    """Сквозной поиск по контрагентам и документам."""
    q = (query or "").strip()
    if len(q) < 2:
        return {"counterparties": [], "documents": [], "query": q}

    like = f"%{q}%"
    cps = list(
        db.scalars(
            select(Counterparty)
            .where(
                Counterparty.org_id == org_id,
                or_(
                    Counterparty.fio.ilike(like),
                    Counterparty.name.ilike(like),
                    Counterparty.inn.ilike(like),
                    Counterparty.email.ilike(like),
                    Counterparty.phone.ilike(like),
                ),
            )
            .order_by(Counterparty.id.desc())
            .limit(limit)
        ).all()
    )

    # документы: номер, шаблон; по контрагенту из найденных
    cp_ids = [c.id for c in cps]
    doc_filters = [
        Document.org_id == org_id,
        or_(
            Document.number.ilike(like),
            Document.template.ilike(like),
            Document.file_path.ilike(like),
            Document.counterparty_id.in_(cp_ids) if cp_ids else False,
        ),
    ]
    # SQLAlchemy False in or_ can be problematic — build carefully
    clauses = [
        Document.number.ilike(like),
        Document.template.ilike(like),
        Document.file_path.ilike(like),
    ]
    if cp_ids:
        clauses.append(Document.counterparty_id.in_(cp_ids))
    docs = list(
        db.scalars(
            select(Document)
            .where(Document.org_id == org_id, or_(*clauses))
            .order_by(Document.id.desc())
            .limit(limit)
        ).all()
    )

    return {
        "query": q,
        "counterparties": [
            {
                "id": c.id,
                "title": c.name or c.fio or f"#{c.id}",
                "inn": c.inn,
                "passport": mask_passport(c.passport_series, c.passport_number),
                "address": mask_address(c.address),
                "type": c.type.value,
            }
            for c in cps
        ],
        "documents": [
            {
                "id": d.id,
                "template": d.template,
                "number": d.number,
                "created_at": d.created_at,
                "counterparty_id": d.counterparty_id,
            }
            for d in docs
        ],
    }
