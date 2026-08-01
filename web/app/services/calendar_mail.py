"""Письма-напоминания календаря."""

from __future__ import annotations

from datetime import date

from app.config import Settings, get_settings
from app.models import CalendarEvent
from app.services.calendar_svc import KIND_LABELS
from app.services.mail import send_email


def notify_calendar_reminder(
    *,
    to_addr: str,
    event: CalendarEvent,
    settings: Settings | None = None,
) -> bool:
    settings = settings or get_settings()
    kind = KIND_LABELS.get(event.kind, event.kind.value)
    due = event.due_on.strftime("%d.%m.%Y")
    today = date.today()
    if event.due_on < today:
        when = f"просрочено (срок был {due})"
    elif event.due_on == today:
        when = f"сегодня ({due})"
    else:
        when = f"{due}"
    cp = ""
    if event.counterparty:
        name = event.counterparty.name or event.counterparty.fio or ""
        if name:
            cp = f"Контрагент: {name}\n"
    notes = f"\n{event.notes}\n" if event.notes else ""
    body = (
        f"Напоминание из календаря «{settings.app_name}».\n\n"
        f"{event.title}\n"
        f"Тип: {kind}\n"
        f"Дата: {when}\n"
        f"{cp}"
        f"{notes}\n"
        f"Календарь: {settings.app_base_url.rstrip('/')}/cabinet/calendar/\n"
    )
    return send_email(
        settings,
        to_addr=to_addr,
        subject=f"[{settings.app_name}] Напоминание: {event.title[:80]}",
        body=body,
    )
