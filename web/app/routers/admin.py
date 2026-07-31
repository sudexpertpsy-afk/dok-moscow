"""Админка сервиса: организации и приглашения."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, require_csrf, require_service_admin
from app.models import Invite, Organization, User, utcnow
from app.security import get_csrf_token, new_invite_token
from app.templating import templates

router = APIRouter(prefix="/admin", tags=["admin"])


def _ctx(request: Request, user: CurrentUser, **extra):
    data = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": user,
        "flash_error": None,
        "flash_ok": None,
        "last_invite_link": None,
    }
    data.update(extra)
    return data


def _home(request: Request, user: CurrentUser, db: Session, status_code: int = 200, **extra):
    orgs = db.scalars(select(Organization).order_by(Organization.id.desc())).all()
    invites = db.scalars(select(Invite).order_by(Invite.id.desc()).limit(50)).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/home.html",
        context=_ctx(request, user, orgs=orgs, invites=invites, **extra),
        status_code=status_code,
    )


@router.get("/", response_class=HTMLResponse)
def admin_home(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    return _home(request, user, db)


@router.post("/organizations", response_class=HTMLResponse)
def create_organization(
    request: Request,
    name: str = Form(...),
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    name = name.strip()
    if not name:
        return _home(request, user, db, status_code=400, flash_error="Укажите название организации.")
    org = Organization(name=name, requisites={})
    db.add(org)
    db.commit()
    return RedirectResponse("/admin/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/invites", response_class=HTMLResponse)
def create_invite(
    request: Request,
    org_id: int = Form(...),
    email: str = Form(...),
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    settings = get_settings()
    email_norm = email.strip().lower()
    org = db.get(Organization, org_id)

    if org is None:
        return _home(request, user, db, status_code=404, flash_error="Организация не найдена.")
    if "@" not in email_norm:
        return _home(request, user, db, status_code=400, flash_error="Некорректный e-mail.")
    if db.scalar(select(User).where(User.email == email_norm)):
        return _home(
            request,
            user,
            db,
            status_code=409,
            flash_error="Пользователь с таким e-mail уже есть.",
        )

    token = new_invite_token()
    invite = Invite(
        org_id=org.id,
        email=email_norm,
        token=token,
        expires_at=utcnow() + timedelta(hours=settings.invite_ttl_hours),
    )
    db.add(invite)
    db.commit()
    link = str(request.base_url).rstrip("/") + f"/invite/{token}"
    return _home(
        request,
        user,
        db,
        flash_ok=f"Приглашение создано. Ссылка: {link}",
        last_invite_link=link,
    )
