"""Настройки организации и счётчики (W-06, W-24)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, client_ip, require_org_user
from app.models import User
from app.org_scope import get_org_for_user, list_events, require_org_id
from app.nav_context import cabinet_nav
from app.security import check_csrf, get_csrf_token, verify_password
from app.services.audit import record_event
from app.services.retention import get_retention_days, set_retention_days
from app.services.settings_svc import (
    BANK_FIELDS,
    ORG_FIELDS,
    PRICE_FIELDS,
    SIGNATORY_BLOCKS,
    adjust_counter,
    ensure_requisites,
    list_counters,
    update_section,
)
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

_AUTH_EVENT_TYPES = {
    "login_success",
    "login_failure",
    "logout",
    "password_reset_request",
    "password_reset",
    "retention_purge",
    "totp_enabled",
    "totp_disabled",
    "totp_verify_failure",
    "totp_admin_reset",
    "totp_backup_login",
}

router = APIRouter(prefix="/cabinet/settings", tags=["settings"])


def _page(request: Request, user: CurrentUser, org, db, section: str, **extra):
    req = ensure_requisites(org)
    ctx = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": user,
        "org": org,
        "nav": cabinet_nav(db, user),
        "active": "settings",
        "section": section,
        "sections": [
            ("реквизиты", "Реквизиты", "/cabinet/settings/"),
            ("банк", "Банк", "/cabinet/settings/bank"),
            ("подписанты", "Подписанты", "/cabinet/settings/signatories"),
            ("прайс", "Прайс", "/cabinet/settings/price"),
            ("счётчики", "Счётчики", "/cabinet/settings/counters"),
            ("безопасность", "Безопасность", "/cabinet/settings/security"),
        ],
        "requisites": req,
        "flash_error": None,
        "flash_ok": None,
        "ORG_FIELDS": ORG_FIELDS,
        "BANK_FIELDS": BANK_FIELDS,
        "PRICE_FIELDS": PRICE_FIELDS,
        "SIGNATORY_BLOCKS": SIGNATORY_BLOCKS,
    }
    ctx.update(extra)
    return ctx


@router.get("/", response_class=HTMLResponse)
def settings_org(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings.html",
        context=_page(request, user, org, db, "реквизиты"),
    )


@router.post("/", response_class=HTMLResponse)
async def settings_org_save(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    values = {f: form.get(f) for f in ORG_FIELDS}
    errors = update_section(org, "организация", values)
    if errors:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_page(request, user, org, db, "реквизиты", flash_error="; ".join(errors)),
            status_code=400,
        )
    db.add(org)
    db.commit()
    db.refresh(org)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings.html",
        context=_page(request, user, org, db, "реквизиты", flash_ok="Реквизиты сохранены."),
    )


@router.get("/bank", response_class=HTMLResponse)
def settings_bank(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings.html",
        context=_page(request, user, org, db, "банк"),
    )


@router.post("/bank", response_class=HTMLResponse)
async def settings_bank_save(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    values = {f: form.get(f) for f in BANK_FIELDS}
    errors = update_section(org, "банк", values)
    if errors:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_page(request, user, org, db, "банк", flash_error="; ".join(errors)),
            status_code=400,
        )
    db.add(org)
    db.commit()
    db.refresh(org)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings.html",
        context=_page(request, user, org, db, "банк", flash_ok="Банковские реквизиты сохранены."),
    )


@router.get("/signatories", response_class=HTMLResponse)
def settings_signatories(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings.html",
        context=_page(request, user, org, db, "подписанты"),
    )


@router.post("/signatories", response_class=HTMLResponse)
async def settings_signatories_save(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    values = {k: form.get(k) for k in form.keys() if k != "csrf_token"}
    errors = update_section(org, "подписанты", values)
    if errors:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_page(request, user, org, db, "подписанты", flash_error="; ".join(errors)),
            status_code=400,
        )
    db.add(org)
    db.commit()
    db.refresh(org)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings.html",
        context=_page(request, user, org, db, "подписанты", flash_ok="Подписанты сохранены."),
    )


@router.get("/price", response_class=HTMLResponse)
def settings_price(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings.html",
        context=_page(request, user, org, db, "прайс"),
    )


@router.post("/price", response_class=HTMLResponse)
async def settings_price_save(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    values = {f: form.get(f) for f in PRICE_FIELDS}
    errors = update_section(org, "прайс", values)
    if errors:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_page(request, user, org, db, "прайс", flash_error="; ".join(errors)),
            status_code=400,
        )
    db.add(org)
    db.commit()
    db.refresh(org)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings.html",
        context=_page(request, user, org, db, "прайс", flash_ok="Прайс сохранён."),
    )


@router.get("/counters", response_class=HTMLResponse)
def settings_counters(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    counters = list_counters(db, require_org_id(user))
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings.html",
        context=_page(request, user, org, db, "счётчики", counters=counters),
    )


@router.post("/counters", response_class=HTMLResponse)
async def settings_counters_save(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    confirm = str(form.get("confirm") or "") == "1"
    key = str(form.get("key") or "").strip()
    if not key or not confirm:
        counters = list_counters(db, require_org_id(user))
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_page(request, user, org, db, "счётчики",
                counters=counters,
                flash_error="Нужны ключ счётчика и подтверждение.",
            ),
            status_code=400,
        )
    try:
        value = int(str(form.get("value") or "0"))
    except ValueError:
        value = 0
    prefix = str(form.get("prefix") or "")
    suffix = str(form.get("suffix") or "")
    adjust_counter(
        db,
        require_org_id(user),
        key,
        value=value,
        prefix=prefix,
        suffix=suffix,
    )
    db.commit()
    counters = list_counters(db, require_org_id(user))
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings.html",
        context=_page(request, user, org, db, "счётчики",
            counters=counters,
            flash_ok=f"Счётчик «{key}» обновлён. Следующий номер: {prefix}{value + 1}{suffix}",
        ),
    )


def _security_ctx(request: Request, user: CurrentUser, org, db: Session, **extra):
    events = [
        e
        for e in list_events(db, require_org_id(user), limit=80)
        if e.type in _AUTH_EVENT_TYPES
    ]
    db_user = db.get(User, user.id)
    setup_secret = request.session.get("totp_setup_secret")
    setup_uri = None
    setup_qr = None
    if setup_secret:
        setup_uri = provisioning_uri(email=user.email, secret=setup_secret)
        setup_qr = qr_data_url(setup_uri)
    return _page(
        request,
        user,
        org,
        db,
        "безопасность",
        retention_days=get_retention_days(org),
        security_events=events,
        totp_enabled=bool(db_user and db_user.totp_enabled),
        force_2fa_setup=bool(request.session.get("force_2fa_setup")),
        totp_setup_secret=setup_secret,
        totp_setup_uri=setup_uri,
        totp_setup_qr=setup_qr,
        totp_backup_codes=request.session.get("totp_backup_codes"),
        **extra,
    )


@router.get("/security", response_class=HTMLResponse)
def settings_security(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings.html",
        context=_security_ctx(request, user, org, db),
    )


@router.post("/security", response_class=HTMLResponse)
async def settings_security_save(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    raw = str(form.get("срок_дней_файлов") or "").strip()
    try:
        days = int(raw)
    except ValueError:
        days = -1
    if days < 0 or days > 36500:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_security_ctx(
                request,
                user,
                org,
                db,
                flash_error="Укажите срок в днях от 0 (не удалять) до 36500.",
            ),
            status_code=400,
        )
    set_retention_days(org, days)
    db.add(org)
    db.commit()
    db.refresh(org)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings.html",
        context=_security_ctx(
            request, user, org, db, flash_ok="Срок хранения файлов сохранён."
        ),
    )


@router.post("/security/2fa/start", response_class=HTMLResponse)
async def totp_start(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    password = str(form.get("password") or "")
    db_user = db.get(User, user.id)
    assert db_user is not None
    if db_user.totp_enabled:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_security_ctx(
                request, user, org, db, flash_error="2FA уже включена."
            ),
            status_code=400,
        )
    if not verify_password(password, db_user.password_hash):
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_security_ctx(
                request, user, org, db, flash_error="Неверный пароль."
            ),
            status_code=401,
        )
    secret = generate_totp_secret()
    request.session["totp_setup_secret"] = secret
    request.session.pop("totp_backup_codes", None)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings.html",
        context=_security_ctx(
            request,
            user,
            org,
            db,
            flash_ok="Отсканируйте QR-код и подтвердите код из приложения.",
        ),
    )


@router.post("/security/2fa/confirm", response_class=HTMLResponse)
async def totp_confirm(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    code = str(form.get("code") or "")
    secret = request.session.get("totp_setup_secret")
    db_user = db.get(User, user.id)
    assert db_user is not None
    if not secret:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_security_ctx(
                request,
                user,
                org,
                db,
                flash_error="Сессия настройки 2FA истекла. Начните заново.",
            ),
            status_code=400,
        )
    if not verify_totp_code(secret, code):
        record_event(
            db,
            type="totp_verify_failure",
            org_id=user.org_id,
            user_id=user.id,
            details={"ip": client_ip(request), "phase": "enable"},
        )
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_security_ctx(
                request, user, org, db, flash_error="Неверный код подтверждения."
            ),
            status_code=401,
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
        details={"ip": client_ip(request)},
        commit=False,
    )
    db.commit()
    notify_totp_change(settings=get_settings(), email=user.email, enabled=True)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings.html",
        context=_security_ctx(
            request,
            user,
            org,
            db,
            flash_ok="Двухфакторная аутентификация включена. Сохраните резервные коды — они показываются один раз.",
        ),
    )


@router.get("/security/2fa/backup.txt")
def totp_backup_download(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
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


@router.post("/security/2fa/dismiss-backup", response_class=HTMLResponse)
async def totp_dismiss_backup(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    request.session.pop("totp_backup_codes", None)
    return RedirectResponse(
        "/cabinet/settings/security", status_code=303
    )


@router.post("/security/2fa/disable", response_class=HTMLResponse)
async def totp_disable(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    password = str(form.get("password") or "")
    code = str(form.get("code") or "")
    db_user = db.get(User, user.id)
    assert db_user is not None
    if not db_user.totp_enabled:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_security_ctx(
                request, user, org, db, flash_error="2FA уже отключена."
            ),
            status_code=400,
        )
    if not verify_password(password, db_user.password_hash):
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_security_ctx(
                request, user, org, db, flash_error="Неверный пароль."
            ),
            status_code=401,
        )
    method = verify_user_totp_or_backup(db_user, code)
    if method is None:
        record_event(
            db,
            type="totp_verify_failure",
            org_id=user.org_id,
            user_id=user.id,
            details={"ip": client_ip(request), "phase": "disable"},
        )
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_security_ctx(
                request, user, org, db, flash_error="Неверный код."
            ),
            status_code=401,
        )
    clear_totp(db_user)
    request.session.pop("totp_backup_codes", None)
    request.session.pop("totp_setup_secret", None)
    record_event(
        db,
        type="totp_disabled",
        org_id=user.org_id,
        user_id=user.id,
        details={"ip": client_ip(request), "via": method},
        commit=False,
    )
    db.commit()
    notify_totp_change(settings=get_settings(), email=user.email, enabled=False)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings.html",
        context=_security_ctx(
            request, user, org, db, flash_ok="Двухфакторная аутентификация отключена."
        ),
    )
