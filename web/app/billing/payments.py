"""Создание платежей и применение статусов Т-Кассы (идемпотентно)."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.billing.crypto import decrypt_secret
from app.billing.tbank import (
    TBankClient,
    TBankConfig,
    TBankError,
    build_subscription_receipt,
    extract_receipt_fields,
    verify_token,
)
from app.config import get_settings
from app.models import (
    Organization,
    Payment,
    PaymentSettings,
    PaymentSource,
    PaymentStatus,
    Subscription,
    SubscriptionPeriod,
    SubscriptionStatus,
    Tariff,
    User,
    utcnow,
)
from app.services.audit import record_event
from app.services.billing import transition_subscription
from app.services.billing_mail import notify_payment_success
from app.services.cms import apply_promo_to_payment, validate_promo_code

log = logging.getLogger("dok.billing")

_TBANK_TO_STATUS = {
    "NEW": PaymentStatus.created,
    "FORM_SHOWING": PaymentStatus.created,
    "AUTHORIZING": PaymentStatus.authorized,
    "AUTHORIZED": PaymentStatus.authorized,
    "CONFIRMING": PaymentStatus.authorized,
    "CONFIRMED": PaymentStatus.confirmed,
    "REJECTED": PaymentStatus.rejected,
    "CANCELED": PaymentStatus.rejected,
    "CANCELLED": PaymentStatus.rejected,
    "DEADLINE_EXPIRED": PaymentStatus.rejected,
    "REFUNDED": PaymentStatus.refunded,
    "PARTIAL_REFUNDED": PaymentStatus.partial_refund,
    "REFUNDING": PaymentStatus.confirmed,
}


def period_delta(period: SubscriptionPeriod) -> timedelta:
    if period == SubscriptionPeriod.year:
        return timedelta(days=365)
    return timedelta(days=30)


def load_tbank_client(db: Session) -> TBankClient:
    settings_row = db.get(PaymentSettings, 1)
    if settings_row is None or not settings_row.terminal_key or not settings_row.password_encrypted:
        raise TBankError("Платёжная система не настроена (TerminalKey / пароль)")
    password = decrypt_secret(settings_row.password_encrypted)
    return TBankClient(
        TBankConfig(terminal_key=settings_row.terminal_key, password=password)
    )


def get_terminal_password(db: Session) -> str:
    row = db.get(PaymentSettings, 1)
    if row is None or not row.password_encrypted:
        raise TBankError("Пароль терминала не задан")
    return decrypt_secret(row.password_encrypted)


def create_card_payment(
    db: Session,
    *,
    org_id: int,
    subscription: Subscription,
    tariff: Tariff,
    period: SubscriptionPeriod,
    amount_kop: int,
    email: str,
    auto_renew: bool = False,
    promo_code: str | None = None,
) -> tuple[Payment, str]:
    purpose = f"Подписка Док.Москва, тариф {tariff.name}, период {'год' if period == SubscriptionPeriod.year else 'месяц'}"
    promo = validate_promo_code(
        db,
        code=promo_code,
        tariff=tariff,
        period=period,
        base_amount_kop=int(amount_kop),
    )
    if not promo.ok:
        raise TBankError(promo.message)
    pay = Payment(
        id=uuid.uuid4(),
        org_id=org_id,
        subscription_id=subscription.id,
        amount_kop=promo.final_amount_kop,
        amount_base_kop=promo.base_amount_kop,
        discount_kop=promo.discount_kop,
        purpose=purpose,
        status=PaymentStatus.created,
        source=PaymentSource.card,
        raw_events=[],
    )
    db.add(pay)
    apply_promo_to_payment(db, pay, promo)
    db.flush()

    app_settings = get_settings()
    pay_settings = db.get(PaymentSettings, 1)
    taxation = (pay_settings.taxation if pay_settings else None) or "usn_income"
    vat = (pay_settings.vat_rate if pay_settings else None) or "none"
    base = app_settings.app_base_url.rstrip("/")
    notification_url = f"{base}/billing/webhook"
    # На проде webhook может быть на dok.moscow — переопределение через PUBLIC
    # оставляем app_base; владелец укажет URL в ЛК банка.

    customer_key = subscription.customer_key or f"org-{org_id}"
    subscription.customer_key = customer_key
    if auto_renew:
        subscription.auto_renew = True

    client = load_tbank_client(db)
    try:
        resp = client.init(
            amount_kop=pay.amount_kop,
            order_id=str(pay.id),
            description=purpose,
            notification_url=notification_url,
            success_url=f"{base}/cabinet/billing/success?order_id={pay.id}",
            fail_url=f"{base}/cabinet/billing/fail?order_id={pay.id}",
            customer_key=customer_key,
            recurrent=bool(auto_renew and pay_settings and pay_settings.recurrents_enabled),
            email=email,
            receipt=build_subscription_receipt(
                email=email,
                taxation=taxation,
                amount_kop=pay.amount_kop,
                description=purpose,
                vat=vat,
            ),
        )
    finally:
        client.close()

    pay.tbank_payment_id = str(resp.get("PaymentId") or "") or None
    pay.append_event(
        {
            "event": "init",
            "email": email,
            "receipt": {
                "Email": email,
                "Taxation": taxation,
                "Tax": vat,
                "Name": purpose[:128],
                "BaseAmount": promo.base_amount_kop,
                "Discount": promo.discount_kop,
                "Amount": pay.amount_kop,
                "PaymentObject": "service",
                "PaymentMethod": "full_prepayment",
            },
            "promo": {
                "code": promo.code,
                "discount_kop": promo.discount_kop,
            } if promo.code else None,
            "response": {
                k: resp.get(k)
                for k in ("PaymentId", "Status", "PaymentURL", "OrderId", "Success", "ErrorCode")
            },
        }
    )
    db.flush()
    return pay, str(resp.get("PaymentURL") or "")


def map_tbank_status(status: str | None) -> PaymentStatus | None:
    if not status:
        return None
    return _TBANK_TO_STATUS.get(status.upper())


def apply_payment_notification(
    db: Session,
    payload: dict[str, Any],
    *,
    skip_token: bool = False,
) -> Payment:
    """Обработать Notification / GetState. Идемпотентно продлевает подписку при CONFIRMED."""
    if not skip_token:
        password = get_terminal_password(db)
        if not verify_token(payload, password):
            record_event(
                db,
                type="billing_webhook_rejected",
                org_id=None,
                user_id=None,
                details={"reason": "bad_token"},
                commit=False,
            )
            raise TBankError("Неверная подпись Token")

    order_id = str(payload.get("OrderId") or "")
    try:
        pay_uuid = uuid.UUID(order_id)
    except ValueError as exc:
        raise TBankError("Неизвестный OrderId") from exc

    pay = db.get(Payment, pay_uuid)
    if pay is None:
        raise TBankError("Платёж не найден")

    # Идемпотентность по сырому событию: тот же Status+PaymentId уже записан
    status_raw = str(payload.get("Status") or "")
    payment_id = str(payload.get("PaymentId") or "")
    fingerprint = f"{status_raw}:{payment_id}:{payload.get('Success')}"
    prev = pay.raw_events or []
    if any(isinstance(e, dict) and e.get("_fp") == fingerprint for e in prev):
        pay.append_event({"event": "notification_duplicate", "_fp": fingerprint, "Status": status_raw})
        db.flush()
        return pay

    event = dict(payload)
    event["_fp"] = fingerprint
    # Не храним полный PAN если вдруг пришёл — маска уже от банка
    pay.append_event(event)

    if payment_id:
        pay.tbank_payment_id = payment_id
    if payload.get("ErrorCode") not in (None, "", "0", 0):
        pay.error_code = str(payload.get("ErrorCode"))

    new_status = map_tbank_status(status_raw)
    already_confirmed = pay.status == PaymentStatus.confirmed

    if new_status is not None:
        # Не откатываем confirmed → authorized при повторных нотификациях
        if not (already_confirmed and new_status in (PaymentStatus.created, PaymentStatus.authorized)):
            if new_status == PaymentStatus.confirmed and already_confirmed:
                pass  # статус уже confirmed — ниже не продлеваем повторно
            else:
                pay.status = new_status

    # Чек 54-ФЗ до письма об оплате (чтобы в письмо попала ссылка)
    r_status, r_url = extract_receipt_fields(payload)
    if r_status:
        pay.receipt_status = r_status
    if r_url:
        pay.receipt_url = r_url

    if status_raw.upper() == "CONFIRMED" and not already_confirmed:
        _activate_subscription_for_payment(db, pay, payload)
        _notify_success_email(db, pay)

    # Возврат после подтверждения
    if new_status in (PaymentStatus.refunded, PaymentStatus.partial_refund):
        pay.status = new_status

    db.flush()
    return pay


def payment_payer_email(pay: Payment) -> str | None:
    for event in reversed(pay.raw_events or []):
        if isinstance(event, dict) and event.get("event") == "init" and event.get("email"):
            return str(event["email"]).strip() or None
    return None


def org_billing_email(db: Session, org: Organization) -> str:
    """E-mail для чеков/писем: реквизиты организации → первый пользователь → админ."""
    req = org.requisites or {}
    block = req.get("организация") if isinstance(req, dict) else None
    if isinstance(block, dict):
        email = (block.get("email") or "").strip()
        if email and "@" in email:
            return email
    user = db.scalar(
        select(User).where(User.org_id == org.id, User.is_active.is_(True)).order_by(User.id)
    )
    if user:
        return user.email
    settings = get_settings()
    return settings.admin_notify_email or settings.bootstrap_admin_email or "noreply@dok.moscow"


def _notify_success_email(db: Session, pay: Payment) -> None:
    org = db.get(Organization, pay.org_id)
    if org is None:
        return
    to_addr = payment_payer_email(pay) or org_billing_email(db, org)
    sub = db.get(Subscription, pay.subscription_id) if pay.subscription_id else None
    notify_payment_success(
        to_addr=to_addr,
        org=org,
        payment=pay,
        ends_at=sub.ends_at if sub else None,
    )


def _activate_subscription_for_payment(
    db: Session,
    pay: Payment,
    payload: dict[str, Any],
) -> None:
    sub = None
    if pay.subscription_id:
        sub = db.scalar(
            select(Subscription)
            .options(joinedload(Subscription.tariff))
            .where(Subscription.id == pay.subscription_id)
        )
    if sub is None:
        return

    now = utcnow()
    base = sub.ends_at
    if base.tzinfo is None:
        from datetime import timezone

        base = base.replace(tzinfo=timezone.utc)
    if not sub.is_current(now) or base < now:
        base = now
    sub.ends_at = base + period_delta(sub.period)
    if sub.status != SubscriptionStatus.active:
        try:
            transition_subscription(sub, to=SubscriptionStatus.active, now=now)
        except ValueError:
            sub.mark_active()
    sub.is_beta = False

    rebill = payload.get("RebillId")
    if rebill:
        sub.rebill_id = str(rebill)
    customer = payload.get("CustomerKey")
    if customer:
        sub.customer_key = str(customer)

    pay.status = PaymentStatus.confirmed
    record_event(
        db,
        type="billing_payment_confirmed",
        org_id=pay.org_id,
        user_id=None,
        details={
            "payment_id": str(pay.id),
            "tbank_payment_id": pay.tbank_payment_id,
            "amount_kop": pay.amount_kop,
            "subscription_id": sub.id,
            "ends_at": sub.ends_at.isoformat(),
        },
        commit=False,
    )


def apply_receipt_only_notification(db: Session, payload: dict[str, Any]) -> Payment | None:
    """Сохранить ссылку/статус чека, если нотификация пришла отдельно от смены Status."""
    r_status, r_url = extract_receipt_fields(payload)
    if not r_status and not r_url:
        return None
    order_id = str(payload.get("OrderId") or "")
    try:
        pay_uuid = uuid.UUID(order_id)
    except ValueError:
        return None
    pay = db.get(Payment, pay_uuid)
    if pay is None:
        return None
    if r_status:
        pay.receipt_status = r_status
    if r_url:
        pay.receipt_url = r_url
    pay.append_event({"event": "receipt_notification", "Status": r_status, "Url": r_url})
    db.flush()
    return pay


def reconcile_payment(db: Session, payment: Payment) -> Payment:
    """Сверка GetState для одного платежа."""
    if not payment.tbank_payment_id:
        return payment
    client = load_tbank_client(db)
    try:
        state = client.get_state(payment.tbank_payment_id)
    finally:
        client.close()
    # GetState не всегда содержит Token — применяем без проверки подписи ответа банка по HTTPS
    payload = {
        "OrderId": str(payment.id),
        "PaymentId": state.get("PaymentId") or payment.tbank_payment_id,
        "Status": state.get("Status"),
        "Success": state.get("Success"),
        "Amount": state.get("Amount"),
        "ErrorCode": state.get("ErrorCode"),
        "RebillId": state.get("RebillId"),
    }
    return apply_payment_notification(db, payload, skip_token=True)
