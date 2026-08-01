"""Лимиты тарифа (W-12): документы/мес, пользователи, водяной знак, генерация."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Document, User, utcnow
from app.services.billing import TariffLimits, get_tariff_limits


@dataclass(frozen=True)
class UsageSnapshot:
    limits: TariffLimits
    documents_this_month: int
    users_active: int
    can_generate: bool
    block_reason: str | None


def _month_start(now: datetime | None = None) -> datetime:
    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def count_documents_month(db: Session, org_id: int, now: datetime | None = None) -> int:
    start = _month_start(now)
    return int(
        db.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.org_id == org_id, Document.created_at >= start)
        )
        or 0
    )


def count_active_users(db: Session, org_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.org_id == org_id, User.is_active.is_(True))
        )
        or 0
    )


def usage_snapshot(db: Session, org_id: int) -> UsageSnapshot:
    limits = get_tariff_limits(db, org_id)
    docs = count_documents_month(db, org_id)
    users = count_active_users(db, org_id)

    reason = None
    can = True
    if not limits.is_current:
        can = False
        reason = "Подписка неактивна или истекла. Оплатите тариф, чтобы создавать документы."
    elif (
        limits.limit_documents_month is not None
        and docs >= limits.limit_documents_month
    ):
        can = False
        reason = (
            f"Достигнут лимит тарифа «{limits.tariff_name}»: "
            f"{limits.limit_documents_month} документов в месяц."
        )

    return UsageSnapshot(
        limits=limits,
        documents_this_month=docs,
        users_active=users,
        can_generate=can,
        block_reason=reason,
    )


def assert_can_generate(db: Session, org_id: int) -> UsageSnapshot:
    from app.services.billing import ensure_beta_subscriptions

    ensure_beta_subscriptions(db)
    snap = usage_snapshot(db, org_id)
    if not snap.can_generate:
        from urllib.parse import quote

        q = quote(snap.block_reason or "Требуется оплата")
        dest = f"/cabinet/billing/?error={q}"
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail=snap.block_reason or "Требуется оплата",
            headers={"Location": dest, "HX-Redirect": dest},
        )
    return snap


def assert_can_add_user(db: Session, org_id: int) -> None:
    from app.services.billing import ensure_beta_subscriptions

    # Организация могла быть создана до сида подписки — досоздаём beta-trial
    ensure_beta_subscriptions(db)
    snap = usage_snapshot(db, org_id)
    lim = snap.limits.limit_users
    if lim is not None and snap.users_active >= lim:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Тариф «{snap.limits.tariff_name}» допускает не более {lim} "
                f"пользовател(я/ей). Смените тариф в разделе «Тариф и оплата»."
            ),
        )


def needs_watermark(db: Session, org_id: int) -> bool:
    return bool(get_tariff_limits(db, org_id).watermark)
