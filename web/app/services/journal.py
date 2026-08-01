"""Журнал документов."""

from __future__ import annotations

from datetime import date, datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Document


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
