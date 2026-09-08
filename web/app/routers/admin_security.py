"""Безопасность учётки администратора сервиса (пароль, 2FA, Яндекс ID).

Без новой инфраструктуры: те же сервисы, что у /cabinet/settings/security.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, client_ip, require_service_admin
from app.models import Event, User
from app.passwords import password_policy_hint, validate_password
from app.routers.admin import _ctx
from app.security import check_csrf, hash_password, verify_password
from app.services.audit import record_event
from app.templating import templates
from app.totp_2fa import (
    backup_codes_txt,
    clear_totp,
    enable_totp,
    generate_backup_codes,
    generate_totp_secret,
    notify_totp_change,
    provisioning_uri,
    qr_data_url,
    verify_totp_code,
    verify_user_totp_or_backup,
)
from app.yandex_oauth import (
    can_unlink_yandex,
    unlink_yandex,
    user_has_password,
    user_yandex_identity,
    yandex_button_visible,
)

router = APIRouter(prefix="/admin/security", tags=["admin-security"])

_BASE = "/admin/security"

_AUTH_EVENT_TYPES = {
    "login_success",
    "login_failure",
    "logout",
    "password_reset_request",
    "password_reset",
    "totp_enabled",
    "totp_disabled",
    "totp_verify_failure",
    "totp_admin_reset",
    "totp_backup_login",
    "oauth_linked",
    "oauth_unlinked",
    "oauth_link_failure",
    "oauth_registered",
    "oauth_login_blocked",
    "oauth_login_pending_2fa",
    "password_set",
}


def _user_security_events(db: Session, user_id: int, *, limit: int = 80) -> list[Event]:
    rows = db.scalars(
        select(Event)
        .where(Event.user_id == user_id)
        .order_by(Event.ts.desc())
        .limit(limit * 2)
    ).all()
    return [e for e in rows if e.type in _AUTH_EVENT_TYPES][:limit]


def _security_page(request: Request, user: CurrentUser, db: Session, **extra):
    db_user = db.get(User, user.id)
    setup_secret = request.session.get("totp_setup_secret")
    setup_uri = None
    setup_qr = None
    if setup_secret:
        setup_uri = provisioning_uri(email=user.email, secret=setup_secret)
        setup_qr = qr_data_url(setup_uri)
    yandex_ident = user_yandex_identity(db, user.id) if db_user else None
    can_unlink, unlink_hint = (False, "")
    if db_user and yandex_ident:
        can_unlink, unlink_hint = can_unlink_yandex(db, db_user)
    flash_ok = extra.pop("flash_ok", None)
    flash_error = extra.pop("flash_error", None)
    if request.query_params.get("oauth_ok") == "linked":
        flash_ok = flash_ok or "Яндекс ID привязан."
    if request.query_params.get("oauth_err"):
        flash_error = flash_error or request.query_params.get("oauth_err")
    return _ctx(
        request,
        user,
        "admin_security",
        db=db,
        flash_ok=flash_ok,
        flash_error=flash_error,
        security_base=_BASE,
        show_retention=False,
        show_admin_2fa_hint=True,
        security_events=_user_security_events(db, user.id),
        totp_enabled=bool(db_user and db_user.totp_enabled),
        force_2fa_setup=bool(request.session.get("force_2fa_setup")),
        totp_setup_secret=setup_secret,
        totp_setup_uri=setup_uri,
        totp_setup_qr=setup_qr,
        totp_backup_codes=request.session.get("totp_backup_codes"),
        has_password=bool(db_user and user_has_password(db_user)),
        yandex_linked=yandex_ident is not None,
        yandex_provider_email=yandex_ident.provider_email if yandex_ident else None,
        yandex_can_unlink=can_unlink,
        yandex_unlink_hint=unlink_hint,
        yandex_available=yandex_button_visible(db),
        password_hint=password_policy_hint(),
        **extra,
    )


def _render(request: Request, user: CurrentUser, db: Session, *, status_code: int = 200, **extra):
    return templates.TemplateResponse(
        request=request,
        name="admin/security.html",
        context=_security_page(request, user, db, **extra),
        status_code=status_code,
    )


@router.get("/", response_class=HTMLResponse)
def admin_security_home(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    return _render(request, user, db)


@router.post("/2fa/start", response_class=HTMLResponse)
async def admin_totp_start(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    password = str(form.get("password") or "")
    db_user = db.get(User, user.id)
    assert db_user is not None
    if db_user.totp_enabled:
        return _render(request, user, db, flash_error="2FA уже включена.", status_code=400)
    if not db_user.password_hash or not verify_password(password, db_user.password_hash):
        return _render(request, user, db, flash_error="Неверный пароль.", status_code=401)
    secret = generate_totp_secret()
    request.session["totp_setup_secret"] = secret
    request.session.pop("totp_backup_codes", None)
    return _render(
        request,
        user,
        db,
        flash_ok="Отсканируйте QR-код и подтвердите код из приложения.",
    )


@router.post("/2fa/confirm", response_class=HTMLResponse)
async def admin_totp_confirm(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    code = str(form.get("code") or "")
    secret = request.session.get("totp_setup_secret")
    db_user = db.get(User, user.id)
    assert db_user is not None
    if not secret:
        return _render(
            request,
            user,
            db,
            flash_error="Сессия настройки 2FA истекла. Начните заново.",
            status_code=400,
        )
    if not verify_totp_code(secret, code):
        record_event(
            db,
            type="totp_verify_failure",
            org_id=user.org_id,
            user_id=user.id,
            details={"ip": client_ip(request), "phase": "enable", "surface": "admin"},
        )
        return _render(
            request, user, db, flash_error="Неверный код подтверждения.", status_code=401
        )
    codes = generate_backup_codes()
    enable_totp(db_user, secret=secret, backup_codes=codes)
    request.session.pop("totp_setup_secret", None)
    request.session.pop("force_2fa_setup", None)
    request.session["totp_backup_codes"] = codes
    record_event(
        db,
        type="totp_enabled",
        org_id=user.org_id,
        user_id=user.id,
        details={"ip": client_ip(request), "surface": "admin"},
        commit=False,
    )
    db.commit()
    notify_totp_change(settings=get_settings(), email=user.email, enabled=True)
    return _render(
        request,
        user,
        db,
        flash_ok="Двухфакторная аутентификация включена. Сохраните резервные коды — они показываются один раз.",
    )


@router.get("/2fa/backup.txt")
def admin_totp_backup_download(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
):
    codes = request.session.get("totp_backup_codes")
    if not codes:
        raise HTTPException(status_code=404, detail="Резервные коды недоступны")
    body = backup_codes_txt(list(codes), email=user.email)
    return PlainTextResponse(
        body,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="dok-moscow-2fa-backup.txt"'
        },
    )


@router.post("/2fa/dismiss-backup", response_class=HTMLResponse)
async def admin_totp_dismiss_backup(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
):
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    request.session.pop("totp_backup_codes", None)
    return RedirectResponse(_BASE + "/", status_code=303)


@router.post("/2fa/disable", response_class=HTMLResponse)
async def admin_totp_disable(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    password = str(form.get("password") or "")
    code = str(form.get("code") or "")
    db_user = db.get(User, user.id)
    assert db_user is not None
    if not db_user.totp_enabled:
        return _render(request, user, db, flash_error="2FA уже отключена.", status_code=400)
    if not db_user.password_hash or not verify_password(password, db_user.password_hash):
        return _render(request, user, db, flash_error="Неверный пароль.", status_code=401)
    method = verify_user_totp_or_backup(db_user, code)
    if method is None:
        record_event(
            db,
            type="totp_verify_failure",
            org_id=user.org_id,
            user_id=user.id,
            details={"ip": client_ip(request), "phase": "disable", "surface": "admin"},
        )
        return _render(request, user, db, flash_error="Неверный код.", status_code=401)
    clear_totp(db_user)
    request.session.pop("totp_backup_codes", None)
    request.session.pop("totp_setup_secret", None)
    record_event(
        db,
        type="totp_disabled",
        org_id=user.org_id,
        user_id=user.id,
        details={"ip": client_ip(request), "via": method, "surface": "admin"},
        commit=False,
    )
    db.commit()
    notify_totp_change(settings=get_settings(), email=user.email, enabled=False)
    return _render(
        request, user, db, flash_ok="Двухфакторная аутентификация отключена."
    )


@router.post("/password", response_class=HTMLResponse)
async def admin_set_password(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    db_user = db.get(User, user.id)
    assert db_user is not None
    password = str(form.get("password") or "")
    password2 = str(form.get("password2") or "")
    if user_has_password(db_user):
        current = str(form.get("current_password") or "")
        if not verify_password(current, db_user.password_hash):
            return _render(
                request, user, db, flash_error="Неверный текущий пароль.", status_code=401
            )
    err = validate_password(password, email=user.email)
    if err:
        return _render(request, user, db, flash_error=err, status_code=400)
    if password != password2:
        return _render(request, user, db, flash_error="Пароли не совпадают.", status_code=400)
    db_user.password_hash = hash_password(password)
    record_event(
        db,
        type="password_set",
        org_id=user.org_id,
        user_id=user.id,
        details={"ip": client_ip(request), "surface": "admin"},
        commit=False,
    )
    db.commit()
    return _render(request, user, db, flash_ok="Пароль сохранён.")


@router.post("/yandex/link")
async def admin_yandex_link(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    if not yandex_button_visible(db):
        return _render(
            request,
            user,
            db,
            flash_error="Привязка Яндекс ID сейчас недоступна.",
            status_code=503,
        )
    db_user = db.get(User, user.id)
    assert db_user is not None
    if user_yandex_identity(db, user.id):
        return RedirectResponse(_BASE + "/", status_code=303)
    if not user_has_password(db_user):
        return _render(
            request,
            user,
            db,
            flash_error="Сначала задайте пароль, затем привяжите Яндекс ID.",
            status_code=400,
        )
    password = str(form.get("password") or "")
    if not verify_password(password, db_user.password_hash):
        return _render(request, user, db, flash_error="Неверный пароль.", status_code=401)
    request.session["yandex_oauth_intent"] = "link"
    request.session["yandex_oauth_link_user_id"] = user.id
    request.session["yandex_oauth_return"] = _BASE + "/"
    return RedirectResponse("/auth/yandex/start", status_code=303)


@router.post("/yandex/unlink", response_class=HTMLResponse)
async def admin_yandex_unlink(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    db_user = db.get(User, user.id)
    assert db_user is not None
    try:
        unlink_yandex(db, db_user)
    except ValueError as exc:
        return _render(request, user, db, flash_error=str(exc), status_code=400)
    record_event(
        db,
        type="oauth_unlinked",
        org_id=user.org_id,
        user_id=user.id,
        details={"provider": "yandex", "ip": client_ip(request), "surface": "admin"},
        commit=False,
    )
    db.commit()
    return _render(request, user, db, flash_ok="Яндекс ID отвязан.")
