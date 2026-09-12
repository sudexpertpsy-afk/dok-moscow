"""W-50 B.5: мягкая политика 2FA для org_admin платных организаций."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import CurrentUser
from app.models import (
    OrgRole,
    PaymentSettings,
    Subscription,
    SubscriptionStatus,
    Tariff,
    TariffCode,
    User,
    UserRole,
    utcnow,
)
from app.services.audit import record_event
from app.services.billing import get_current_subscription
from app.totp_2fa import is_org_admin_subject

GRACE_DAYS = 7
MEMBER_REMIND_DAYS = 30
MEMBER_DISMISS_COOKIE = "dok_2fa_member_dismiss"

PAID_TARIFF_CODES = frozenset({TariffCode.specialist, TariffCode.organization})

# Чувствительные маршруты: после дедлайна без TOTP — редирект на security
PATHS_REQUIRING_2FA = (
    "/cabinet/staff",
    "/cabinet/settings/numbering",
    "/cabinet/billing",
    "/cabinet/settings/data",
)

TwoFaStatus = Literal["ok", "grace", "blocked", "n/a"]


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        from datetime import timezone

        return dt.replace(tzinfo=timezone.utc)
    return dt


def policy_enabled(db: Session) -> bool:
    row = db.get(PaymentSettings, 1)
    return bool(row and row.two_fa_policy_enabled)


def policy_enabled_at(db: Session) -> datetime | None:
    row = db.get(PaymentSettings, 1)
    if row is None:
        return None
    return _aware(row.two_fa_policy_enabled_at)


def paid_subscription_started_at(db: Session, org_id: int) -> datetime | None:
    """Начало текущей платной (не guest/beta complimentary) подписки."""
    sub = get_current_subscription(db, org_id)
    if sub is None or sub.tariff is None:
        return None
    if sub.tariff.code not in PAID_TARIFF_CODES:
        return None
    if sub.is_complimentary or sub.is_beta:
        return None
    if not sub.is_current():
        return None
    return _aware(sub.starts_at)


def org_admin_deadline(db: Session, user: User | CurrentUser) -> datetime | None:
    """Дедлайн включения 2FA: max(paid_start, policy_enabled_at) + 7 дней."""
    if not policy_enabled(db):
        return None
    if not is_org_admin_subject(user):  # type: ignore[arg-type]
        return None
    org_id = getattr(user, "org_id", None)
    if org_id is None:
        return None
    paid_start = paid_subscription_started_at(db, int(org_id))
    if paid_start is None:
        return None
    enabled_at = policy_enabled_at(db)
    if enabled_at is None:
        base = paid_start
    else:
        base = max(paid_start, enabled_at)
    return base + timedelta(days=GRACE_DAYS)


def org_admin_2fa_status(db: Session, user: User | CurrentUser) -> TwoFaStatus:
    if getattr(user, "totp_enabled", False):
        return "ok"
    deadline = org_admin_deadline(db, user)
    if deadline is None:
        return "n/a"
    if utcnow() < deadline:
        return "grace"
    return "blocked"


def path_requires_2fa(path: str) -> bool:
    p = (path or "").rstrip("/") or "/"
    for prefix in PATHS_REQUIRING_2FA:
        pref = prefix.rstrip("/")
        if p == pref or p.startswith(pref + "/"):
            return True
    return False


def enforce_soft_2fa(
    request: Request,
    user: User | CurrentUser,
    db: Session,
) -> RedirectResponse | None:
    """
    Если status=blocked и путь чувствительный — редирект на security.
    Событие security.2fa_policy_block — один раз за сессию.
    """
    if not path_requires_2fa(request.url.path):
        return None
    # Страница настройки 2FA не блокируем
    if request.url.path.startswith("/cabinet/settings/security"):
        return None
    if getattr(user, "totp_enabled", False):
        return None
    if org_admin_2fa_status(db, user) != "blocked":
        return None

    if not request.session.get("2fa_policy_block_logged"):
        uid = int(user.id)
        oid = user.org_id
        record_event(
            db,
            type="security.2fa_policy_block",
            org_id=int(oid) if oid is not None else None,
            user_id=uid,
            details={"path": request.url.path},
            commit=True,
        )
        request.session["2fa_policy_block_logged"] = True

    loc = "/cabinet/settings/security?msg=" + quote("Включите 2FA для доступа к этому разделу")
    return RedirectResponse(loc, status_code=303)


def grace_days_left(db: Session, user: User | CurrentUser) -> int | None:
    deadline = org_admin_deadline(db, user)
    if deadline is None:
        return None
    delta = deadline - utcnow()
    days = int(delta.total_seconds() // 86400)
    return max(0, days)


def member_reminder_due(request: Request, user: User | CurrentUser) -> bool:
    """org_member без 2FA — напоминание раз в 30 дней (cookie dismiss)."""
    if getattr(user, "totp_enabled", False):
        return False
    from app.org_roles import is_org_member

    # CurrentUser / User
    if not is_org_member(user):  # type: ignore[arg-type]
        return False
    raw = request.cookies.get(MEMBER_DISMISS_COOKIE)
    if not raw:
        return True
    try:
        dismissed_ts = float(raw)
    except ValueError:
        return True
    age = utcnow().timestamp() - dismissed_ts
    return age >= MEMBER_REMIND_DAYS * 86400


def soft_2fa_banner_context(
    request: Request, db: Session, user: User | CurrentUser
) -> dict:
    """Контекст баннеров для layout кабинета."""
    status = org_admin_2fa_status(db, user)
    days = grace_days_left(db, user) if status == "grace" else None
    return {
        "two_fa_status": status,
        "two_fa_grace_days": days,
        "two_fa_show_grace_banner": status == "grace",
        "two_fa_show_member_reminder": member_reminder_due(request, user),
        "two_fa_security_url": "/cabinet/settings/security",
    }


def attach_soft_2fa_request_state(
    request: Request, db: Session, user: CurrentUser
) -> None:
    request.state.soft_2fa = soft_2fa_banner_context(request, db, user)


def two_fa_adoption_stats(db: Session) -> dict:
    """Доля 2FA: org_admin платных org и все пользователи (для /admin/status)."""
    # org_admins платных активных подписок
    paid_org_ids = list(
        db.scalars(
            select(Subscription.org_id)
            .join(Tariff, Tariff.id == Subscription.tariff_id)
            .where(
                Tariff.code.in_(list(PAID_TARIFF_CODES)),
                Subscription.status.in_(
                    [SubscriptionStatus.active, SubscriptionStatus.trial]
                ),
                Subscription.is_complimentary.is_(False),
                Subscription.is_beta.is_(False),
            )
            .distinct()
        ).all()
    )
    # только текущие (не истёкшие) — фильтр в Python по is_current дорого;
    # берём org с get_current_subscription paid
    paid_current: set[int] = set()
    for oid in paid_org_ids:
        if paid_subscription_started_at(db, int(oid)) is not None:
            paid_current.add(int(oid))

    admins = list(
        db.scalars(
            select(User).where(
                User.org_id.in_(paid_current) if paid_current else False,
                User.role == UserRole.user,
                User.org_role == OrgRole.org_admin,
                User.is_active.is_(True),
            )
        ).all()
    ) if paid_current else []
    admins_n = len(admins)
    admins_2fa = sum(1 for u in admins if u.totp_enabled)

    all_users_n = int(
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.is_active.is_(True), User.role == UserRole.user)
        )
        or 0
    )
    all_2fa = int(
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.is_active.is_(True),
                User.role == UserRole.user,
                User.totp_enabled.is_(True),
            )
        )
        or 0
    )

    def _pct(num: int, den: int) -> float | None:
        if den <= 0:
            return None
        return round(100.0 * num / den, 1)

    return {
        "paid_org_admins_total": admins_n,
        "paid_org_admins_2fa": admins_2fa,
        "paid_org_admins_pct": _pct(admins_2fa, admins_n),
        "all_users_total": all_users_n,
        "all_users_2fa": all_2fa,
        "all_users_pct": _pct(all_2fa, all_users_n),
    }
