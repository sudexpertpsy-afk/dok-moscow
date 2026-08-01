"""Ежедневная рассылка напоминаний календаря."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.billing.payments import org_billing_email
from app.services.audit import record_event
from app.services.calendar_mail import notify_calendar_reminder
from app.services.calendar_svc import due_reminders, mark_reminded

log = logging.getLogger("dok.calendar.reminders")


def process_calendar_reminders(db: Session) -> int:
    sent = 0
    for ev in due_reminders(db):
        try:
            email = org_billing_email(db, ev.organization)
            ok = notify_calendar_reminder(to_addr=email, event=ev)
            mark_reminded(ev)
            record_event(
                db,
                type="calendar_reminder",
                org_id=ev.org_id,
                user_id=None,
                details={
                    "event_id": ev.id,
                    "title": ev.title,
                    "due_on": ev.due_on.isoformat(),
                    "email": email,
                    "sent": bool(ok),
                },
                commit=False,
            )
            sent += 1
        except Exception:
            log.exception("calendar reminder failed event=%s", ev.id)
    if sent:
        db.commit()
    return sent
