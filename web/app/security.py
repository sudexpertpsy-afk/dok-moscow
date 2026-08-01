"""Пароли, CSRF, сессии."""

from __future__ import annotations

import secrets
from typing import Any

import bcrypt
from fastapi import Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import Settings, get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def new_invite_token() -> str:
    return secrets.token_urlsafe(32)


def _csrf_serializer(settings: Settings | None = None) -> URLSafeTimedSerializer:
    settings = settings or get_settings()
    return URLSafeTimedSerializer(settings.secret_key, salt="dok-csrf")


def issue_csrf_token(session_id: str, settings: Settings | None = None) -> str:
    return _csrf_serializer(settings).dumps({"sid": session_id, "n": secrets.token_hex(8)})


def validate_csrf_token(token: str, session_id: str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    try:
        data = _csrf_serializer(settings).loads(token, max_age=settings.session_max_age)
    except BadSignature:
        return False
    return isinstance(data, dict) and data.get("sid") == session_id


def ensure_session_id(request: Request) -> str:
    sid = request.session.get("sid")
    if not sid:
        sid = secrets.token_hex(16)
        request.session["sid"] = sid
    return sid


def get_csrf_token(request: Request) -> str:
    sid = ensure_session_id(request)
    token = request.session.get("csrf_token")
    if not token or not validate_csrf_token(token, sid):
        token = issue_csrf_token(sid)
        request.session["csrf_token"] = token
    return token


def check_csrf(request: Request, submitted: str | None) -> bool:
    if not submitted:
        return False
    sid = ensure_session_id(request)
    expected = request.session.get("csrf_token")
    if not expected:
        return False
    return secrets.compare_digest(submitted, expected) and validate_csrf_token(submitted, sid)


def login_user_session(request: Request, user_id: int, org_id: int | None, role: str) -> None:
    ensure_session_id(request)
    request.session["user_id"] = user_id
    request.session["org_id"] = org_id
    request.session["role"] = role
    # ротация CSRF после входа
    request.session["csrf_token"] = issue_csrf_token(request.session["sid"])


def logout_user_session(request: Request) -> None:
    request.session.clear()


def session_user_snapshot(request: Request) -> dict[str, Any] | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return {
        "user_id": int(user_id),
        "org_id": request.session.get("org_id"),
        "role": request.session.get("role"),
    }
