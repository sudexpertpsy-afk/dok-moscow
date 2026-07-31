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

    @property
    def is_service_admin(self) -> bool:
        return self.role == UserRole.service_admin


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
    return CurrentUser(
        id=user.id,
        email=user.email,
        org_id=user.org_id,
        role=user.role,
        is_active=user.is_active,
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


def require_org_user(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Пользователь организации (не admin без org) — кабинет."""
    if user.org_id is None and not user.is_service_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет организации")
    return user


def assert_same_org(user: CurrentUser, org_id: int) -> None:
    if user.is_service_admin:
        return
    if user.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Не найдено")
