"""Фоновые задачи: сверка GetState, автопродление, напоминания об окончании (W-11/W-14)."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app import db as dbmod
from app.billing.payments import (
    apply_payment_notification,
    create_card_payment,
    flag_incomplete_receipts,
    load_tbank_client,
    org_billing_email,
    payment_receipt_complete,
    reconcile_payment,
)
from app.billing.tbank import TBankError
from app.config import get_settings
from app.models import (
    Event,
    Payment,
    PaymentSettings,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
    utcnow,
)
from app.services.audit import record_event
from app.services.billing_mail import notify_autorenew_failed, notify_subscription_expiring

log = logging.getLogger("dok.billing.jobs")

EXPIRY_NOTICE_DAYS = (7, 1)


def reconcile_stale_payments(db: Session, *, older_than_min: int = 15) -> int:
    """Сверить платежи в промежуточных статусах и confirmed без чека (W-45/G-03)."""
    cutoff = utcnow() - timedelta(minutes=older_than_min)
    rows = list(
        db.scalars(
            select(Payment).where(
                Payment.status.in_([PaymentStatus.created, PaymentStatus.authorized]),
                Payment.tbank_payment_id.is_not(None),
                Payment.updated_at < cutoff,
            )
        ).all()
    )
    # Добираем чек только для недавних confirmed/refunded без receipt
    recent_cut = utcnow() - timedelta(days=7)
    need_receipt = db.scalars(
        select(Payment).where(
            Payment.status.in_(
                [
                    PaymentStatus.confirmed,
                    PaymentStatus.refunded,
                    PaymentStatus.partial_refund,
                ]
            ),
            Payment.tbank_payment_id.is_not(None),
            Payment.created_at >= recent_cut,
        )
    ).all()
    for pay in need_receipt:
        if not payment_receipt_complete(pay):
            rows.append(pay)

    done = 0
    for pay in rows:
        try:
            reconcile_payment(db, pay)
            done += 1
        except Exception:
            log.exception("GetState failed payment=%s", pay.id)
    if done:
        db.commit()
    from app.services.ops import write_marker

    write_marker("billing_reconcile", reconciled=done)
    try:
        flag_incomplete_receipts(db)
    except Exception:
        log.exception("flag_incomplete_receipts failed")
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

        from app.services.cms import tariff_amount_kop

        tariff = sub.tariff
        amount = tariff_amount_kop(tariff, sub.period)
        if amount <= 0:
            continue

        email = org_billing_email(db, sub.organization)
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


def notify_expiring_subscriptions(db: Session) -> int:
    """Письма за 7 и за 1 день до окончания (календарь Europe/Moscow), без автопродления."""
    from app.timeutil import moscow_day_bounds_utc

    now = utcnow()
    sent = 0
    for days in EXPIRY_NOTICE_DAYS:
        day_start, day_end = moscow_day_bounds_utc(days, now=now)
        subs = db.scalars(
            select(Subscription)
            .options(joinedload(Subscription.tariff), joinedload(Subscription.organization))
            .where(
                Subscription.auto_renew.is_(False),
                Subscription.status.in_([SubscriptionStatus.active, SubscriptionStatus.trial]),
                Subscription.ends_at >= day_start,
                Subscription.ends_at < day_end,
            )
        ).all()
        for sub in subs:
            if _expiry_notice_already_sent(db, sub.id, days):
                continue
            email = org_billing_email(db, sub.organization)
            ok = notify_subscription_expiring(
                to_addr=email,
                org=sub.organization,
                sub=sub,
                days_left=days,
            )
            record_event(
                db,
                type="billing_expiry_notice",
                org_id=sub.org_id,
                user_id=None,
                details={
                    "subscription_id": sub.id,
                    "days_left": days,
                    "email": email,
                    "sent": bool(ok),
                },
                commit=False,
            )
            sent += 1
    if sent:
        db.commit()
    return sent


def _expiry_notice_already_sent(db: Session, subscription_id: int, days_left: int) -> bool:
    cutoff = utcnow() - timedelta(days=2)
    rows = db.scalars(
        select(Event).where(
            Event.type == "billing_expiry_notice",
            Event.ts >= cutoff,
        )
    ).all()
    for row in rows:
        details = row.details or {}
        if details.get("subscription_id") == subscription_id and details.get("days_left") == days_left:
            return True
    return False


def _notify_renewal_failed(db: Session, sub: Subscription) -> None:
    email = org_billing_email(db, sub.organization)
    notify_autorenew_failed(to_addr=email, org=sub.organization)
    record_event(
        db,
        type="billing_autorenew_exhausted",
        org_id=sub.org_id,
        user_id=None,
        details={"subscription_id": sub.id},
        commit=False,
    )


def _run_daily_jobs() -> None:
    """Синхронные суточные задачи (вызывать через asyncio.to_thread)."""
    from app.services.ops import write_marker

    db = dbmod.SessionLocal()
    try:
        n = process_autorenewals(db)
        if n:
            log.info("Autorenew started %s", n)
        n_mail = notify_expiring_subscriptions(db)
        if n_mail:
            log.info("Expiry notices sent %s", n_mail)
        from app.services.calendar_reminders import process_calendar_reminders

        n_cal = process_calendar_reminders(db)
        if n_cal:
            log.info("Calendar reminders sent %s", n_cal)
        from app.services.self_serve_cleanup import process_unpaid_guest_cleanup

        cleanup = process_unpaid_guest_cleanup(db)
        if cleanup.get("warned") or cleanup.get("deactivated"):
            log.info("Self-serve cleanup: %s", cleanup)
        watch_stats = None
        if os.environ.get("LEGAL_WATCH_WORKER", "1") != "0":
            from app.services.legal_monitor import run_daily_watch

            watch_stats = run_daily_watch(db)
            log.info("Legal watch: %s", watch_stats)
            write_marker("legal_watch", stats=watch_stats or {})
        write_marker(
            "billing_daily",
            autorenew=n,
            expiry_notices=n_mail,
            calendar_reminders=n_cal,
            self_serve_cleanup=cleanup,
        )
    finally:
        db.close()


async def billing_background_loop(stop: asyncio.Event) -> None:
    """Каждые 60 с: раз в 30 мин сверка, раз в сутки автопродление и напоминания."""
    # Сразу отдать управление lifespan — иначе create_task блокирует старт
    # синхронным HTTP мониторинга НПА до первого await.
    await asyncio.sleep(2)
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
                await asyncio.to_thread(_run_daily_jobs)
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
