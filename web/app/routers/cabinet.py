"""Кабинет организации (заглушки разделов W-01)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, assert_same_org, get_current_user, require_org_user
from app.models import Organization
from app.security import get_csrf_token
from app.templating import templates

router = APIRouter(prefix="/cabinet", tags=["cabinet"])

NAV = [
    ("documents", "Документы", "/cabinet/documents/"),
    ("package", "Комплект", "/cabinet/package/"),
    ("templates", "Мои шаблоны", "/cabinet/templates/"),
    ("counterparties", "Контрагенты", "/cabinet/counterparties/"),
    ("journal", "Журнал", "/cabinet/journal"),
    ("calendar", "Календарь", "/cabinet/calendar/"),
    ("billing", "Тариф и оплата", "/cabinet/billing/"),
    ("settings", "Настройки", "/cabinet/settings/"),
]


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
            "nav": NAV,
            "active": page,
            "title": title,
            "hint": hint,
        },
    )


@router.get("/", response_class=HTMLResponse)
def documents(request: Request, user: CurrentUser = Depends(require_org_user), db: Session = Depends(get_db)):
    return RedirectResponse("/cabinet/documents/", status_code=status.HTTP_303_SEE_OTHER)


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


@router.get("/org/{org_id}", response_class=HTMLResponse)
def org_scoped_probe(
    org_id: int,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Проверочный эндпойнт изоляции: чужой org_id → 404."""
    assert_same_org(user, org_id)
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Не найдено")
    return _cabinet(
        request,
        user,
        db,
        "documents",
        "Документы",
        f"Организация «{org.name}».",
    )
