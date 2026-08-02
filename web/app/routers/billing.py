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
        record_webhook_fail("bad_json")
        record_event(
            db,
            type="billing_webhook_rejected",
            org_id=None,
            user_id=None,
            details={"reason": "bad_json", "ip": ip},
        )
        return PlainTextResponse("OK", status_code=200)

    if not isinstance(payload, dict):
        record_webhook_fail("not_object")
        return PlainTextResponse("OK", status_code=200)

    try:
        apply_payment_notification(db, payload)
        db.commit()
    except TBankError as exc:
        webhook_limiter.register_failure(ip)
        log.info("webhook rejected: %s", exc)
        record_webhook_fail(str(exc))
        record_event(
            db,
            type="billing_webhook_rejected",
            org_id=None,
            user_id=None,
            details={
                "reason": str(exc),
                "ip": ip,
                "order_id": str(payload.get("OrderId") or ""),
                "status": str(payload.get("Status") or ""),
            },
        )
        # Банку всё равно отвечаем OK на неизвестный OrderId после проверки Token?
        # При неверном Token — тоже OK, чтобы не усиливать ретраи с неверным секретом.
        return PlainTextResponse("OK", status_code=200)
    except Exception:
        log.exception("webhook processing error")
        db.rollback()
        record_webhook_fail("processing_error")
        # 500 — банк повторит доставку
        return PlainTextResponse("ERROR", status_code=500)

    record_webhook_ok()
    return PlainTextResponse("OK", status_code=200)
