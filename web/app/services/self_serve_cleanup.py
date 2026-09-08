"""Анти-абьюз self-serve: деактивация пустых guest-кабинетов (W-47)."""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import (
    Document,
    Event,
    Lead,
    LeadStatus,
    Organization,
    Payment,
    PaymentStatus,
    Subscription,
    Tariff,
    TariffCode,
    User,
    utcnow,
)
from app.services.audit import record_event
from app.services.mail import send_email

log = logging.getLogger("dok.self_serve_cleanup")

INACTIVE_DAYS = 14
WARN_DAYS_BEFORE = 3  # письмо за 3 дня до деактивации


def _guest_org_ids(db: Session) -> list[int]:
    return list(
        db.scalars(
            select(Subscription.org_id)
            .join(Tariff, Tariff.id == Subscription.tariff_id)
            .where(Tariff.code == TariffCode.guest)
        ).all()
    )


def _has_confirmed_payment(db: Session, org_id: int) -> bool:
    n = db.scalar(
        select(func.count())
        .select_from(Payment)
        .where(Payment.org_id == org_id, Payment.status == PaymentStatus.confirmed)
    )
    return int(n or 0) > 0


def _has_documents(db: Session, org_id: int) -> bool:
    n = db.scalar(
        select(func.count()).select_from(Document).where(Document.org_id == org_id)
    )
    return int(n or 0) > 0


def _org_admin(db: Session, org_id: int) -> User | None:
    return db.scalar(
        select(User)
        .where(User.org_id == org_id, User.is_active.is_(True))
        .order_by(User.id.asc())
        .limit(1)
    )


def _event_recent(db: Session, org_id: int, event_type: str, *, within_days: int = 20) -> bool:
    cutoff = utcnow() - timedelta(days=within_days)
    rows = db.scalars(
        select(Event).where(Event.type == event_type, Event.org_id == org_id, Event.ts >= cutoff)
    ).all()
    return bool(rows)


def process_unpaid_guest_cleanup(db: Session, settings: Settings | None = None) -> dict[str, int]:
    """
    Организации guest без оплаты и документов, с неподтверждённым e-mail:
    — за 3 дня до порога 14 дней — предупреждение;
    — старше 14 дней — деактивация пользователей (не удаление).
    """
    settings = settings or get_settings()
    now = utcnow()
    warn_age = timedelta(days=INACTIVE_DAYS - WARN_DAYS_BEFORE)
    deactivate_age = timedelta(days=INACTIVE_DAYS)
    warned = 0
    deactivated = 0

    for org_id in _guest_org_ids(db):
        org = db.get(Organization, org_id)
        if org is None:
            continue
        if _has_confirmed_payment(db, org_id) or _has_documents(db, org_id):
            continue
        admin = _org_admin(db, org_id)
        if admin is None or admin.email_verified:
            continue
        age = now - (org.created_at if org.created_at.tzinfo else org.created_at.replace(tzinfo=now.tzinfo))
        if age < warn_age:
            continue

        if age < deactivate_age:
            if _event_recent(db, org_id, "self_serve_deactivate_warn"):
                continue
            send_email(
                settings,
                to_addr=admin.email,
                subject="[Док.Москва] Кабинет будет деактивирован",
                body=(
                    f"Здравствуйте.\n\n"
                    f"Кабинет «{org.name}» без оплаты и документов будет "
                    f"деактивирован через {WARN_DAYS_BEFORE} дн., если e-mail "
                    f"не подтверждён и не появится активность.\n\n"
                    f"Войдите в кабинет и подтвердите e-mail или оформите оплату.\n"
                ),
            )
            record_event(
                db,
                type="self_serve_deactivate_warn",
                org_id=org_id,
                user_id=admin.id,
                details={"email": admin.email},
                commit=False,
            )
            warned += 1
            continue

        users = list(db.scalars(select(User).where(User.org_id == org_id, User.is_active.is_(True))))
        if not users:
            continue
        for u in users:
            u.is_active = False
        lead = db.scalar(select(Lead).where(Lead.org_id == org_id).order_by(Lead.id.desc()).limit(1))
        if lead is not None and lead.status == LeadStatus.signed_up:
            # статус не меняем на rejected — просто деактивировали
            pass
        send_email(
            settings,
            to_addr=admin.email,
            subject="[Док.Москва] Кабинет деактивирован",
            body=(
                f"Здравствуйте.\n\n"
                f"Кабинет «{org.name}» деактивирован: нет оплаты, документов "
                f"и подтверждения e-mail более {INACTIVE_DAYS} дней.\n"
                f"Данные сохранены. Для восстановления напишите на поддержку.\n"
            ),
        )
        record_event(
            db,
            type="self_serve_deactivated",
            org_id=org_id,
            user_id=admin.id,
            details={"email": admin.email, "users": len(users)},
            commit=False,
        )
        deactivated += 1

    if warned or deactivated:
        db.commit()
    log.info("self_serve cleanup: warned=%s deactivated=%s", warned, deactivated)
    return {"warned": warned, "deactivated": deactivated}
