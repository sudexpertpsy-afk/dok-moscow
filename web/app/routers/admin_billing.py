"""Админка: платёжная система и учёт платежей (W-13)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from io import BytesIO

from fastapi import APIRouter, Depends, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from openpyxl import Workbook
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.billing.crypto import encrypt_secret
from app.billing.payments import (
    load_tbank_client,
    org_billing_email,
    period_delta,
    reconcile_payment,
)
from app.billing.tbank import TBankError
from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, require_csrf, require_service_admin
from app.models import (
    Organization,
    Payment,
    PaymentMode,
    PaymentSettings,
    PaymentSource,
    PaymentStatus,
    Subscription,
    SubscriptionPeriod,
    SubscriptionStatus,
    utcnow,
)
from app.routers.admin import _ctx
from app.services.audit import record_event
from app.services.billing import ensure_tariffs, get_tariff, transition_subscription
from app.services.billing_mail import notify_manual_extend
from app.templating import templates

router = APIRouter(prefix="/admin", tags=["admin-billing"])


def _bctx(request, user, active, **extra):
    return _ctx(request, user, active, **extra)


@router.get("/payment-settings", response_class=HTMLResponse)
def payment_settings_page(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    row = db.get(PaymentSettings, 1)
    return templates.TemplateResponse(
        request=request,
        name="admin/payment_settings.html",
        context=_bctx(
            request,
            user,
            "paysettings",
            settings_row=row,
            password_set=bool(row and row.password_encrypted),
        ),
    )


@router.post("/payment-settings", response_class=HTMLResponse)
def payment_settings_save(
    request: Request,
    terminal_key: str = Form(""),
    password: str = Form(""),
    mode: str = Form("test"),
    recurrents_enabled: str | None = Form(None),
    taxation: str = Form("usn_income"),
    vat_rate: str = Form("none"),
    default_receipt_email: str = Form(""),
    party_check_daily_limit: str = Form("100"),
    require_2fa_for_org_admins: str | None = Form(None),
    yandex_login_enabled: str | None = Form(None),
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    row = db.get(PaymentSettings, 1)
    if row is None:
        row = PaymentSettings(id=1)
        db.add(row)
    row.terminal_key = terminal_key.strip() or None
    if password.strip():
        row.password_encrypted = encrypt_secret(password.strip())
    try:
        row.mode = PaymentMode(mode)
    except ValueError:
        row.mode = PaymentMode.test
    row.recurrents_enabled = bool(recurrents_enabled)
    row.taxation = taxation.strip() or "usn_income"
    row.vat_rate = vat_rate.strip() or "none"
    row.default_receipt_email = default_receipt_email.strip() or None
    try:
        limit = int(str(party_check_daily_limit).strip() or "100")
    except ValueError:
        limit = 100
    row.party_check_daily_limit = max(1, min(limit, 10_000))
    row.require_2fa_for_org_admins = bool(require_2fa_for_org_admins)
    row.yandex_login_enabled = bool(yandex_login_enabled)
    row.updated_by_user_id = user.id
    record_event(
        db,
        type="payment_settings_changed",
        org_id=None,
        user_id=user.id,
        details={
            "mode": row.mode.value,
            "terminal_set": bool(row.terminal_key),
            "password_updated": bool(password.strip()),
            "recurrents": row.recurrents_enabled,
            "party_check_daily_limit": row.party_check_daily_limit,
            "require_2fa_for_org_admins": row.require_2fa_for_org_admins,
            "yandex_login_enabled": row.yandex_login_enabled,
        },
        commit=False,
    )
    db.commit()
    return RedirectResponse("/admin/payment-settings?ok=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/payment-settings/test", response_class=HTMLResponse)
def payment_settings_test(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    row = db.get(PaymentSettings, 1)
    flash_ok = None
    flash_error = None
    try:
        client = load_tbank_client(db)
        try:
            init = client.init(
                amount_kop=1000,
                order_id=f"probe-{uuid.uuid4()}",
                description="Проверка подключения Док.Москва",
                notification_url=f"{get_settings().app_base_url.rstrip('/')}/billing/webhook",
                success_url=f"{get_settings().app_base_url.rstrip('/')}/admin/payment-settings",
                fail_url=f"{get_settings().app_base_url.rstrip('/')}/admin/payment-settings",
            )
            pid = str(init.get("PaymentId") or "")
            if pid:
                client.cancel(pid)
            flash_ok = f"Подключение успешно (PaymentId={pid or '—'}, затем Cancel)."
            if row and row.mode == PaymentMode.live:
                flash_ok += " Внимание: сейчас режим Бой."
        finally:
            client.close()
        record_event(
            db,
            type="payment_settings_probe",
            org_id=None,
            user_id=user.id,
            details={"ok": True},
        )
    except TBankError as exc:
        flash_error = f"Ошибка проверки: {exc}"
        record_event(
            db,
            type="payment_settings_probe",
            org_id=None,
            user_id=user.id,
            details={"ok": False, "error": str(exc)},
        )
    return templates.TemplateResponse(
        request=request,
        name="admin/payment_settings.html",
        context=_bctx(
            request,
            user,
            "paysettings",
            settings_row=row,
            password_set=bool(row and row.password_encrypted),
            flash_ok=flash_ok,
            flash_error=flash_error,
        ),
        status_code=200 if flash_ok else 400,
    )


def _month_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def _payments_summary(db: Session) -> dict:
    start, end = _month_bounds()
    # прошлый месяц
    prev_end = start
    if start.month == 1:
        prev_start = start.replace(year=start.year - 1, month=12)
    else:
        prev_start = start.replace(month=start.month - 1)

    def agg(a: datetime, b: datetime) -> dict:
        rows = db.scalars(
            select(Payment).where(
                Payment.status == PaymentStatus.confirmed,
                Payment.created_at >= a,
                Payment.created_at < b,
            )
        ).all()
        total = sum(p.amount_kop for p in rows)
        return {"count": len(rows), "sum_kop": total}

    cur = agg(start, end)
    prev = agg(prev_start, prev_end)
    active_subs = db.scalar(
        select(func.count()).select_from(Subscription).where(
            Subscription.status.in_([SubscriptionStatus.active, SubscriptionStatus.trial]),
            Subscription.ends_at > utcnow(),
        )
    ) or 0
    new_subs = db.scalar(
        select(func.count()).select_from(Subscription).where(
            Subscription.created_at >= start,
            Subscription.created_at < end,
            Subscription.is_beta.is_(False),
        )
    ) or 0
    # MRR: сумма month-эквивалентов активных платных
    mrr = 0
    for sub in db.scalars(
        select(Subscription)
        .options(joinedload(Subscription.tariff))
        .where(
            Subscription.status.in_([SubscriptionStatus.active, SubscriptionStatus.trial]),
            Subscription.ends_at > utcnow(),
            Subscription.is_beta.is_(False),
        )
    ).all():
        t = sub.tariff
        if sub.period == SubscriptionPeriod.year:
            mrr += t.price_year_kop // 12
        else:
            mrr += t.price_month_kop
    return {
        "month_sum_kop": cur["sum_kop"],
        "month_count": cur["count"],
        "prev_sum_kop": prev["sum_kop"],
        "prev_count": prev["count"],
        "active_subs": int(active_subs),
        "new_subs": int(new_subs),
        "mrr_kop": mrr,
    }


@router.get("/payments", response_class=HTMLResponse)
def payments_list(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    status_f: str = Query("", alias="status"),
    org_id: int | None = Query(None),
    q: str = Query(""),
):
    stmt = select(Payment).options(joinedload(Payment.organization)).order_by(Payment.created_at.desc())
    if status_f:
        try:
            stmt = stmt.where(Payment.status == PaymentStatus(status_f))
        except ValueError:
            pass
    if org_id:
        stmt = stmt.where(Payment.org_id == org_id)
    if q.strip():
        qq = q.strip()
        clauses = [Payment.purpose.ilike(f"%{qq}%"), Payment.tbank_payment_id.ilike(f"%{qq}%")]
        if qq.isdigit():
            clauses.append(Payment.amount_kop == int(qq))
            clauses.append(Payment.amount_kop == int(float(qq) * 100))
        try:
            clauses.append(Payment.id == uuid.UUID(qq))
        except ValueError:
            pass
        stmt = stmt.where(or_(*clauses))
    payments = db.scalars(stmt.limit(300)).all()
    orgs = db.scalars(select(Organization).order_by(Organization.name)).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/payments.html",
        context=_bctx(
            request,
            user,
            "payments",
            payments=payments,
            orgs=orgs,
            summary=_payments_summary(db),
            filter_status=status_f,
            filter_org=org_id,
            filter_q=q,
        ),
    )


@router.get("/payments/export")
def payments_export(
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    status_f: str = Query("", alias="status"),
    org_id: int | None = Query(None),
):
    stmt = select(Payment).options(joinedload(Payment.organization)).order_by(Payment.created_at.desc())
    if status_f:
        try:
            stmt = stmt.where(Payment.status == PaymentStatus(status_f))
        except ValueError:
            pass
    if org_id:
        stmt = stmt.where(Payment.org_id == org_id)
    rows = db.scalars(stmt.limit(5000)).all()
    wb = Workbook()
    ws = wb.active
    ws.title = "Платежи"
    ws.append(
        ["Дата", "Организация", "ИНН", "Сумма ₽", "Назначение", "PaymentId", "Статус", "Чек"]
    )
    for p in rows:
        inn = ""
        if p.organization and isinstance(p.organization.requisites, dict):
            inn = str((p.organization.requisites.get("организация") or {}).get("инн") or "")
        ws.append(
            [
                p.created_at.strftime("%Y-%m-%d %H:%M"),
                p.organization.name if p.organization else "",
                inn,
                p.amount_kop / 100,
                p.purpose,
                p.tbank_payment_id or "",
                p.status.value,
                p.receipt_status or "",
            ]
        )
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=dok-payments.xlsx"},
    )


@router.get("/payments/{payment_id}", response_class=HTMLResponse)
def payment_detail(
    payment_id: uuid.UUID,
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    pay = db.scalar(
        select(Payment)
        .options(joinedload(Payment.organization), joinedload(Payment.subscription))
        .where(Payment.id == payment_id)
    )
    if pay is None:
        return RedirectResponse("/admin/payments", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="admin/payment_detail.html",
        context=_bctx(request, user, "payments", pay=pay),
    )


@router.post("/payments/{payment_id}/reconcile")
def payment_reconcile(
    payment_id: uuid.UUID,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    pay = db.get(Payment, payment_id)
    if pay is None:
        return RedirectResponse("/admin/payments", status_code=303)
    try:
        reconcile_payment(db, pay)
        db.commit()
    except TBankError:
        db.rollback()
    return RedirectResponse(f"/admin/payments/{payment_id}", status_code=303)


@router.post("/payments/{payment_id}/mark-refund")
def payment_mark_refund(
    payment_id: uuid.UUID,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    pay = db.get(Payment, payment_id)
    if pay is None:
        return RedirectResponse("/admin/payments", status_code=303)
    pay.status = PaymentStatus.refunded
    pay.append_event({"event": "manual_refund", "by": user.email, "at": utcnow().isoformat()})
    record_event(
        db,
        type="billing_manual_refund",
        org_id=pay.org_id,
        user_id=user.id,
        details={"payment_id": str(pay.id)},
        commit=False,
    )
    db.commit()
    return RedirectResponse(f"/admin/payments/{payment_id}", status_code=303)


@router.post("/subscriptions/manual-extend", response_class=HTMLResponse)
def manual_extend(
    request: Request,
    org_id: int = Form(...),
    tariff_code: str = Form("specialist"),
    period: str = Form("month"),
    basis: str = Form(...),
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    from app.models import TariffCode
    from app.services.billing import get_current_subscription

    ensure_tariffs(db)
    org = db.get(Organization, org_id)
    if org is None or not basis.strip():
        return RedirectResponse("/admin/payments?err=1", status_code=303)
    try:
        code = TariffCode(tariff_code)
        per = SubscriptionPeriod(period)
    except ValueError:
        return RedirectResponse("/admin/payments?err=1", status_code=303)
    tariff = get_tariff(db, code)
    sub = get_current_subscription(db, org_id)
    if sub is None or tariff is None:
        return RedirectResponse("/admin/payments?err=1", status_code=303)

    # идемпотентность: тот же basis не создаёт второй платёж
    existing = db.scalar(
        select(Payment).where(
            Payment.org_id == org_id,
            Payment.source == PaymentSource.manual,
            Payment.manual_basis == basis.strip(),
        )
    )
    if existing:
        return RedirectResponse(f"/admin/payments/{existing.id}", status_code=303)

    amount = tariff.price_year_kop if per == SubscriptionPeriod.year else tariff.price_month_kop
    sub.tariff_id = tariff.id
    sub.period = per
    now = utcnow()
    base = sub.ends_at if sub.is_current(now) else now
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    sub.ends_at = base + period_delta(per)
    try:
        transition_subscription(sub, to=SubscriptionStatus.active, now=now)
    except ValueError:
        sub.mark_active()
    sub.is_beta = False
    pay = Payment(
        id=uuid.uuid4(),
        org_id=org_id,
        subscription_id=sub.id,
        amount_kop=amount,
        purpose=f"Ручное продление: {tariff.name}, {per.value}. Основание: {basis.strip()}",
        status=PaymentStatus.confirmed,
        source=PaymentSource.manual,
        manual_basis=basis.strip(),
        raw_events=[{"event": "manual_extend", "by": user.email}],
    )
    db.add(pay)
    record_event(
        db,
        type="billing_manual_extend",
        org_id=org_id,
        user_id=user.id,
        details={"payment_id": str(pay.id), "basis": basis.strip()},
        commit=False,
    )
    db.flush()
    notify_manual_extend(
        to_addr=org_billing_email(db, org),
        org=org,
        payment=pay,
        ends_at=sub.ends_at,
    )
    db.commit()
    return RedirectResponse(f"/admin/payments/{pay.id}", status_code=303)
