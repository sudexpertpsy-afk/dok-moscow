"""Маршруты входа / выхода / инвайта / восстановления пароля (W-01, W-09)."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, client_ip, get_optional_user, home_for_user, require_csrf
from app.models import Invite, PasswordResetToken, User, UserRole, utcnow
from app.passwords import password_policy_hint, validate_password
from app.rate_limit import LoginRateLimiter
from app.security import (
    get_csrf_token,
    hash_password,
    login_user_session,
    logout_user_session,
    verify_password,
)
from app.services.audit import record_event
from app.services.mail import send_email
from app.templating import templates

router = APIRouter(tags=["auth"])

_settings = get_settings()
login_limiter = LoginRateLimiter(_settings.login_rate_limit, _settings.login_rate_window_sec)
reset_limiter = LoginRateLimiter(5, 60 * 60)


def _render(request: Request, name: str, ctx: dict | None = None, status_code: int = 200):
    base = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": None,
        "flash_error": None,
        "flash_ok": None,
        "password_hint": password_policy_hint(),
    }
    if ctx:
        base.update(ctx)
    return templates.TemplateResponse(
        request=request, name=name, context=base, status_code=status_code
    )


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user=Depends(get_optional_user)):
    if user:
        return RedirectResponse(home_for_user(user), status_code=status.HTTP_303_SEE_OTHER)
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
        record_event(
            db,
            type="login_failure",
            org_id=user.org_id if user else None,
            user_id=user.id if user else None,
            details={"email": email_norm, "ip": ip},
        )
        return _render(
            request,
            "auth/login.html",
            {"flash_error": "Неверный e-mail или пароль.", "email": email},
            status_code=401,
        )

    login_limiter.reset(ip)
    user.last_login_at = utcnow()
    record_event(
        db,
        type="login_success",
        org_id=user.org_id,
        user_id=user.id,
        details={"ip": ip},
        commit=False,
    )
    db.commit()
    login_user_session(request, user.id, user.org_id, user.role.value)
    home = home_for_user(
        CurrentUser(
            id=user.id,
            email=user.email,
            org_id=user.org_id,
            role=user.role,
            is_active=user.is_active,
        )
    )
    return RedirectResponse(home, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
def logout(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_optional_user),
    _: None = Depends(require_csrf),
):
    if user:
        record_event(
            db,
            type="logout",
            org_id=user.org_id,
            user_id=user.id,
            details={"ip": client_ip(request)},
        )
    logout_user_session(request)
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request, user=Depends(get_optional_user)):
    if user:
        return RedirectResponse(home_for_user(user), status_code=status.HTTP_303_SEE_OTHER)
    return _render(request, "auth/forgot_password.html")


@router.post("/forgot-password", response_class=HTMLResponse)
def forgot_password_submit(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    settings = get_settings()
    ip = client_ip(request)
    key = f"reset:{ip}"
    if reset_limiter.is_blocked(key):
        return _render(
            request,
            "auth/forgot_password.html",
            {"flash_error": "Слишком много запросов. Попробуйте позже.", "email": email},
            status_code=429,
        )
    reset_limiter.register_failure(key)

    email_norm = email.strip().lower()
    # Одинаковый ответ — не раскрываем наличие учётки
    ok_msg = (
        "Если учётная запись с таким e-mail существует, "
        "мы отправили ссылку для сброса пароля."
    )
    user = db.scalar(select(User).where(User.email == email_norm, User.is_active.is_(True)))
    if user:
        raw = secrets.token_urlsafe(32)
        token = PasswordResetToken(
            user_id=user.id,
            token_hash=_hash_token(raw),
            expires_at=utcnow() + timedelta(hours=settings.password_reset_ttl_hours),
        )
        db.add(token)
        record_event(
            db,
            type="password_reset_request",
            org_id=user.org_id,
            user_id=user.id,
            details={"ip": ip},
            commit=False,
        )
        db.commit()

        base = settings.app_base_url.rstrip("/")
        link = f"{base}/reset-password/{raw}"
        send_email(
            settings,
            to_addr=user.email,
            subject="[Док.Москва] Сброс пароля",
            body=(
                f"Здравствуйте.\n\n"
                f"Запрошен сброс пароля для {user.email}.\n"
                f"Ссылка действует {settings.password_reset_ttl_hours} ч.:\n\n"
                f"{link}\n\n"
                f"Если вы не запрашивали сброс — проигнорируйте письмо.\n"
            ),
        )

    return _render(request, "auth/forgot_password.html", {"flash_ok": ok_msg, "email": email})


@router.get("/reset-password/{token}", response_class=HTMLResponse)
def reset_password_page(request: Request, token: str, db: Session = Depends(get_db)):
    row = db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == _hash_token(token))
    )
    if row is None or row.is_used or row.is_expired():
        return _render(
            request,
            "auth/reset_invalid.html",
            {"flash_error": "Ссылка недействительна или истекла."},
            status_code=404,
        )
    return _render(request, "auth/reset_password.html", {"token": token})


@router.post("/reset-password/{token}", response_class=HTMLResponse)
def reset_password_submit(
    request: Request,
    token: str,
    password: str = Form(...),
    password2: str = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    row = db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == _hash_token(token))
    )
    if row is None or row.is_used or row.is_expired():
        return _render(
            request,
            "auth/reset_invalid.html",
            {"flash_error": "Ссылка недействительна или истекла."},
            status_code=404,
        )
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        return _render(
            request,
            "auth/reset_invalid.html",
            {"flash_error": "Пользователь не найден."},
            status_code=404,
        )

    err = validate_password(password, email=user.email)
    if err:
        return _render(
            request,
            "auth/reset_password.html",
            {"token": token, "flash_error": err},
            status_code=400,
        )
    if password != password2:
        return _render(
            request,
            "auth/reset_password.html",
            {"token": token, "flash_error": "Пароли не совпадают."},
            status_code=400,
        )

    user.password_hash = hash_password(password)
    row.used_at = utcnow()
    record_event(
        db,
        type="password_reset",
        org_id=user.org_id,
        user_id=user.id,
        details={"ip": client_ip(request)},
        commit=False,
    )
    db.commit()
    return _render(
        request,
        "auth/login.html",
        {"flash_ok": "Пароль обновлён. Войдите с новым паролем."},
    )


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

    err = validate_password(password, email=invite.email)
    if err:
        return _render(
            request,
            "auth/accept_invite.html",
            {
                "invite": invite,
                "org_name": invite.organization.name,
                "token": token,
                "flash_error": err,
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
    db.flush()
    record_event(
        db,
        type="login_success",
        org_id=user.org_id,
        user_id=user.id,
        details={"ip": client_ip(request), "via": "invite"},
        commit=False,
    )
    db.commit()
    db.refresh(user)
    login_user_session(request, user.id, user.org_id, user.role.value)
    return RedirectResponse("/cabinet/", status_code=status.HTTP_303_SEE_OTHER)
