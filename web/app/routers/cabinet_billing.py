"""Кабинет: «Тариф и оплата» (W-12)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing.payments import create_card_payment, reconcile_payment
from app.billing.tbank import TBankError
from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, require_csrf, require_org_user
from app.models import (
    Payment,
    PaymentSettings,
    PaymentStatus,
    SubscriptionPeriod,
    Tariff,
    TariffCode,
)
from app.org_scope import get_org_for_user
from app.routers.cabinet import NAV
from app.security import get_csrf_token
from app.services.billing import get_current_subscription, get_tariff
from app.services.limits import usage_snapshot
from app.templating import templates

router = APIRouter(prefix="/cabinet/billing", tags=["cabinet-billing"])


def _ctx(request: Request, user: CurrentUser, org, **extra):
    base = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": user,
        "org": org,
        "nav": NAV,
        "active": "billing",
        "flash_error": None,
        "flash_ok": None,
    }
    base.update(extra)
    return base


@router.get("/", response_class=HTMLResponse)
def billing_page(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
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
    pay_settings = db.get(PaymentSettings, 1)
    error = request.query_params.get("error")
    return templates.TemplateResponse(
        request=request,
        name="cabinet/billing.html",
        context=_ctx(
            request,
            user,
            org,
            snap=snap,
            sub=sub,
            tariffs=tariffs,
            payments=payments,
            recurrents_enabled=bool(pay_settings and pay_settings.recurrents_enabled),
            terminal_ready=bool(
                pay_settings and pay_settings.terminal_key and pay_settings.password_encrypted
            ),
            flash_error=error,
        ),
    )


@router.post("/pay", response_class=HTMLResponse)
def billing_pay(
    request: Request,
    tariff_code: str = Form(...),
    period: str = Form(...),
    auto_renew: str | None = Form(None),
    receipt_email: str | None = Form(None),
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    org = get_org_for_user(db, user)
    try:
        code = TariffCode(tariff_code)
        per = SubscriptionPeriod(period)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Неверный тариф или период") from exc

    tariff = get_tariff(db, code)
    if tariff is None or code == TariffCode.guest:
        raise HTTPException(status_code=400, detail="Этот тариф нельзя оплатить картой")

    amount = tariff.price_year_kop if per == SubscriptionPeriod.year else tariff.price_month_kop
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Нулевая сумма")

    sub = get_current_subscription(db, org.id)
    if sub is None:
        raise HTTPException(status_code=400, detail="Нет подписки организации")

    # смена тарифа: обновляем tariff_id / period сразу при создании платежа
    sub.tariff_id = tariff.id
    sub.period = per

    email = (receipt_email or user.email).strip()
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
        )
        db.commit()
    except TBankError as exc:
        db.rollback()
        snap = usage_snapshot(db, org.id)
        return templates.TemplateResponse(
            request=request,
            name="cabinet/billing.html",
            context=_ctx(
                request,
                user,
                org,
                snap=snap,
                sub=get_current_subscription(db, org.id),
                tariffs=db.scalars(select(Tariff).where(Tariff.is_active.is_(True))).all(),
                payments=db.scalars(
                    select(Payment).where(Payment.org_id == org.id).order_by(Payment.created_at.desc()).limit(50)
                ).all(),
                recurrents_enabled=False,
                terminal_ready=False,
                flash_error=str(exc),
            ),
            status_code=400,
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
        context=_ctx(
            request,
            user,
            org,
            ok=True,
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
        context=_ctx(
            request,
            user,
            org,
            ok=False,
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
