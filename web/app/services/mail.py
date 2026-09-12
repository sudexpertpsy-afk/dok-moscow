"""Общая отправка писем через SMTP (W-08/W-09)."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.config import Settings

log = logging.getLogger("dok.mail")


def send_email(
    settings: Settings,
    *,
    to_addr: str,
    subject: str,
    body: str,
) -> bool:
    """Отправить письмо. Без SMTP — лог и False."""
    to_addr = (to_addr or "").strip()
    if not to_addr:
        log.warning("Пустой получатель: %s", subject)
        return False
    if not settings.smtp_host:
        log.info("Письмо без SMTP: to=%s subject=%s\n%s", to_addr, subject, body)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or settings.smtp_user or to_addr
    msg["To"] = to_addr
    msg.set_content(body)

    try:
        if int(settings.smtp_port) == 465:
            # Implicit TLS (Яндекс 360 / многие провайдеры).
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
                if settings.smtp_use_tls:
                    smtp.starttls()
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)
        log.info("Письмо отправлено: to=%s subject=%s", to_addr, subject)
        return True
    except Exception:
        log.exception("Ошибка SMTP: to=%s subject=%s", to_addr, subject)
        return False
