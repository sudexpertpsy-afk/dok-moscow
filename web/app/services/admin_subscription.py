"""W-38: административное управление подпиской организации."""

from __future__ import annotations

import calendar
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.billing.payments import org_billing_email, period_delta
from app.models import (
    Event,
    Organization,
    Payment,
    PaymentSource,
    PaymentStatus,
    Subscription,
    SubscriptionPeriod,
    SubscriptionStatus,
    Tariff,
    TariffCode,
    User,
    utcnow,
)
from app.services.audit import record_event
from app.services.billing import (
    ensure_tariffs,
    get_current_subscription,
    get_tariff,
    transition_subscription,
)
from app.services.billing_mail import notify_admin_subscription_change

REASON_CHOICES: tuple[tuple[str, str], ...] = (
    ("beta", "бета-доступ"),
    ("invoice", "оплата по счёту (№ п/п)"),
    ("partner", "партнёрство"),
    ("compensation", "компенсация"),
    ("other", "иное"),
)
REASON_LABELS = dict(REASON_CHOICES)

TermMode = Literal["absolute", "relative"]
Action = Literal["apply", "terminate"]


class AdminSubscriptionError(ValueError):
    """Ошибка валидации формы управления подпиской."""


@dataclass
class AdminSubscriptionResult:
    subscription: Subscription
    payment: Payment | None
    ends_at: datetime | None
    action: Action
    notified: bool


def add_months(dt: datetime, months: int) -> datetime:
    """Календарное +N месяцев с усечением дня."""
    if months < 0:
        raise AdminSubscriptionError("Срок в месяцах должен быть ≥ 0")
    y = dt.year + (dt.month - 1 + months) // 12
    m = (dt.month - 1 + months) % 12 + 1
    last = calendar.monthrange(y, m)[1]
    d = min(dt.day, last)
    return dt.replace(year=y, month=m, day=d)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def subscription_badge(sub: Subscription | None, *, now: datetime | None = None) -> dict:
    """Тариф / статус / бейдж для таблицы организаций."""
    now = now or utcnow()
    if sub is None or sub.tariff is None:
        return {
            "tariff_name": "—",
            "status": "нет",
            "ends_at": None,
            "badge": "none",
            "badge_label": "нет",
            "is_current": False,
        }
    from app.timeutil import moscow_calendar_days_left

    end = _aware(sub.ends_at)
    current = sub.is_current(now)
    # Бейдж «истекает» — по календарным дням МСК, не по UTC-суткам.
    days_left = float(moscow_calendar_days_left(end, now=now))
    if not current or sub.status == SubscriptionStatus.cancelled:
        badge, label = "expired", "истекла"
        if sub.status == SubscriptionStatus.cancelled and end > now:
            badge, label = "expired", "завершена"
    elif days_left <= 14:
        badge, label = "expiring", "истекает"
    else:
        badge, label = "active", "активна"
    return {
        "tariff_name": sub.tariff.name,
        "status": sub.status.value,
        "ends_at": end,
        "badge": badge,
        "badge_label": label,
        "is_current": current,
        "is_complimentary": bool(sub.is_complimentary),
        "is_beta": bool(sub.is_beta),
    }


def org_subscription_map(db: Session, org_ids: list[int]) -> dict[int, Subscription | None]:
    out: dict[int, Subscription | None] = {oid: None for oid in org_ids}
    for oid in org_ids:
        out[oid] = get_current_subscription(db, oid)
    return out


def subscription_history(db: Session, org_id: int, *, limit: int = 30) -> list[Event]:
    return list(
        db.scalars(
            select(Event)
            .where(
                Event.org_id == org_id,
                Event.type.in_(
                    (
                        "billing_admin_subscription_apply",
                        "billing_admin_subscription_terminate",
                        "billing_manual_extend",
                    )
                ),
            )
            .order_by(Event.id.desc())
            .limit(limit)
        ).all()
    )


def _ensure_subscription(db: Session, org: Organization, tariff: Tariff) -> Subscription:
    sub = get_current_subscription(db, org.id)
    now = utcnow()
    if sub is not None:
        return sub
    sub = Subscription(
        org_id=org.id,
        tariff_id=tariff.id,
        period=SubscriptionPeriod.month,
        starts_at=now,
        ends_at=now,
        status=SubscriptionStatus.expired,
        auto_renew=False,
        is_beta=False,
        is_complimentary=False,
    )
    db.add(sub)
    db.flush()
    return sub


def _resolve_ends_at(
    *,
    sub: Subscription,
    term_mode: TermMode,
    months: int | None,
    ends_on: date | None,
    now: datetime,
) -> datetime:
    from app.timeutil import end_of_moscow_day, to_moscow

    if term_mode == "absolute":
        if ends_on is None:
            raise AdminSubscriptionError("Укажите дату окончания")
        # W-45/G-02: конец календарного дня Europe/Moscow, не UTC 23:59:59
        end = end_of_moscow_day(ends_on)
        if end <= now:
            raise AdminSubscriptionError("Дата окончания должна быть в будущем")
        return end

    if months not in (1, 3, 6, 12):
        raise AdminSubscriptionError("Срок: +1 / +3 / +6 / +12 месяцев")
    base = _aware(sub.ends_at) if sub.is_current(now) else now
    if base < now:
        base = now
    extended = add_months(base, months)
    # Согласовать с бейджами/письмами: конец дня МСК даты окончания
    return end_of_moscow_day(to_moscow(extended).date())


def _months_between(start: datetime, end: datetime) -> float:
    return (_aware(end) - _aware(start)).total_seconds() / (86400 * 30.4375)


def extension_requires_totp(
    *,
    term_mode: TermMode,
    months: int | None,
    ends_at: datetime,
    base: datetime,
) -> bool:
    """TOTP нужен, если *добавляемый* срок > 12 мес. (не если итог уже далеко в будущем)."""
    if term_mode == "relative":
        return (months or 0) > 12
    return _months_between(base, ends_at) > 12.01


def apply_admin_subscription(
    db: Session,
    *,
    org: Organization,
    actor: User,
    action: Action,
    tariff_code: str,
    term_mode: TermMode = "relative",
    months: int | None = None,
    ends_on: date | None = None,
    reason: str = "other",
    reason_comment: str = "",
    notify: bool = False,
    confirm: str = "",
    totp_ok: bool = False,
) -> AdminSubscriptionResult:
    """Применить / завершить подписку. Вызывающий проверяет TOTP при необходимости."""
    if confirm.strip().upper() != "YES":
        raise AdminSubscriptionError("Нужно подтверждение (YES)")

    ensure_tariffs(db)
    now = utcnow()
    comment = (reason_comment or "").strip()

    if action == "terminate":
        if not totp_ok:
            raise AdminSubscriptionError("Для завершения подписки нужен код 2FA")
        sub = get_current_subscription(db, org.id)
        if sub is None:
            raise AdminSubscriptionError("У организации нет подписки")
        try:
            transition_subscription(sub, to=SubscriptionStatus.cancelled, now=now)
        except ValueError:
            sub.mark_cancelled()
        sub.ends_at = now
        sub.auto_renew = False
        sub.updated_at = now
        record_event(
            db,
            type="billing_admin_subscription_terminate",
            org_id=org.id,
            user_id=actor.id,
            details={
                "tariff_id": sub.tariff_id,
                "ends_at": now.isoformat(),
                "reason": reason,
                "comment": comment,
            },
            commit=False,
        )
        notified = False
        if notify:
            notified = notify_admin_subscription_change(
                to_addr=org_billing_email(db, org),
                org=org,
                tariff_name=sub.tariff.name if sub.tariff else "—",
                ends_at=now,
                terminated=True,
            )
        db.flush()
        return AdminSubscriptionResult(sub, None, now, "terminate", notified)

    if reason not in REASON_LABELS:
        raise AdminSubscriptionError("Укажите основание")
    if reason == "invoice" and not comment:
        raise AdminSubscriptionError("Для оплаты по счёту укажите № п/п в комментарии")
    if reason == "other" and not comment:
        raise AdminSubscriptionError("Для основания «иное» заполните комментарий")

    try:
        code = TariffCode(tariff_code)
    except ValueError as exc:
        raise AdminSubscriptionError("Неизвестный тариф") from exc
    tariff = get_tariff(db, code)
    if tariff is None:
        raise AdminSubscriptionError("Тариф не найден")

    sub = _ensure_subscription(db, org, tariff)
    base = _aware(sub.ends_at) if sub.is_current(now) else now
    if base < now:
        base = now
    ends_at = _resolve_ends_at(
        sub=sub, term_mode=term_mode, months=months, ends_on=ends_on, now=now
    )
    if extension_requires_totp(
        term_mode=term_mode, months=months, ends_at=ends_at, base=base
    ) and not totp_ok:
        raise AdminSubscriptionError(
            "Для продления больше чем на 12 месяцев нужен код 2FA"
        )

    # идемпотентность оплаты по счёту — как W-13
    payment: Payment | None = None
    if reason == "invoice":
        existing = db.scalar(
            select(Payment).where(
                Payment.org_id == org.id,
                Payment.source == PaymentSource.manual,
                Payment.manual_basis == comment,
            )
        )
        if existing is not None:
            raise AdminSubscriptionError(
                f"Платёж с основанием «{comment}» уже существует (id {existing.id})"
            )

    sub.tariff_id = tariff.id
    if months == 12 or (
        term_mode == "absolute" and (_aware(ends_at) - now) >= timedelta(days=360)
    ):
        sub.period = SubscriptionPeriod.year
    else:
        sub.period = SubscriptionPeriod.month
    if not sub.is_current(now):
        sub.starts_at = now
    sub.ends_at = ends_at
    try:
        transition_subscription(sub, to=SubscriptionStatus.active, now=now)
    except ValueError:
        sub.mark_active()

    complimentary = reason != "invoice"
    sub.is_complimentary = complimentary
    sub.is_beta = reason == "beta"
    if reason == "invoice":
        sub.is_beta = False
    sub.updated_at = now

    if reason == "invoice":
        amount = (
            tariff.price_year_kop
            if sub.period == SubscriptionPeriod.year
            else tariff.price_month_kop
        )
        payment = Payment(
            id=uuid.uuid4(),
            org_id=org.id,
            subscription_id=sub.id,
            amount_kop=amount,
            purpose=(
                f"Ручное продление: {tariff.name}, {sub.period.value}. "
                f"Основание: {comment}"
            ),
            status=PaymentStatus.confirmed,
            source=PaymentSource.manual,
            manual_basis=comment,
            raw_events=[{"event": "admin_subscription", "by": actor.email, "reason": reason}],
        )
        db.add(payment)
        db.flush()

    record_event(
        db,
        type="billing_admin_subscription_apply",
        org_id=org.id,
        user_id=actor.id,
        details={
            "tariff": code.value,
            "tariff_name": tariff.name,
            "ends_at": ends_at.isoformat(),
            "reason": reason,
            "reason_label": REASON_LABELS[reason],
            "comment": comment,
            "complimentary": complimentary,
            "payment_id": str(payment.id) if payment else None,
            "term_mode": term_mode,
            "months": months,
        },
        commit=False,
    )

    notified = False
    if notify:
        notified = notify_admin_subscription_change(
            to_addr=org_billing_email(db, org),
            org=org,
            tariff_name=tariff.name,
            ends_at=ends_at,
            terminated=False,
        )
    db.flush()
    return AdminSubscriptionResult(sub, payment, ends_at, "apply", notified)
