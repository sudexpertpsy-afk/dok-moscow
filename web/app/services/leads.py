"""Заявки с лендинга: сохранение и уведомление администратору."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Lead
from app.services.mail import send_email


def create_lead(
    db: Session,
    *,
    email: str,
    profile: str | None,
    comment: str | None,
) -> Lead:
    lead = Lead(
        email=email.strip().lower(),
        profile=(profile or "").strip()[:255] or None,
        comment=(comment or "").strip() or None,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


def notify_admin_new_lead(settings: Settings, lead: Lead) -> bool:
    """Отправить письмо админу. Без SMTP — запись в лог (dev/тест)."""
    to_addr = (settings.admin_notify_email or settings.bootstrap_admin_email or "").strip()
    subject = f"[Док.Москва] Заявка на ранний доступ: {lead.email}"
    body = (
        f"Новая заявка с лендинга.\n\n"
        f"E-mail: {lead.email}\n"
        f"Профиль: {lead.profile or '—'}\n"
        f"Комментарий: {lead.comment or '—'}\n"
        f"ID: {lead.id}\n"
        f"Время: {lead.ts}\n"
    )
    return send_email(settings, to_addr=to_addr, subject=subject, body=body)
