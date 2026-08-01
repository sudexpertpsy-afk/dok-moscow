"""Зависимости FastAPI: текущий пользователь, роли, CSRF."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Form, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User, UserRole
from app.security import check_csrf, session_user_snapshot


@dataclass
class CurrentUser:
    id: int
    email: str
    org_id: int | None
    role: UserRole
    is_active: bool
    nav_order: dict | None = None

    @property
    def is_service_admin(self) -> bool:
        return self.role == UserRole.service_admin


def home_for_user(user: CurrentUser) -> str:
    """Куда вести после входа / при повторном заходе на /login."""
    if user.is_service_admin and user.org_id is None:
        return "/admin/"
    if user.org_id is None:
        return "/login"
    return "/cabinet/"


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def require_csrf(
    request: Request,
    csrf_token: str | None = Form(None),
) -> None:
    header = request.headers.get("x-csrf-token")
    token = csrf_token or header
    if not check_csrf(request, token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Неверный CSRF-токен")


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
) -> CurrentUser | None:
    snap = session_user_snapshot(request)
    if not snap:
        return None
    user = db.get(User, snap["user_id"])
    if user is None or not user.is_active:
        return None
    order = user.nav_order if isinstance(user.nav_order, dict) else None
    return CurrentUser(
        id=user.id,
        email=user.email,
        org_id=user.org_id,
        role=user.role,
        is_active=user.is_active,
        nav_order=order,
    )


def get_current_user(user: CurrentUser | None = Depends(get_optional_user)) -> CurrentUser:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется вход",
            headers={"HX-Redirect": "/login"},
        )
    return user


def require_service_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_service_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Только администратор сервиса")
    return user


def require_org_user(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Пользователь с организацией. Админ сервиса без org → редирект в /admin/."""
    if user.org_id is None:
        if user.is_service_admin:
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                detail="Redirect to admin",
                headers={"Location": "/admin/", "HX-Redirect": "/admin/"},
            )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет организации")
    # W-24: принудительный мастер 2FA (политика сервиса)
    if request.session.get("force_2fa_setup"):
        path = request.url.path
        if not path.startswith("/cabinet/settings/security"):
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                detail="Требуется включить 2FA",
                headers={
                    "Location": "/cabinet/settings/security",
                    "HX-Redirect": "/cabinet/settings/security",
                },
            )
    return user


def assert_same_org(user: CurrentUser, org_id: int) -> None:
    if user.is_service_admin:
        return
    if user.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Не найдено")
