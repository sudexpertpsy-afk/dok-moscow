"""Заявки с лендинга: сохранение и уведомление администратору."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Lead

log = logging.getLogger("dok.leads")


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
    """Отправить письмо админу. Без SMTP — запись в лог (dev/тест). Возвращает True при отправке."""
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
    if not settings.smtp_host or not to_addr:
        log.info("Заявка #%s (уведомление без SMTP): %s\n%s", lead.id, subject, body)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or settings.smtp_user or to_addr
    msg["To"] = to_addr
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        log.info("Уведомление о заявке #%s отправлено на %s", lead.id, to_addr)
        return True
    except Exception:
        log.exception("Не удалось отправить уведомление о заявке #%s", lead.id)
        return False
