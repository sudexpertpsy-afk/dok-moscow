"""Фоновые задачи: сверка GetState и автопродление подписок."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app import db as dbmod
from app.billing.payments import (
    apply_payment_notification,
    create_card_payment,
    load_tbank_client,
    reconcile_payment,
)
from app.billing.tbank import TBankError
from app.config import get_settings
from app.models import (
    Organization,
    Payment,
    PaymentSettings,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
    User,
    utcnow,
)
from app.services.audit import record_event
from app.services.mail import send_email

log = logging.getLogger("dok.billing.jobs")


def reconcile_stale_payments(db: Session, *, older_than_min: int = 15) -> int:
    """Сверить платежи в промежуточных статусах старше N минут."""
    cutoff = utcnow() - timedelta(minutes=older_than_min)
    rows = db.scalars(
        select(Payment).where(
            Payment.status.in_([PaymentStatus.created, PaymentStatus.authorized]),
            Payment.tbank_payment_id.is_not(None),
            Payment.updated_at < cutoff,
        )
    ).all()
    done = 0
    for pay in rows:
        try:
            reconcile_payment(db, pay)
            done += 1
        except Exception:
            log.exception("GetState failed payment=%s", pay.id)
    if done:
        db.commit()
    return done


def process_autorenewals(db: Session, *, within_days: int = 3) -> int:
    """Init+Charge для подписок с автопродлением за N дней до окончания."""
    settings_row = db.get(PaymentSettings, 1)
    if settings_row is None or not settings_row.recurrents_enabled:
        return 0
    if not settings_row.terminal_key or not settings_row.password_encrypted:
        return 0

    now = utcnow()
    horizon = now + timedelta(days=within_days)
    subs = db.scalars(
        select(Subscription)
        .options(joinedload(Subscription.tariff), joinedload(Subscription.organization))
        .where(
            Subscription.auto_renew.is_(True),
            Subscription.rebill_id.is_not(None),
            Subscription.status.in_([SubscriptionStatus.active, SubscriptionStatus.trial]),
            Subscription.ends_at <= horizon,
            Subscription.ends_at > now - timedelta(days=1),
        )
    ).all()

    started = 0
    for sub in subs:
        recent = db.scalars(
            select(Payment).where(
                Payment.subscription_id == sub.id,
                Payment.created_at >= now - timedelta(days=4),
                Payment.purpose.contains("автопродление"),
            )
        ).all()
        if any(p.status == PaymentStatus.confirmed for p in recent):
            continue
        failures = [
            p for p in recent if p.status in (PaymentStatus.rejected, PaymentStatus.created)
        ]
        if len(failures) >= 3:
            _notify_renewal_failed(db, sub)
            continue
        if failures:
            last = max(failures, key=lambda p: p.created_at)
            if last.created_at > now - timedelta(hours=20):
                continue

        tariff = sub.tariff
        amount = (
            tariff.price_year_kop if sub.period.value == "year" else tariff.price_month_kop
        )
        if amount <= 0:
            continue

        email = _org_billing_email(db, sub.organization)
        try:
            pay, _url = create_card_payment(
                db,
                org_id=sub.org_id,
                subscription=sub,
                tariff=tariff,
                period=sub.period,
                amount_kop=amount,
                email=email,
                auto_renew=True,
            )
            pay.purpose = "автопродление " + pay.purpose
            if not pay.tbank_payment_id or not sub.rebill_id:
                pay.status = PaymentStatus.rejected
                pay.error_code = "no_rebill"
                db.flush()
                continue
            client = load_tbank_client(db)
            try:
                charge_resp = client.charge(
                    payment_id=pay.tbank_payment_id, rebill_id=sub.rebill_id
                )
            finally:
                client.close()
            pay.append_event({"event": "charge", "Status": charge_resp.get("Status")})
            apply_payment_notification(
                db,
                {
                    "OrderId": str(pay.id),
                    "PaymentId": charge_resp.get("PaymentId") or pay.tbank_payment_id,
                    "Status": charge_resp.get("Status"),
                    "Success": charge_resp.get("Success"),
                    "Amount": charge_resp.get("Amount") or pay.amount_kop,
                    "ErrorCode": charge_resp.get("ErrorCode"),
                    "RebillId": sub.rebill_id,
                },
                skip_token=True,
            )
            started += 1
        except TBankError as exc:
            log.warning("Autorenew failed org=%s: %s", sub.org_id, exc)
            record_event(
                db,
                type="billing_autorenew_failed",
                org_id=sub.org_id,
                user_id=None,
                details={"error": str(exc), "code": exc.code},
                commit=False,
            )
        except Exception:
            log.exception("Autorenew exception org=%s", sub.org_id)

    expired = db.scalars(
        select(Subscription).where(
            Subscription.status.in_([SubscriptionStatus.active, SubscriptionStatus.trial]),
            Subscription.ends_at < now,
        )
    ).all()
    for sub in expired:
        sub.mark_expired()

    db.commit()
    return started


def _org_billing_email(db: Session, org: Organization) -> str:
    user = db.scalar(
        select(User).where(User.org_id == org.id, User.is_active.is_(True)).order_by(User.id)
    )
    if user:
        return user.email
    settings = get_settings()
    return settings.admin_notify_email or settings.bootstrap_admin_email or "noreply@dok.moscow"


def _notify_renewal_failed(db: Session, sub: Subscription) -> None:
    settings = get_settings()
    email = _org_billing_email(db, sub.organization)
    send_email(
        settings,
        to_addr=email,
        subject="[Док.Москва] Не удалось продлить подписку",
        body=(
            f"Автоматическое списание для «{sub.organization.name}» не удалось после 3 попыток.\n"
            f"Продлите подписку вручную в кабинете: "
            f"{settings.app_base_url.rstrip('/')}/cabinet/billing/\n"
        ),
    )
    record_event(
        db,
        type="billing_autorenew_exhausted",
        org_id=sub.org_id,
        user_id=None,
        details={"subscription_id": sub.id},
        commit=False,
    )


async def billing_background_loop(stop: asyncio.Event) -> None:
    """Каждые 60 с: раз в 30 мин сверка, раз в сутки автопродление."""
    last_reconcile = 0.0
    last_renew_day = ""
    while not stop.is_set():
        try:
            loop = asyncio.get_running_loop()
            now_mono = loop.time()
            if now_mono - last_reconcile >= 30 * 60:
                db = dbmod.SessionLocal()
                try:
                    n = reconcile_stale_payments(db)
                    if n:
                        log.info("Reconciled %s payments", n)
                finally:
                    db.close()
                last_reconcile = now_mono

            day = utcnow().strftime("%Y-%m-%d")
            if day != last_renew_day:
                db = dbmod.SessionLocal()
                try:
                    n = process_autorenewals(db)
                    if n:
                        log.info("Autorenew started %s", n)
                finally:
                    db.close()
                last_renew_day = day
        except Exception:
            log.exception("billing background loop error")
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except TimeoutError:
            pass


def start_billing_worker() -> asyncio.Event:
    stop = asyncio.Event()

    async def _runner() -> None:
        await billing_background_loop(stop)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_runner(), name="billing-worker")
    except RuntimeError:
        log.warning("No running loop — billing worker not started")
    return stop
