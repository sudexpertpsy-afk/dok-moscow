"""W-50 B.4/C.6: KPI админки с учётом is_internal."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Organization, Payment, PaymentStatus, utcnow


def payment_funnel_30d(db: Session, *, include_internal: bool) -> dict:
    """Платежи за 30 дней по статусам; rejected → cancelled; без is_internal по умолчанию."""
    since = utcnow() - timedelta(days=30)
    stmt = (
        select(Payment.status, func.count())
        .select_from(Payment)
        .join(Organization, Organization.id == Payment.org_id)
        .where(Payment.created_at >= since)
        .group_by(Payment.status)
    )
    if not include_internal:
        stmt = stmt.where(Organization.is_internal.is_(False))

    by_status: dict[str, int] = {row[0].value if hasattr(row[0], "value") else str(row[0]): int(row[1]) for row in db.execute(stmt).all()}

    created = int(by_status.get(PaymentStatus.created.value, 0))
    # authorized считаем как ещё «созданные» в воронке оплаты
    created += int(by_status.get(PaymentStatus.authorized.value, 0))
    confirmed = int(by_status.get(PaymentStatus.confirmed.value, 0))
    expired = int(by_status.get(PaymentStatus.expired.value, 0))
    cancelled = int(by_status.get(PaymentStatus.rejected.value, 0))

    denom = created + confirmed + expired + cancelled
    expired_share = round(100.0 * expired / denom, 1) if denom else 0.0
    return {
        "created": created,
        "confirmed": confirmed,
        "expired": expired,
        "cancelled": cancelled,
        "expired_share": expired_share,
    }
