"""Кабинет: страница «Как пользоваться» — инструкции по разделам."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, assert_same_org, require_org_user
from app.models import Organization
from app.nav_context import cabinet_nav
from app.security import get_csrf_token
from app.templating import templates

router = APIRouter(prefix="/cabinet/help", tags=["cabinet-help"])


@router.get("/", response_class=HTMLResponse)
def help_page(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    if user.org_id is None:
        if user.is_service_admin:
            return RedirectResponse("/admin/", status_code=status.HTTP_303_SEE_OTHER)
        raise HTTPException(status_code=403, detail="Нет организации")

    org = db.get(Organization, user.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    assert_same_org(user, org.id)

    settings = get_settings()
    return templates.TemplateResponse(
        request=request,
        name="cabinet/help.html",
        context={
            "request": request,
            "csrf_token": get_csrf_token(request),
            "app_name": settings.app_name,
            "public_base_url": settings.public_base_url.rstrip("/"),
            "user": user,
            "org": org,
            "nav": cabinet_nav(db, user),
            "active": "help",
        },
    )
