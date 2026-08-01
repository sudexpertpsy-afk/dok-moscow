"""Календарь, планирование и напоминания кабинета."""

from __future__ import annotations

import calendar as cal_mod
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import (
    CalendarEvent,
    CalendarEventKind,
    CalendarEventStatus,
    Contract,
    Counterparty,
    Document,
    Organization,
    utcnow,
)

_RU_DATE = re.compile(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

KIND_LABELS = {
    CalendarEventKind.plan: "План",
    CalendarEventKind.meeting: "Встреча",
    CalendarEventKind.contract_end: "Окончание договора",
    CalendarEventKind.contract_start: "Начало / заключение",
    CalendarEventKind.payment_due: "Оплата",
    CalendarEventKind.other: "Другое",
}

STATUS_LABELS = {
    CalendarEventStatus.planned: "Запланировано",
    CalendarEventStatus.done: "Выполнено",
    CalendarEventStatus.cancelled: "Отменено",
}


def parse_ru_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    m = _RU_DATE.match(text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    m = _ISO_DATE.match(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def parse_amount(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    text = str(value).strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    text = re.sub(r"[^\d.]", "", text)
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def create_event(
    db: Session,
    *,
    org_id: int,
    title: str,
    due_on: date,
    kind: CalendarEventKind = CalendarEventKind.plan,
    notes: str | None = None,
    remind_days_before: int | None = 1,
    counterparty_id: int | None = None,
    document_id: int | None = None,
    contract_id: int | None = None,
    created_by: int | None = None,
    status: CalendarEventStatus = CalendarEventStatus.planned,
) -> CalendarEvent:
    title = (title or "").strip()
    if not title:
        raise ValueError("Укажите название")
    if remind_days_before is not None and remind_days_before < 0:
        remind_days_before = 0
    ev = CalendarEvent(
        org_id=org_id,
        title=title[:255],
        notes=(notes or "").strip() or None,
        due_on=due_on,
        kind=kind,
        status=status,
        remind_days_before=remind_days_before,
        counterparty_id=counterparty_id,
        document_id=document_id,
        contract_id=contract_id,
        created_by=created_by,
    )
    db.add(ev)
    db.flush()
    return ev


def update_event(
    db: Session,
    ev: CalendarEvent,
    *,
    title: str,
    due_on: date,
    kind: CalendarEventKind,
    notes: str | None,
    remind_days_before: int | None,
    status: CalendarEventStatus,
    counterparty_id: int | None,
) -> CalendarEvent:
    ev.title = (title or "").strip()[:255]
    if not ev.title:
        raise ValueError("Укажите название")
    ev.due_on = due_on
    ev.kind = kind
    ev.notes = (notes or "").strip() or None
    ev.remind_days_before = remind_days_before
    # при смене даты/напоминания — снова можно напомнить
    if ev.remind_days_before is None:
        ev.reminded_at = None
    elif ev.status == CalendarEventStatus.planned:
        remind_on = ev.due_on - timedelta(days=int(ev.remind_days_before))
        if remind_on > date.today():
            ev.reminded_at = None
    ev.status = status
    ev.counterparty_id = counterparty_id
    db.flush()
    return ev


def events_in_range(
    db: Session,
    org_id: int,
    start: date,
    end: date,
    *,
    include_cancelled: bool = False,
) -> list[CalendarEvent]:
    stmt = (
        select(CalendarEvent)
        .options(
            joinedload(CalendarEvent.counterparty),
            joinedload(CalendarEvent.contract),
            joinedload(CalendarEvent.document),
        )
        .where(
            CalendarEvent.org_id == org_id,
            CalendarEvent.due_on >= start,
            CalendarEvent.due_on <= end,
        )
        .order_by(CalendarEvent.due_on, CalendarEvent.id)
    )
    if not include_cancelled:
        stmt = stmt.where(CalendarEvent.status != CalendarEventStatus.cancelled)
    return list(db.scalars(stmt).unique().all())


def upcoming_and_overdue(
    db: Session,
    org_id: int,
    *,
    within_days: int = 14,
    today: date | None = None,
) -> list[CalendarEvent]:
    today = today or date.today()
    horizon = today + timedelta(days=within_days)
    return list(
        db.scalars(
            select(CalendarEvent)
            .options(joinedload(CalendarEvent.counterparty))
            .where(
                CalendarEvent.org_id == org_id,
                CalendarEvent.status == CalendarEventStatus.planned,
                CalendarEvent.due_on <= horizon,
            )
            .order_by(CalendarEvent.due_on, CalendarEvent.id)
        )
        .unique()
        .all()
    )


@dataclass
class MonthCell:
    day: date | None
    in_month: bool
    events: list[CalendarEvent]


def month_grid(
    db: Session,
    org_id: int,
    year: int,
    month: int,
) -> list[list[MonthCell]]:
    first = date(year, month, 1)
    last = date(year, month, cal_mod.monthrange(year, month)[1])
    # сетка с понедельника
    grid_start = first - timedelta(days=first.weekday())
    grid_end = last + timedelta(days=(6 - last.weekday()))
    events = events_in_range(db, org_id, grid_start, grid_end)
    by_day: dict[date, list[CalendarEvent]] = {}
    for ev in events:
        by_day.setdefault(ev.due_on, []).append(ev)

    weeks: list[list[MonthCell]] = []
    cur = grid_start
    while cur <= grid_end:
        week: list[MonthCell] = []
        for _ in range(7):
            week.append(
                MonthCell(
                    day=cur,
                    in_month=(cur.month == month),
                    events=by_day.get(cur, []),
                )
            )
            cur += timedelta(days=1)
        weeks.append(week)
    return weeks


def upsert_contract_from_package(
    db: Session,
    *,
    org: Organization,
    counterparty: Counterparty,
    contract_template: str,
    context: dict,
    document_ids: list[int],
    user_id: int | None,
) -> Contract | None:
    """Создать/обновить договор и события календаря после генерации комплекта."""
    from app.services.templates import ensure_core_on_path

    ensure_core_on_path()
    from docfiller_core.master import БЕЗ_ДОГОВОРА

    if not contract_template or contract_template == БЕЗ_ДОГОВОРА:
        return None
    if not str(contract_template).startswith("Договор_"):
        return None

    number = str(context.get("номер_договора") or "").strip() or "б/н"
    signed_on = parse_ru_date(context.get("дата_договора") or context.get("дата_начала"))
    ends_on = parse_ru_date(context.get("дата_окончания"))
    if ends_on is None and signed_on is not None:
        days = context.get("срок_дней")
        try:
            n = int(str(days).strip()) if days not in (None, "") else 0
        except ValueError:
            n = 0
        if n > 0:
            ends_on = signed_on + timedelta(days=n)
    amount = parse_amount(context.get("сумма") or context.get("сумма_договора"))

    existing = db.scalar(
        select(Contract).where(
            Contract.org_id == org.id,
            Contract.number == number,
            Contract.counterparty_id == counterparty.id,
        )
    )
    if existing:
        contract = existing
        contract.template = contract_template
        if signed_on:
            contract.signed_on = signed_on
        if ends_on:
            contract.ends_on = ends_on
        if amount is not None:
            contract.amount = amount
    else:
        contract = Contract(
            org_id=org.id,
            counterparty_id=counterparty.id,
            template=contract_template,
            number=number,
            signed_on=signed_on,
            ends_on=ends_on,
            amount=amount,
        )
        db.add(contract)
        db.flush()

    for doc_id in document_ids:
        doc = db.get(Document, doc_id)
        if doc and doc.org_id == org.id:
            doc.contract_id = contract.id

    cp_name = counterparty.name or counterparty.fio or "контрагент"
    if signed_on:
        _upsert_linked_event(
            db,
            org_id=org.id,
            contract_id=contract.id,
            kind=CalendarEventKind.contract_start,
            due_on=signed_on,
            title=f"Договор {number} заключён — {cp_name}",
            notes=f"Шаблон: {contract_template}",
            counterparty_id=counterparty.id,
            document_id=document_ids[0] if document_ids else None,
            created_by=user_id,
            remind_days_before=None,
        )
    if ends_on:
        _upsert_linked_event(
            db,
            org_id=org.id,
            contract_id=contract.id,
            kind=CalendarEventKind.contract_end,
            due_on=ends_on,
            title=f"Окончание договора {number} — {cp_name}",
            notes=f"Шаблон: {contract_template}",
            counterparty_id=counterparty.id,
            document_id=document_ids[0] if document_ids else None,
            created_by=user_id,
            remind_days_before=7,
        )
    db.flush()
    return contract


def _upsert_linked_event(
    db: Session,
    *,
    org_id: int,
    contract_id: int,
    kind: CalendarEventKind,
    due_on: date,
    title: str,
    notes: str | None,
    counterparty_id: int | None,
    document_id: int | None,
    created_by: int | None,
    remind_days_before: int | None,
) -> CalendarEvent:
    existing = db.scalar(
        select(CalendarEvent).where(
            CalendarEvent.org_id == org_id,
            CalendarEvent.contract_id == contract_id,
            CalendarEvent.kind == kind,
            CalendarEvent.status != CalendarEventStatus.cancelled,
        )
    )
    if existing:
        existing.title = title[:255]
        existing.due_on = due_on
        existing.notes = notes
        existing.counterparty_id = counterparty_id
        if document_id:
            existing.document_id = document_id
        if remind_days_before is not None:
            existing.remind_days_before = remind_days_before
            remind_on = due_on - timedelta(days=int(remind_days_before))
            if remind_on > date.today():
                existing.reminded_at = None
        db.flush()
        return existing
    return create_event(
        db,
        org_id=org_id,
        title=title,
        due_on=due_on,
        kind=kind,
        notes=notes,
        remind_days_before=remind_days_before,
        counterparty_id=counterparty_id,
        document_id=document_id,
        contract_id=contract_id,
        created_by=created_by,
    )


def due_reminders(db: Session, *, today: date | None = None) -> list[CalendarEvent]:
    """События, по которым пора отправить напоминание."""
    today = today or date.today()
    rows = db.scalars(
        select(CalendarEvent)
        .options(
            joinedload(CalendarEvent.organization),
            joinedload(CalendarEvent.counterparty),
        )
        .where(
            CalendarEvent.status == CalendarEventStatus.planned,
            CalendarEvent.remind_days_before.is_not(None),
            CalendarEvent.reminded_at.is_(None),
        )
    ).unique().all()
    ready: list[CalendarEvent] = []
    for ev in rows:
        days = int(ev.remind_days_before or 0)
        remind_on = ev.due_on - timedelta(days=days)
        if remind_on <= today <= ev.due_on + timedelta(days=1):
            ready.append(ev)
    return ready


def mark_reminded(ev: CalendarEvent) -> None:
    ev.reminded_at = utcnow()
