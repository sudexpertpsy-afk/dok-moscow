"""Кабинет: календарь, планирование, напоминания."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, require_csrf, require_org_user
from app.models import (
    CalendarEvent,
    CalendarEventKind,
    CalendarEventStatus,
    Counterparty,
)
from app.org_scope import get_org_for_user
from app.routers.cabinet import NAV
from app.security import get_csrf_token
from app.services.calendar_svc import (
    KIND_LABELS,
    STATUS_LABELS,
    create_event,
    month_grid,
    parse_ru_date,
    upcoming_and_overdue,
    update_event,
)
from app.templating import templates

router = APIRouter(prefix="/cabinet/calendar", tags=["calendar"])

_MONTH_NAMES = (
    "",
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)
_WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def _ctx(request: Request, user: CurrentUser, org, **extra):
    base = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": user,
        "org": org,
        "nav": NAV,
        "active": "calendar",
        "flash_error": None,
        "flash_ok": None,
        "kind_labels": KIND_LABELS,
        "status_labels": STATUS_LABELS,
        "weekdays": _WEEKDAYS,
    }
    base.update(extra)
    return base


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    m = month + delta
    y = year
    while m < 1:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return y, m


@router.get("/", response_class=HTMLResponse)
def calendar_home(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    year: int | None = Query(None),
    month: int | None = Query(None),
    day: str | None = Query(None),
):
    org = get_org_for_user(db, user)
    today = date.today()
    y = year or today.year
    m = month or today.month
    if not (1 <= m <= 12) or y < 2000 or y > 2100:
        y, m = today.year, today.month
    selected = parse_ru_date(day) if day else None
    if selected is None and day:
        try:
            selected = date.fromisoformat(day)
        except ValueError:
            selected = None

    weeks = month_grid(db, org.id, y, m)
    attention = upcoming_and_overdue(db, org.id, within_days=14, today=today)
    day_events = []
    if selected:
        day_events = [
            ev
            for week in weeks
            for cell in week
            if cell.day == selected
            for ev in cell.events
        ]
        if not day_events:
            from app.services.calendar_svc import events_in_range

            day_events = events_in_range(db, org.id, selected, selected)

    prev_y, prev_m = _shift_month(y, m, -1)
    next_y, next_m = _shift_month(y, m, 1)
    counterparties = db.scalars(
        select(Counterparty).where(Counterparty.org_id == org.id).order_by(Counterparty.id.desc()).limit(200)
    ).all()

    return templates.TemplateResponse(
        request=request,
        name="cabinet/calendar.html",
        context=_ctx(
            request,
            user,
            org,
            year=y,
            month=m,
            month_title=f"{_MONTH_NAMES[m]} {y}",
            weeks=weeks,
            today=today,
            selected=selected,
            day_events=day_events,
            attention=attention,
            prev_year=prev_y,
            prev_month=prev_m,
            next_year=next_y,
            next_month=next_m,
            counterparties=counterparties,
            kind_choices=list(CalendarEventKind),
            ok=request.query_params.get("ok"),
            flash_ok=(
                "Событие сохранено."
                if request.query_params.get("ok") == "1"
                else (
                    "Событие удалено."
                    if request.query_params.get("ok") == "del"
                    else (
                        "Статус обновлён."
                        if request.query_params.get("ok") == "done"
                        else None
                    )
                )
            ),
        ),
    )


@router.get("/new", response_class=HTMLResponse)
def calendar_new(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    day: str | None = Query(None),
):
    org = get_org_for_user(db, user)
    due = parse_ru_date(day) or date.today()
    if day and parse_ru_date(day) is None:
        try:
            due = date.fromisoformat(day)
        except ValueError:
            due = date.today()
    counterparties = db.scalars(
        select(Counterparty).where(Counterparty.org_id == org.id).order_by(Counterparty.id.desc()).limit(200)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="cabinet/calendar_event_form.html",
        context=_ctx(
            request,
            user,
            org,
            event=None,
            due_on=due.isoformat(),
            counterparties=counterparties,
            kind_choices=list(CalendarEventKind),
            status_choices=list(CalendarEventStatus),
        ),
    )


@router.post("/new", response_class=HTMLResponse)
def calendar_create(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    title: str = Form(...),
    due_on: str = Form(...),
    kind: str = Form("plan"),
    notes: str = Form(""),
    remind_days_before: str = Form("1"),
    counterparty_id: str = Form(""),
    _: None = Depends(require_csrf),
):
    org = get_org_for_user(db, user)
    due = parse_ru_date(due_on)
    if due is None:
        try:
            due = date.fromisoformat(due_on)
        except ValueError:
            return _form_error(request, user, org, db, "Укажите корректную дату", title, due_on, kind, notes, remind_days_before, counterparty_id)
    try:
        k = CalendarEventKind(kind)
    except ValueError:
        k = CalendarEventKind.plan
    remind = _parse_remind(remind_days_before)
    cp_id = int(counterparty_id) if counterparty_id.strip().isdigit() else None
    if cp_id:
        cp = db.get(Counterparty, cp_id)
        if cp is None or cp.org_id != org.id:
            cp_id = None
    try:
        create_event(
            db,
            org_id=org.id,
            title=title,
            due_on=due,
            kind=k,
            notes=notes,
            remind_days_before=remind,
            counterparty_id=cp_id,
            created_by=user.id,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _form_error(request, user, org, db, str(exc), title, due_on, kind, notes, remind_days_before, counterparty_id)
    return RedirectResponse(
        f"/cabinet/calendar/?year={due.year}&month={due.month}&day={due.isoformat()}&ok=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/{event_id}", response_class=HTMLResponse)
def calendar_edit(
    event_id: int,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    ev = db.get(CalendarEvent, event_id)
    if ev is None or ev.org_id != org.id:
        return RedirectResponse("/cabinet/calendar/", status_code=303)
    counterparties = db.scalars(
        select(Counterparty).where(Counterparty.org_id == org.id).order_by(Counterparty.id.desc()).limit(200)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="cabinet/calendar_event_form.html",
        context=_ctx(
            request,
            user,
            org,
            event=ev,
            due_on=ev.due_on.isoformat(),
            counterparties=counterparties,
            kind_choices=list(CalendarEventKind),
            status_choices=list(CalendarEventStatus),
        ),
    )


@router.post("/{event_id}", response_class=HTMLResponse)
def calendar_update(
    event_id: int,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    title: str = Form(...),
    due_on: str = Form(...),
    kind: str = Form("plan"),
    notes: str = Form(""),
    remind_days_before: str = Form(""),
    status_v: str = Form("planned"),
    counterparty_id: str = Form(""),
    _: None = Depends(require_csrf),
):
    org = get_org_for_user(db, user)
    ev = db.get(CalendarEvent, event_id)
    if ev is None or ev.org_id != org.id:
        return RedirectResponse("/cabinet/calendar/", status_code=303)
    due = parse_ru_date(due_on)
    if due is None:
        try:
            due = date.fromisoformat(due_on)
        except ValueError:
            return RedirectResponse(f"/cabinet/calendar/{event_id}?err=1", status_code=303)
    try:
        k = CalendarEventKind(kind)
        st = CalendarEventStatus(status_v)
    except ValueError:
        return RedirectResponse(f"/cabinet/calendar/{event_id}?err=1", status_code=303)
    cp_id = int(counterparty_id) if counterparty_id.strip().isdigit() else None
    if cp_id:
        cp = db.get(Counterparty, cp_id)
        if cp is None or cp.org_id != org.id:
            cp_id = None
    try:
        update_event(
            db,
            ev,
            title=title,
            due_on=due,
            kind=k,
            notes=notes,
            remind_days_before=_parse_remind(remind_days_before),
            status=st,
            counterparty_id=cp_id,
        )
        db.commit()
    except ValueError:
        db.rollback()
        return RedirectResponse(f"/cabinet/calendar/{event_id}?err=1", status_code=303)
    return RedirectResponse(
        f"/cabinet/calendar/?year={due.year}&month={due.month}&day={due.isoformat()}&ok=1",
        status_code=303,
    )


@router.post("/{event_id}/done", response_class=HTMLResponse)
def calendar_mark_done(
    event_id: int,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    org = get_org_for_user(db, user)
    ev = db.get(CalendarEvent, event_id)
    if ev is None or ev.org_id != org.id:
        return RedirectResponse("/cabinet/calendar/", status_code=303)
    ev.status = CalendarEventStatus.done
    db.commit()
    return RedirectResponse(
        f"/cabinet/calendar/?year={ev.due_on.year}&month={ev.due_on.month}&ok=done",
        status_code=303,
    )


@router.post("/{event_id}/delete", response_class=HTMLResponse)
def calendar_delete(
    event_id: int,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    org = get_org_for_user(db, user)
    ev = db.get(CalendarEvent, event_id)
    if ev is None or ev.org_id != org.id:
        return RedirectResponse("/cabinet/calendar/", status_code=303)
    y, m = ev.due_on.year, ev.due_on.month
    ev.status = CalendarEventStatus.cancelled
    db.commit()
    return RedirectResponse(f"/cabinet/calendar/?year={y}&month={m}&ok=del", status_code=303)


def _parse_remind(raw: str) -> int | None:
    text = (raw or "").strip()
    if text == "" or text.lower() in {"none", "нет", "-"}:
        return None
    try:
        return max(0, min(90, int(text)))
    except ValueError:
        return 1


def _form_error(request, user, org, db, message, title, due_on, kind, notes, remind, cp_id):
    counterparties = db.scalars(
        select(Counterparty).where(Counterparty.org_id == org.id).order_by(Counterparty.id.desc()).limit(200)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="cabinet/calendar_event_form.html",
        context=_ctx(
            request,
            user,
            org,
            event=None,
            due_on=due_on,
            counterparties=counterparties,
            kind_choices=list(CalendarEventKind),
            status_choices=list(CalendarEventStatus),
            flash_error=message,
            form_title=title,
            form_kind=kind,
            form_notes=notes,
            form_remind=remind,
            form_cp=cp_id,
        ),
        status_code=400,
    )
