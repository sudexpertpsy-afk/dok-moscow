"""Кабинет организации (заглушки разделов W-01)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, assert_same_org, require_org_user
from app.models import Organization, TariffCode
from app.nav_context import cabinet_nav
from app.navigation import cabinet_menu_tuples, roles_for_user
from app.security import check_csrf, get_csrf_token
from app.templating import templates

router = APIRouter(prefix="/cabinet", tags=["cabinet"])

# Обратная совместимость импортов: статический снимок меню (полный доступ).
NAV = cabinet_menu_tuples(
    roles=roles_for_user(is_service_admin=False, has_org=True, is_org_admin=True),
    tariff=TariffCode.organization,
)


def _cabinet(
    request: Request,
    user: CurrentUser,
    db: Session,
    page: str,
    title: str,
    hint: str,
):
    if user.org_id is None:
        if user.is_service_admin:
            return RedirectResponse("/admin/", status_code=status.HTTP_303_SEE_OTHER)
        raise HTTPException(status_code=403, detail="Нет организации")

    org = db.get(Organization, user.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    assert_same_org(user, org.id)

    return templates.TemplateResponse(
        request=request,
        name="cabinet/placeholder.html",
        context={
            "request": request,
            "csrf_token": get_csrf_token(request),
            "app_name": get_settings().app_name,
            "user": user,
            "org": org,
            "nav": cabinet_nav(db, user),
            "active": page,
            "title": title,
            "hint": hint,
        },
    )


@router.get("/", response_class=HTMLResponse)
def cabinet_home(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    """W-28: дашборд вместо редиректа на документы."""
    from app.services.billing import get_tariff_limits
    from app.services.dashboard import load_dashboard
    from app.services.onboarding import build_onboarding_checklist

    org = db.get(Organization, user.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    dash = load_dashboard(db, org.id)
    limits = get_tariff_limits(db, org.id)
    is_paid = limits.tariff_code in (TariffCode.specialist, TariffCode.organization)
    checklist = build_onboarding_checklist(db, org, is_paid=is_paid)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/dashboard.html",
        context={
            "request": request,
            "csrf_token": get_csrf_token(request),
            "app_name": get_settings().app_name,
            "user": user,
            "org": org,
            "nav": cabinet_nav(db, user),
            "active": "home",
            "dash": dash,
            "onboarding": checklist,
        },
    )


@router.post("/onboarding/dismiss", response_class=HTMLResponse)
async def onboarding_dismiss(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    """Скрыть чеклист онбординга (доступно с шага 2)."""
    from app.services.billing import get_tariff_limits
    from app.services.onboarding import build_onboarding_checklist, dismiss_onboarding

    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    org = db.get(Organization, user.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    limits = get_tariff_limits(db, org.id)
    is_paid = limits.tariff_code in (TariffCode.specialist, TariffCode.organization)
    checklist = build_onboarding_checklist(db, org, is_paid=is_paid)
    if checklist.can_dismiss:
        dismiss_onboarding(org)
        db.commit()
    return RedirectResponse("/cabinet/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/2fa-remind/dismiss", response_class=HTMLResponse)
async def dismiss_2fa_member_remind(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
):
    """Закрыть напоминание 2FA для org_member на 30 дней."""
    from app.models import utcnow
    from app.services.two_fa_policy import MEMBER_DISMISS_COOKIE

    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")

    settings = get_settings()
    resp = RedirectResponse("/cabinet/", status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(
        key=MEMBER_DISMISS_COOKIE,
        value=str(utcnow().timestamp()),
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=bool(settings.session_https_only),
        path="/",
    )
    return resp


@router.get("/package", response_class=HTMLResponse)
def package(request: Request, user: CurrentUser = Depends(require_org_user), db: Session = Depends(get_db)):
    return RedirectResponse("/cabinet/package/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/counterparties", response_class=HTMLResponse)
def counterparties(
    request: Request, user: CurrentUser = Depends(require_org_user), db: Session = Depends(get_db)
):
    return RedirectResponse("/cabinet/counterparties/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request, user: CurrentUser = Depends(require_org_user), db: Session = Depends(get_db)
):
    return RedirectResponse("/cabinet/settings/", status_code=status.HTTP_303_SEE_OTHER)
