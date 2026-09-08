"""Подтверждение e-mail (W-47, неблокирующее)."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import EmailVerificationToken, User, utcnow
from app.services.audit import record_event
from app.services.mail import send_email

VERIFY_TTL_HOURS = 72


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_and_send_verification(
    db: Session,
    *,
    user: User,
    settings: Settings | None = None,
    commit: bool = True,
) -> bool:
    """Создать токен и отправить письмо. Возвращает True, если письмо ушло."""
    settings = settings or get_settings()
    if user.email_verified:
        return False
    raw = secrets.token_urlsafe(32)
    row = EmailVerificationToken(
        user_id=user.id,
        token_hash=_hash_token(raw),
        expires_at=utcnow() + timedelta(hours=VERIFY_TTL_HOURS),
    )
    db.add(row)
    record_event(
        db,
        type="email_verification_sent",
        org_id=user.org_id,
        user_id=user.id,
        details={},
        commit=False,
    )
    if commit:
        db.commit()
    else:
        db.flush()

    base = settings.app_base_url.rstrip("/")
    link = f"{base}/verify-email/{raw}"
    return send_email(
        settings,
        to_addr=user.email,
        subject="[Док.Москва] Подтвердите e-mail",
        body=(
            f"Здравствуйте.\n\n"
            f"Подтвердите адрес {user.email} для кабинета Док.Москва.\n"
            f"Ссылка действует {VERIFY_TTL_HOURS} ч.:\n\n"
            f"{link}\n\n"
            f"Без подтверждения недоступны сброс пароля и приглашение сотрудников; "
            f"оплата работает.\n"
        ),
    )


def confirm_email_token(db: Session, raw_token: str) -> User | None:
    """Подтвердить e-mail по токену. None — недействителен."""
    row = db.scalar(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == _hash_token(raw_token)
        )
    )
    if row is None or row.is_used or row.is_expired():
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    user.email_verified = True
    row.used_at = utcnow()
    record_event(
        db,
        type="email_verified",
        org_id=user.org_id,
        user_id=user.id,
        details={},
        commit=False,
    )
    db.commit()
    db.refresh(user)
    return user
