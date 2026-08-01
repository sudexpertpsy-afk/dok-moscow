"""Маршруты входа / привязки через Яндекс ID (W-25)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import client_ip, get_optional_user
from app.models import User
from app.routers.auth import _finish_login, _render
from app.services.audit import record_event
from app.totp_2fa import DEVICE_COOKIE, validate_device_token
from app.yandex_oauth import (
    build_authorize_url,
    exchange_code_for_profile,
    issue_oauth_state,
    link_identity,
    load_oauth_state,
    new_pkce_pair,
    resolve_login_user,
    yandex_button_visible,
    yandex_credentials_configured,
    yandex_login_enabled_in_db,
)

log = logging.getLogger("dok.yandex_auth")

router = APIRouter(tags=["auth-yandex"])

_EMAIL_EXISTS_MSG = (
    "Аккаунт с этим e-mail уже есть — войдите паролем и привяжите Яндекс ID в профиле."
)


def _clear_oauth_session(request: Request) -> None:
    for key in (
        "yandex_oauth_state",
        "yandex_oauth_verifier",
        "yandex_oauth_intent",
        "yandex_oauth_link_user_id",
    ):
        request.session.pop(key, None)


@router.get("/auth/yandex/start")
def yandex_start(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_optional_user),
):
    settings = get_settings()
    if not yandex_credentials_configured(settings) or not yandex_login_enabled_in_db(db):
        return _render(
            request,
            "auth/login.html",
            {
                "flash_error": "Вход через Яндекс сейчас недоступен.",
                "yandex_login_available": False,
            },
            status_code=503,
        )

    intent = request.session.get("yandex_oauth_intent") or "login"
    link_uid = request.session.get("yandex_oauth_link_user_id")
    if intent == "link":
        if user is None:
            return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        if int(link_uid or 0) != user.id:
            _clear_oauth_session(request)
            return RedirectResponse(
                "/cabinet/settings/security", status_code=status.HTTP_303_SEE_OTHER
            )

    try:
        _nonce, verifier = new_pkce_pair()
        state = issue_oauth_state(
            code_verifier=verifier,
            intent=intent if intent == "link" else "login",
            link_user_id=int(link_uid) if intent == "link" and link_uid else None,
            settings=settings,
        )
        # Дублируем в сессию (удобно для одного хоста); callback опирается на signed state.
        request.session["yandex_oauth_state"] = state
        request.session["yandex_oauth_verifier"] = verifier
        if intent != "link":
            request.session["yandex_oauth_intent"] = "login"
            request.session.pop("yandex_oauth_link_user_id", None)
        url = build_authorize_url(
            state=state,
            code_verifier=verifier,
            settings=settings,
            force_confirm=(intent == "link"),
        )
    except Exception:
        log.exception("Не удалось начать OAuth Яндекс")
        _clear_oauth_session(request)
        return _render(
            request,
            "auth/login.html",
            {
                "flash_error": "Яндекс временно недоступен. Войдите паролем.",
                "yandex_login_available": yandex_button_visible(db, settings),
            },
            status_code=503,
        )
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/auth/yandex/callback")
def yandex_callback(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_optional_user),
):
    settings = get_settings()
    ip = client_ip(request)
    err = request.query_params.get("error")
    if err:
        _clear_oauth_session(request)
        return _render(
            request,
            "auth/login.html",
            {
                "flash_error": "Авторизация Яндекс отменена или отклонена.",
                "yandex_login_available": yandex_button_visible(db, settings),
            },
            status_code=400,
        )

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    payload = load_oauth_state(state or "", settings=settings) if state else None

    # Fallback на сессию того же хоста (старые вкладки / тесты).
    if payload is None:
        expected = request.session.get("yandex_oauth_state")
        verifier = request.session.get("yandex_oauth_verifier")
        if state and expected and state == expected and verifier:
            from app.yandex_oauth import OAuthStatePayload

            payload = OAuthStatePayload(
                verifier=str(verifier),
                intent=str(request.session.get("yandex_oauth_intent") or "login"),
                link_user_id=(
                    int(request.session["yandex_oauth_link_user_id"])
                    if request.session.get("yandex_oauth_link_user_id")
                    else None
                ),
            )

    if not code or payload is None:
        _clear_oauth_session(request)
        return _render(
            request,
            "auth/login.html",
            {
                "flash_error": "Некорректный ответ Яндекс OAuth (state/PKCE).",
                "yandex_login_available": yandex_button_visible(db, settings),
            },
            status_code=400,
        )

    intent = payload.intent or "login"
    link_uid = payload.link_user_id
    verifier = payload.verifier

    try:
        profile = exchange_code_for_profile(
            code=code, code_verifier=verifier, settings=settings
        )
    except Exception:
        log.exception("Обмен кода Яндекс /info не удался")
        _clear_oauth_session(request)
        return _render(
            request,
            "auth/login.html",
            {
                "flash_error": "Яндекс временно недоступен. Войдите паролем.",
                "yandex_login_available": yandex_button_visible(db, settings),
            },
            status_code=503,
        )

    if intent == "link":
        _clear_oauth_session(request)
        if user is None or link_uid is None or int(link_uid) != user.id:
            return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        db_user = db.get(User, user.id)
        assert db_user is not None
        try:
            link_identity(db, db_user, profile)
        except ValueError as exc:
            record_event(
                db,
                type="oauth_link_failure",
                org_id=db_user.org_id,
                user_id=db_user.id,
                details={"provider": "yandex", "ip": ip, "error": str(exc)},
            )
            return RedirectResponse(
                f"/cabinet/settings/security?oauth_err={str(exc)}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        record_event(
            db,
            type="oauth_linked",
            org_id=db_user.org_id,
            user_id=db_user.id,
            details={"provider": "yandex", "sub": profile.sub, "ip": ip},
            commit=False,
        )
        db.commit()
        return RedirectResponse(
            "/cabinet/settings/security?oauth_ok=linked",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # вход / регистрация
    _clear_oauth_session(request)
    if not yandex_login_enabled_in_db(db):
        return _render(
            request,
            "auth/login.html",
            {
                "flash_error": "Вход через Яндекс отключён администратором.",
                "yandex_login_available": False,
            },
            status_code=403,
        )

    try:
        db_user, outcome = resolve_login_user(db, profile)
    except Exception:
        log.exception("Ошибка сопоставления аккаунта Яндекс")
        db.rollback()
        return _render(
            request,
            "auth/login.html",
            {
                "flash_error": "Не удалось завершить вход через Яндекс.",
                "yandex_login_available": yandex_button_visible(db, settings),
            },
            status_code=500,
        )

    if outcome == "email_exists":
        record_event(
            db,
            type="oauth_login_blocked",
            org_id=None,
            user_id=None,
            details={
                "provider": "yandex",
                "email": profile.email,
                "ip": ip,
                "reason": "email_exists",
            },
        )
        return _render(
            request,
            "auth/login.html",
            {
                "flash_error": _EMAIL_EXISTS_MSG,
                "email": profile.email,
                "yandex_login_available": yandex_button_visible(db, settings),
            },
            status_code=409,
        )

    if db_user is None or outcome == "error":
        return _render(
            request,
            "auth/login.html",
            {
                "flash_error": "Не удалось войти через Яндекс.",
                "yandex_login_available": yandex_button_visible(db, settings),
            },
            status_code=400,
        )

    if outcome == "registered":
        record_event(
            db,
            type="oauth_registered",
            org_id=db_user.org_id,
            user_id=db_user.id,
            details={"provider": "yandex", "sub": profile.sub, "ip": ip},
            commit=False,
        )
        db.commit()

    via = "yandex_register" if outcome == "registered" else "yandex"

    if db_user.totp_enabled:
        device_ok = validate_device_token(request.cookies.get(DEVICE_COOKIE), db_user)
        if not device_ok:
            record_event(
                db,
                type="oauth_login_pending_2fa",
                org_id=db_user.org_id,
                user_id=db_user.id,
                details={"provider": "yandex", "ip": ip},
                commit=False,
            )
            db.commit()
            request.session["pending_2fa_user_id"] = db_user.id
            request.session.pop("force_2fa_setup", None)
            return RedirectResponse("/login/2fa", status_code=status.HTTP_303_SEE_OTHER)

    return _finish_login(request, db, db_user, ip=ip, via=via)
