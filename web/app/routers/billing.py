"""Вебхук Т-Кассы и служебные эндпойнты биллинга (W-11)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.billing.payments import apply_payment_notification
from app.billing.tbank import TBankError
from app.db import get_db
from app.deps import client_ip
from app.rate_limit import LoginRateLimiter
from app.services.audit import record_event
from app.services.ops import record_webhook_fail, record_webhook_ok

log = logging.getLogger("dok.billing.webhook")

router = APIRouter(prefix="/billing", tags=["billing"])

# Лимит вебхука: защита от флуда (банк тоже ретраит — запас большой)
webhook_limiter = LoginRateLimiter(limit=120, window_sec=60, name="billing_webhook")


@router.post("/webhook", response_class=PlainTextResponse)
async def tbank_webhook(request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    if webhook_limiter.is_blocked(ip):
        log.warning("webhook rate-limited ip=%s", ip)
        return PlainTextResponse("OK", status_code=200)

    try:
        payload = await request.json()
    except Exception:
        webhook_limiter.register_failure(ip)
        log.warning("webhook rejected reason=bad_json ip=%s", ip)
        record_webhook_fail("bad_json", ip=ip)
        record_event(
            db,
            type="billing_webhook_rejected",
            org_id=None,
            user_id=None,
            details={"reason": "bad_json", "ip": ip},
        )
        return PlainTextResponse("OK", status_code=200)

    if not isinstance(payload, dict):
        log.warning("webhook rejected reason=not_object ip=%s", ip)
        record_webhook_fail("not_object", ip=ip)
        record_event(
            db,
            type="billing_webhook_rejected",
            org_id=None,
            user_id=None,
            details={"reason": "not_object", "ip": ip},
        )
        return PlainTextResponse("OK", status_code=200)

    order_id = str(payload.get("OrderId") or "")
    payment_id = str(payload.get("PaymentId") or "")

    try:
        apply_payment_notification(db, payload)
        db.commit()
    except TBankError as exc:
        reason = str(exc)
        webhook_limiter.register_failure(ip)
        # HOTFIX: неизвестный OrderId / нет платежа — всегда OK банку (гасим ретраи).
        log.warning(
            "webhook rejected reason=%s order_id=%s payment_id=%s ip=%s",
            reason,
            order_id,
            payment_id,
            ip,
        )
        record_webhook_fail(reason, ip=ip)
        record_event(
            db,
            type="billing_webhook_rejected",
            org_id=None,
            user_id=None,
            details={
                "reason": reason,
                "ip": ip,
                "order_id": order_id,
                "payment_id": payment_id,
                "status": str(payload.get("Status") or ""),
            },
        )
        return PlainTextResponse("OK", status_code=200)
    except Exception:
        log.exception(
            "webhook processing error order_id=%s payment_id=%s ip=%s",
            order_id,
            payment_id,
            ip,
        )
        db.rollback()
        record_webhook_fail("processing_error", ip=ip)
        # 500 — банк повторит доставку
        return PlainTextResponse("ERROR", status_code=500)

    record_webhook_ok()
    return PlainTextResponse("OK", status_code=200)
