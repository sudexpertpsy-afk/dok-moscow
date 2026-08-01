"""Раздел «Сотрудники» организации (W-27)."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, forbidden_org_admin_page, require_org_user
from app.models import TariffCode, User
from app.nav_context import cabinet_nav
from app.org_roles import is_org_admin
from app.org_scope import get_org_for_user
from app.security import check_csrf, get_csrf_token
from app.services.billing import get_tariff_limits
from app.services.limits import usage_snapshot
from app.services.staff import (
    create_org_invite,
    deactivate_staff,
    get_org_user,
    list_org_users,
    transfer_org_admin,
)
from app.templating import templates

router = APIRouter(prefix="/cabinet/staff", tags=["staff"])


def _staff_allowed(db: Session, org_id: int) -> tuple[bool, str | None]:
    lim = get_tariff_limits(db, org_id)
    if lim.tariff_code != TariffCode.organization or not lim.is_current:
        return False, "Раздел «Сотрудники» доступен на тарифе «Организация»."
    return True, None


def _page(request: Request, user: CurrentUser, org, db: Session, **extra):
    allowed, deny = _staff_allowed(db, org.id)
    users = list_org_users(db, org.id) if allowed else []
    snap = usage_snapshot(db, org.id) if allowed else None
    ctx = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": user,
        "org": org,
        "nav": cabinet_nav(db, user),
        "active": "staff",
        "allowed": allowed,
        "deny_reason": deny,
        "staff": users,
        "usage": snap,
        "flash_error": None,
        "flash_ok": None,
        "is_org_admin_fn": is_org_admin,
    }
    ctx.update(extra)
    return ctx


def _gate(request: Request, user: CurrentUser, db: Session):
    if not user.is_org_admin:
        return forbidden_org_admin_page(request, user, db)
    return None


@router.get("/", response_class=HTMLResponse)
def staff_home(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    denied = _gate(request, user, db)
    if denied is not None:
        return denied
    org = get_org_for_user(db, user)
    ok = request.query_params.get("ok")
    flash_ok = {
        "invite": "Приглашение отправлено.",
        "deactivate": "Сотрудник деактивирован.",
        "transfer": "Роль администратора передана.",
    }.get(ok or "")
    err = request.query_params.get("error")
    return templates.TemplateResponse(
        request=request,
        name="cabinet/staff.html",
        context=_page(request, user, org, db, flash_ok=flash_ok, flash_error=err),
    )


@router.post("/invite", response_class=HTMLResponse)
def staff_invite(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    email: str = Form(""),
    csrf_token: str = Form(""),
):
    denied = _gate(request, user, db)
    if denied is not None:
        return denied
    if not check_csrf(request, csrf_token):
        return RedirectResponse("/cabinet/staff/?error=CSRF", status_code=303)
    org = get_org_for_user(db, user)
    allowed, deny = _staff_allowed(db, org.id)
    if not allowed:
        return RedirectResponse(
            f"/cabinet/billing/?error={quote(deny or 'Нужен тариф Организация')}",
            status_code=303,
        )
    actor = db.get(User, user.id)
    assert actor is not None
    try:
        create_org_invite(
            db,
            org=org,
            email=email,
            invited_by=actor,
            app_base_url=str(request.base_url).rstrip("/"),
        )
    except HTTPException as exc:
        detail = str(exc.detail)
        if exc.status_code == 403:
            return RedirectResponse(
                f"/cabinet/billing/?error={quote(detail)}",
                status_code=303,
            )
        return templates.TemplateResponse(
            request=request,
            name="cabinet/staff.html",
            context=_page(request, user, org, db, flash_error=detail),
            status_code=400,
        )
    return RedirectResponse("/cabinet/staff/?ok=invite", status_code=303)


@router.post("/{user_id}/deactivate", response_class=HTMLResponse)
def staff_deactivate(
    user_id: int,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    csrf_token: str = Form(""),
):
    denied = _gate(request, user, db)
    if denied is not None:
        return denied
    if not check_csrf(request, csrf_token):
        return RedirectResponse("/cabinet/staff/?error=CSRF", status_code=303)
    org = get_org_for_user(db, user)
    actor = db.get(User, user.id)
    target = get_org_user(db, org.id, user_id)
    assert actor is not None
    try:
        deactivate_staff(db, org=org, actor=actor, target=target)
    except HTTPException as exc:
        return RedirectResponse(
            f"/cabinet/staff/?error={quote(str(exc.detail))}",
            status_code=303,
        )
    return RedirectResponse("/cabinet/staff/?ok=deactivate", status_code=303)


@router.post("/{user_id}/transfer-admin", response_class=HTMLResponse)
def staff_transfer(
    user_id: int,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    csrf_token: str = Form(""),
    confirm: str = Form(""),
):
    denied = _gate(request, user, db)
    if denied is not None:
        return denied
    if not check_csrf(request, csrf_token):
        return RedirectResponse("/cabinet/staff/?error=CSRF", status_code=303)
    org = get_org_for_user(db, user)
    actor = db.get(User, user.id)
    target = get_org_user(db, org.id, user_id)
    assert actor is not None
    try:
        transfer_org_admin(
            db,
            org=org,
            actor=actor,
            target=target,
            confirm=confirm == "1",
        )
    except HTTPException as exc:
        return RedirectResponse(
            f"/cabinet/staff/?error={quote(str(exc.detail))}",
            status_code=303,
        )
    return RedirectResponse("/cabinet/staff/?ok=transfer", status_code=303)
