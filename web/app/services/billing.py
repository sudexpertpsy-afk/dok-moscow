"""Биллинг W-10: справочник тарифов, подписки, лимиты из БД."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.models import (
    Organization,
    PaymentMode,
    PaymentSettings,
    Subscription,
    SubscriptionPeriod,
    SubscriptionStatus,
    Tariff,
    TariffCode,
    utcnow,
)

# Цены в копейках — единственный источник для сида; рантайм читает из БД.
# Канонические цены (коп.): месяц → год −10%, 2 года −20% от месячной базы.
YEAR_PREPAY_DISCOUNT_PCT = 10
YEARS2_PREPAY_DISCOUNT_PCT = 20


def year_amount_from_month_kop(month_kop: int) -> int:
    return int(month_kop) * 12 * (100 - YEAR_PREPAY_DISCOUNT_PCT) // 100


def years2_amount_from_month_kop(month_kop: int) -> int:
    return int(month_kop) * 24 * (100 - YEARS2_PREPAY_DISCOUNT_PCT) // 100


DEFAULT_TARIFFS: tuple[dict, ...] = (
    {
        "code": TariffCode.guest,
        "name": "Гость",
        "price_month_kop": 0,
        "price_year_kop": 0,
        "limit_documents_month": 3,
        "limit_users": 1,
        "watermark": True,
        "blurb": "До 3 документов в месяц, водяной знак на PDF. Чтобы оценить кабинет без оплаты.",
        "features": ["до 3 документов в месяц", "1 пользователь", "водяной знак на PDF"],
    },
    {
        "code": TariffCode.specialist,
        "name": "Специалист",
        "price_month_kop": 25_000,
        "price_year_kop": year_amount_from_month_kop(25_000),
        "limit_documents_month": None,
        "limit_users": 1,
        "watermark": False,
        "blurb": "Один пользователь, полный кабинет без водяного знака.",
        "features": ["полный кабинет без водяного знака", "1 пользователь", "журналы, шаблоны, DaData"],
    },
    {
        "code": TariffCode.organization,
        "name": "Организация",
        "price_month_kop": 110_000,
        "price_year_kop": year_amount_from_month_kop(110_000),
        "limit_documents_month": None,
        "limit_users": 5,
        "watermark": False,
        "blurb": "До 5 пользователей, свои шаблоны.",
        "features": [
            "до 5 пользователей по приглашениям",
            "свои шаблоны организации",
            "приоритет для команд СРО и учреждений",
        ],
    },
)


@dataclass(frozen=True)
class TariffLimits:
    tariff_code: TariffCode
    tariff_name: str
    limit_documents_month: int | None
    limit_users: int | None
    watermark: bool
    subscription_status: SubscriptionStatus | None
    ends_at: datetime | None
    is_current: bool


def ensure_tariffs(db: Session) -> list[Tariff]:
    """Создать недостающие тарифы (идемпотентно).

    W-36: цены и описания правятся в админке — при повторном вызове
    существующие строки НЕ перезаписываются.
    """
    out: list[Tariff] = []
    for row in DEFAULT_TARIFFS:
        tariff = db.scalar(select(Tariff).where(Tariff.code == row["code"]))
        if tariff is None:
            data = dict(row)
            # опциональные поля лендинга (если колонки уже есть)
            if hasattr(Tariff, "blurb"):
                data.setdefault("blurb", "")
            if hasattr(Tariff, "features"):
                data.setdefault("features", [])
            tariff = Tariff(**data, is_active=True)
            db.add(tariff)
        out.append(tariff)
    db.flush()
    return out


def ensure_payment_settings(db: Session) -> PaymentSettings:
    row = db.get(PaymentSettings, 1)
    if row is None:
        row = PaymentSettings(
            id=1,
            mode=PaymentMode.test,
            two_fa_policy_enabled=True,
            two_fa_policy_enabled_at=utcnow(),
            purge_mode="dry",
            purge_mode_changed_at=utcnow(),
        )
        db.add(row)
        db.flush()
    else:
        if row.two_fa_policy_enabled and row.two_fa_policy_enabled_at is None:
            row.two_fa_policy_enabled_at = utcnow()
        if not getattr(row, "purge_mode", None):
            row.purge_mode = "dry"
        if getattr(row, "purge_mode_changed_at", None) is None and (row.purge_mode or "dry") == "dry":
            row.purge_mode_changed_at = utcnow()
        db.flush()
    return row


def beta_trial_ends_at() -> datetime:
    """Конец календарного дня BETA_TRIAL_UNTIL по Europe/Moscow (не 23:59 UTC)."""
    from app.timeutil import end_of_moscow_day

    settings = get_settings()
    return end_of_moscow_day(settings.beta_trial_until)


def ensure_beta_subscriptions(db: Session) -> int:
    """У организаций без подписки — trial «Организация» до BETA_TRIAL_UNTIL.

    На бете нужен запас по пользователям (до 5); после оплаты выбирают тариф сами.
    """
    ensure_tariffs(db)
    org_tariff = db.scalar(select(Tariff).where(Tariff.code == TariffCode.organization))
    assert org_tariff is not None
    ends = beta_trial_ends_at()
    starts = utcnow()
    created = 0
    orgs = db.scalars(select(Organization)).all()
    for org in orgs:
        existing = db.scalar(
            select(Subscription)
            .where(Subscription.org_id == org.id)
            .order_by(Subscription.id.desc())
            .limit(1)
        )
        if existing is not None:
            continue
        db.add(
            Subscription(
                org_id=org.id,
                tariff_id=org_tariff.id,
                period=SubscriptionPeriod.month,
                starts_at=starts,
                ends_at=ends,
                status=SubscriptionStatus.trial,
                auto_renew=False,
                is_beta=True,
            )
        )
        created += 1
    db.flush()
    return created


def bootstrap_billing(db: Session) -> None:
    ensure_tariffs(db)
    ensure_payment_settings(db)
    ensure_beta_subscriptions(db)
    db.commit()


def get_tariff(db: Session, code: TariffCode) -> Tariff | None:
    return db.scalar(select(Tariff).where(Tariff.code == code, Tariff.is_active.is_(True)))


def get_current_subscription(db: Session, org_id: int) -> Subscription | None:
    """Актуальная подписка: trial/active с неистёкшим сроком, иначе последняя."""
    rows = db.scalars(
        select(Subscription)
        .options(joinedload(Subscription.tariff))
        .where(Subscription.org_id == org_id)
        .order_by(Subscription.id.desc())
    ).all()
    for sub in rows:
        if sub.is_current():
            return sub
    return rows[0] if rows else None


def get_tariff_limits(db: Session, org_id: int) -> TariffLimits:
    """Лимиты тарифа из БД (не из кода). Без подписки — лимиты «Гость»."""
    sub = get_current_subscription(db, org_id)
    if sub is not None and sub.tariff is not None:
        t = sub.tariff
        return TariffLimits(
            tariff_code=t.code,
            tariff_name=t.name,
            limit_documents_month=t.limit_documents_month,
            limit_users=t.limit_users,
            watermark=t.watermark,
            subscription_status=sub.status,
            ends_at=sub.ends_at,
            is_current=sub.is_current(),
        )
    guest = get_tariff(db, TariffCode.guest)
    if guest is None:
        ensure_tariffs(db)
        guest = get_tariff(db, TariffCode.guest)
    assert guest is not None
    # Нет строки подписки — работаем как бесплатный «Гость» (лимиты активны)
    return TariffLimits(
        tariff_code=guest.code,
        tariff_name=guest.name,
        limit_documents_month=guest.limit_documents_month,
        limit_users=guest.limit_users,
        watermark=guest.watermark,
        subscription_status=SubscriptionStatus.trial,
        ends_at=None,
        is_current=True,
    )


def transition_subscription(
    sub: Subscription,
    *,
    to: SubscriptionStatus,
    now: datetime | None = None,
) -> None:
    """Допустимые переходы статусов подписки."""
    allowed = {
        SubscriptionStatus.trial: {
            SubscriptionStatus.active,
            SubscriptionStatus.expired,
            SubscriptionStatus.cancelled,
        },
        SubscriptionStatus.active: {
            SubscriptionStatus.expired,
            SubscriptionStatus.cancelled,
            SubscriptionStatus.active,
        },
        SubscriptionStatus.expired: {SubscriptionStatus.active, SubscriptionStatus.trial},
        SubscriptionStatus.cancelled: {SubscriptionStatus.active, SubscriptionStatus.trial},
    }
    if to not in allowed[sub.status]:
        raise ValueError(f"Переход {sub.status.value} → {to.value} запрещён")
    now = now or utcnow()
    if to == SubscriptionStatus.active:
        sub.mark_active()
    elif to == SubscriptionStatus.expired:
        sub.mark_expired()
    elif to == SubscriptionStatus.cancelled:
        sub.mark_cancelled()
    elif to == SubscriptionStatus.trial:
        sub.status = SubscriptionStatus.trial
    sub.updated_at = now
