"""Кабинет: «Тариф и оплата» (W-12)."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing.payments import create_card_payment, reconcile_payment
from app.billing.settings_access import terminal_status
from app.billing.tbank import TBankError
from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, require_csrf, require_org_user
from app.models import (
    Payment,
    PaymentStatus,
    SubscriptionPeriod,
    Tariff,
    TariffCode,
)
from app.org_scope import get_org_for_user
from app.nav_context import cabinet_nav
from app.security import get_csrf_token
from app.services.billing import get_current_subscription, get_tariff
from app.services.cms import (
    format_price_rub,
    tariff_amount_kop,
    tariff_price_label,
    validate_promo_code,
)
from app.services.limits import usage_snapshot
from app.templating import templates

log = logging.getLogger("dok.billing")

router = APIRouter(prefix="/cabinet/billing", tags=["cabinet-billing"])


def _ctx(request: Request, user: CurrentUser, org, db, **extra):
    base = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": user,
        "org": org,
        "nav": cabinet_nav(db, user),
        "active": "billing",
        "flash_error": None,
        "flash_ok": None,
    }
    base.update(extra)
    return base


def _org_email(org) -> str:
    req = org.requisites or {}
    block = req.get("организация") if isinstance(req, dict) else None
    if isinstance(block, dict):
        return (block.get("email") or "").strip()
    return ""


def _billing_view_context(
    request: Request,
    user: CurrentUser,
    org,
    db: Session,
    *,
    flash_error: str | None = None,
    receipt_email_default: str | None = None,
):
    snap = usage_snapshot(db, org.id)
    sub = get_current_subscription(db, org.id)
    tariffs = db.scalars(
        select(Tariff).where(Tariff.is_active.is_(True)).order_by(Tariff.price_month_kop)
    ).all()
    payments = db.scalars(
        select(Payment)
        .where(Payment.org_id == org.id)
        .order_by(Payment.created_at.desc())
        .limit(50)
    ).all()
    status = terminal_status(db)
    return _ctx(
        request,
        user,
        org,
        db,
        snap=snap,
        sub=sub,
        tariffs=tariffs,
        payments=payments,
        recurrents_enabled=status.recurrents_enabled,
        terminal_ready=status.ready,
        payment_test_mode=status.test_mode and status.ready,
        receipt_email_default=receipt_email_default or _org_email(org) or user.email,
        tariff_price_label=tariff_price_label,
        flash_error=flash_error or request.query_params.get("error"),
    )


@router.get("/", response_class=HTMLResponse)
def billing_page(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/billing.html",
        context=_billing_view_context(request, user, org, db),
    )


@router.get("/promo-preview", response_class=HTMLResponse)
def promo_preview(
    tariff_code: str = "",
    period: str = "month",
    promo_code: str = "",
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    try:
        code = TariffCode(tariff_code)
        per = SubscriptionPeriod(period)
    except ValueError:
        return HTMLResponse('<p class="muted">Выберите тариф и период.</p>')
    tariff = get_tariff(db, code)
    if tariff is None or code == TariffCode.guest:
        return HTMLResponse('<p class="muted">Промокод доступен для платных тарифов.</p>')
    base = tariff_amount_kop(tariff, per)
    result = validate_promo_code(
        db,
        code=promo_code,
        tariff=tariff,
        period=per,
        base_amount_kop=base,
    )
    if not promo_code.strip():
        return HTMLResponse(f'<p class="muted">К оплате: {format_price_rub(base)}.</p>')
    if not result.ok:
        return HTMLResponse(f'<p class="alert alert-error">{result.message}</p>')
    body = (
        f'<p class="alert alert-ok">{result.message}: '
        f'−{format_price_rub(result.discount_kop)}, '
        f'к оплате {format_price_rub(result.final_amount_kop)}.</p>'
    )
    return HTMLResponse(body)


@router.post("/pay", response_class=HTMLResponse)
def billing_pay(
    request: Request,
    tariff_code: str = Form(...),
    period: str = Form(...),
    auto_renew: str | None = Form(None),
    receipt_email: str | None = Form(None),
    promo_code: str = Form(""),
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    org = get_org_for_user(db, user)
    try:
        code = TariffCode(tariff_code)
        per = SubscriptionPeriod(period)
    except ValueError as exc:
        log.warning("billing_pay: bad tariff/period %r %r", tariff_code, period)
        raise HTTPException(status_code=400, detail="Неверный тариф или период") from exc

    tariff = get_tariff(db, code)
    if tariff is None or code == TariffCode.guest:
        log.warning("billing_pay: unpaid tariff %s", tariff_code)
        raise HTTPException(status_code=400, detail="Этот тариф нельзя оплатить картой")

    amount = tariff_amount_kop(tariff, per)
    if amount <= 0:
        log.warning("billing_pay: zero amount tariff=%s period=%s", code, per)
        raise HTTPException(status_code=400, detail="Нулевая сумма")

    sub = get_current_subscription(db, org.id)
    if sub is None:
        log.warning("billing_pay: no subscription org=%s", org.id)
        raise HTTPException(status_code=400, detail="Нет подписки организации")

    # смена тарифа: обновляем tariff_id / period сразу при создании платежа
    sub.tariff_id = tariff.id
    sub.period = per

    email = (receipt_email or _org_email(org) or user.email).strip()
    want_renew = bool(auto_renew)
    try:
        pay, url = create_card_payment(
            db,
            org_id=org.id,
            subscription=sub,
            tariff=tariff,
            period=per,
            amount_kop=amount,
            email=email,
            auto_renew=want_renew,
            promo_code=promo_code,
        )
        db.commit()
    except TBankError as exc:
        db.rollback()
        log.error("billing_pay failed org=%s: %s", org.id, exc)
        # 303 на GET с текстом ошибки — чтобы не залипать на POST-ответе
        # со старым «кнопка disabled / не настроен» в истории браузера.
        from urllib.parse import quote

        return RedirectResponse(
            f"/cabinet/billing/?error={quote(str(exc)[:500])}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if not url:
        return RedirectResponse(
            f"/cabinet/billing/success?order_id={pay.id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/success", response_class=HTMLResponse)
def billing_success(
    request: Request,
    order_id: str = "",
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    pay = _payment_for_org(db, org.id, order_id)
    if pay and pay.status != PaymentStatus.confirmed and pay.tbank_payment_id:
        try:
            reconcile_payment(db, pay)
            db.commit()
        except TBankError:
            db.rollback()
        db.refresh(pay) if pay else None
    return templates.TemplateResponse(
        request=request,
        name="cabinet/billing_result.html",
        context=_ctx(request, user, org, db, ok=True,
            pay=pay,
            title="Оплата принята",
            hint="Если статус ещё не обновился — подождите минуту или обновите страницу.",
        ),
    )


@router.get("/fail", response_class=HTMLResponse)
def billing_fail(
    request: Request,
    order_id: str = "",
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    pay = _payment_for_org(db, org.id, order_id)
    if pay and pay.tbank_payment_id:
        try:
            reconcile_payment(db, pay)
            db.commit()
        except TBankError:
            db.rollback()
    return templates.TemplateResponse(
        request=request,
        name="cabinet/billing_result.html",
        context=_ctx(request, user, org, db, ok=False,
            pay=pay,
            title="Оплата не завершена",
            hint="Средства не списаны или платёж отклонён. Можно попробовать снова.",
        ),
    )


def _payment_for_org(db: Session, org_id: int, order_id: str) -> Payment | None:
    if not order_id:
        return None
    try:
        pid = uuid.UUID(order_id)
    except ValueError:
        return None
    pay = db.get(Payment, pid)
    if pay is None or pay.org_id != org_id:
        return None
    return pay
