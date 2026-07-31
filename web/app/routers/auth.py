"""Маршруты входа / выхода / принятия инвайта."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import client_ip, get_optional_user, require_csrf
from app.models import Invite, User, UserRole, utcnow
from app.rate_limit import LoginRateLimiter
from app.security import (
    get_csrf_token,
    hash_password,
    login_user_session,
    logout_user_session,
    verify_password,
)
from app.templating import templates

router = APIRouter(tags=["auth"])

_settings = get_settings()
login_limiter = LoginRateLimiter(_settings.login_rate_limit, _settings.login_rate_window_sec)


def _render(request: Request, name: str, ctx: dict | None = None, status_code: int = 200):
    base = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": None,
        "flash_error": None,
        "flash_ok": None,
    }
    if ctx:
        base.update(ctx)
    return templates.TemplateResponse(
        request=request, name=name, context=base, status_code=status_code
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user=Depends(get_optional_user)):
    if user:
        return RedirectResponse("/cabinet/", status_code=status.HTTP_303_SEE_OTHER)
    return _render(request, "auth/login.html")


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    ip = client_ip(request)
    if login_limiter.is_blocked(ip):
        return _render(
            request,
            "auth/login.html",
            {"flash_error": "Слишком много попыток. Попробуйте позже.", "email": email},
            status_code=429,
        )

    email_norm = email.strip().lower()
    user = db.scalar(select(User).where(User.email == email_norm))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        login_limiter.register_failure(ip)
        return _render(
            request,
            "auth/login.html",
            {"flash_error": "Неверный e-mail или пароль.", "email": email},
            status_code=401,
        )

    login_limiter.reset(ip)
    user.last_login_at = utcnow()
    db.commit()
    login_user_session(request, user.id, user.org_id, user.role.value)
    target = "/admin/" if user.role == UserRole.service_admin and user.org_id is None else "/cabinet/"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
def logout(request: Request, _: None = Depends(require_csrf)):
    logout_user_session(request)
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/invite/{token}", response_class=HTMLResponse)
def invite_page(request: Request, token: str, db: Session = Depends(get_db)):
    invite = db.scalar(select(Invite).where(Invite.token == token))
    if invite is None or invite.is_used or invite.is_expired():
        return _render(
            request,
            "auth/invite_invalid.html",
            {"flash_error": "Приглашение недействительно или уже использовано."},
            status_code=404,
        )
    return _render(
        request,
        "auth/accept_invite.html",
        {
            "invite": invite,
            "org_name": invite.organization.name if invite.organization else "",
            "token": token,
        },
    )


@router.post("/invite/{token}", response_class=HTMLResponse)
def invite_accept(
    request: Request,
    token: str,
    password: str = Form(...),
    password2: str = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    invite = db.scalar(select(Invite).where(Invite.token == token))
    if invite is None or invite.is_used or invite.is_expired():
        return _render(
            request,
            "auth/invite_invalid.html",
            {"flash_error": "Приглашение недействительно или уже использовано."},
            status_code=404,
        )

    if len(password) < 8:
        return _render(
            request,
            "auth/accept_invite.html",
            {
                "invite": invite,
                "org_name": invite.organization.name,
                "token": token,
                "flash_error": "Пароль не короче 8 символов.",
            },
            status_code=400,
        )
    if password != password2:
        return _render(
            request,
            "auth/accept_invite.html",
            {
                "invite": invite,
                "org_name": invite.organization.name,
                "token": token,
                "flash_error": "Пароли не совпадают.",
            },
            status_code=400,
        )

    existing = db.scalar(select(User).where(User.email == invite.email.lower()))
    if existing:
        return _render(
            request,
            "auth/invite_invalid.html",
            {"flash_error": "Пользователь с этим e-mail уже зарегистрирован."},
            status_code=409,
        )

    user = User(
        org_id=invite.org_id,
        email=invite.email.lower(),
        password_hash=hash_password(password),
        role=UserRole.user,
        is_active=True,
        last_login_at=utcnow(),
    )
    invite.used_at = utcnow()
    db.add(user)
    db.commit()
    db.refresh(user)
    login_user_session(request, user.id, user.org_id, user.role.value)
    return RedirectResponse("/cabinet/", status_code=status.HTTP_303_SEE_OTHER)
