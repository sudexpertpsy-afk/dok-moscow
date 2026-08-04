"""Настройки организации и счётчики (W-06, W-24, W-25)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, client_ip, forbidden_org_admin_page, require_org_user
from app.models import User
from app.org_scope import get_org_for_user, list_events, require_org_id
from app.nav_context import cabinet_nav
from app.passwords import password_policy_hint, validate_password
from app.security import check_csrf, get_csrf_token, hash_password, verify_password
from app.services.audit import record_event
from app.services.retention import get_retention_days, set_retention_days
from app.services.settings_svc import (
    BANK_FIELD_LABELS,
    BANK_FIELDS,
    BANK_FORM_MAP,
    ORG_FIELD_LABELS,
    ORG_FIELDS,
    PRICE_FIELDS,
    SIGNATORY_BLOCKS,
    adjust_counter,
    bank_from_form,
    bank_is_complete,
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
from app.yandex_oauth import (
    can_unlink_yandex,
    unlink_yandex,
    user_has_password,
    user_yandex_identity,
    yandex_button_visible,
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
    "oauth_linked",
    "oauth_unlinked",
    "oauth_link_failure",
    "oauth_registered",
    "oauth_login_blocked",
    "oauth_login_pending_2fa",
    "password_set",
}

router = APIRouter(prefix="/cabinet/settings", tags=["settings"])


def _sections_for(user: CurrentUser) -> list[tuple[str, str, str]]:
    if user.is_org_admin:
        return [
            ("реквизиты", "Реквизиты", "/cabinet/settings/"),
            ("банк", "Банк", "/cabinet/settings/bank"),
            ("подписанты", "Подписанты", "/cabinet/settings/signatories"),
            ("печать", "Печать и подписи", "/cabinet/settings/branding"),
            ("прайс", "Прайс", "/cabinet/settings/price"),
            ("счётчики", "Счётчики", "/cabinet/settings/counters"),
            ("безопасность", "Безопасность", "/cabinet/settings/security"),
        ]
    return [
        ("реквизиты", "Реквизиты", "/cabinet/settings/"),
        ("безопасность", "Безопасность", "/cabinet/settings/security"),
    ]


def _page(request: Request, user: CurrentUser, org, db, section: str, **extra):
    req = ensure_requisites(org)
    read_only = not user.is_org_admin and section == "реквизиты"
    ctx = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": user,
        "org": org,
        "nav": cabinet_nav(db, user),
        "active": "settings",
        "section": section,
        "sections": _sections_for(user),
        "requisites": req,
        "read_only": read_only,
        "flash_error": None,
        "flash_ok": None,
        "ORG_FIELDS": ORG_FIELDS,
        "ORG_FIELD_LABELS": ORG_FIELD_LABELS,
        "BANK_FIELDS": BANK_FIELDS,
        "BANK_FORM_MAP": BANK_FORM_MAP,
        "BANK_FIELD_LABELS": BANK_FIELD_LABELS,
        "bank_complete": bank_is_complete(req),
        "dadata_enabled": bool(get_settings().dadata_key),
        "PRICE_FIELDS": PRICE_FIELDS,
        "SIGNATORY_BLOCKS": SIGNATORY_BLOCKS,
    }
    ctx.update(extra)
    return ctx


def _require_org_settings_admin(request: Request, user: CurrentUser, db: Session):
    if user.is_org_admin:
        return None
    return forbidden_org_admin_page(request, user, db)


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
    denied = _require_org_settings_admin(request, user, db)
    if denied is not None:
        return denied
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
    denied = _require_org_settings_admin(request, user, db)
    if denied is not None:
        return denied
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
    denied = _require_org_settings_admin(request, user, db)
    if denied is not None:
        return denied
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    values = bank_from_form(form)
    if not values:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_page(
                request,
                user,
                org,
                db,
                "банк",
                flash_error="Не получены поля банка. Обновите страницу и сохраните снова.",
            ),
            status_code=400,
        )
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
    denied = _require_org_settings_admin(request, user, db)
    if denied is not None:
        return denied
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
    denied = _require_org_settings_admin(request, user, db)
    if denied is not None:
        return denied
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


@router.get("/branding", response_class=HTMLResponse)
def settings_branding(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    denied = _require_org_settings_admin(request, user, db)
    if denied is not None:
        return denied
    from app.services.branding import list_slots_status
    from app.services.branding_access import can_manage_branding
    from app.services.facsimile import get_facsimile_prefs
    from app.services.settings_svc import ensure_requisites

    org = get_org_for_user(db, user)
    ok, reason = can_manage_branding(db, org.id)
    req = ensure_requisites(org)
    prefs = get_facsimile_prefs(req)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings_branding.html",
        context=_page(
            request,
            user,
            org,
            db,
            "печать",
            slots=list_slots_status(org.id),
            branding_allowed=ok,
            branding_reason=reason,
            prefs=prefs,
            flash_ok=request.query_params.get("ok"),
            flash_error=request.query_params.get("err"),
        ),
    )


@router.post("/branding/prefs", response_class=HTMLResponse)
async def settings_branding_prefs(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    denied = _require_org_settings_admin(request, user, db)
    if denied is not None:
        return denied
    from app.services.branding_access import assert_can_manage_branding
    from app.services.facsimile import set_facsimile_prefs
    from app.services.settings_svc import ensure_requisites

    org = get_org_for_user(db, user)
    gate = assert_can_manage_branding(db, org.id)
    if gate is not None:
        return gate
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    req = ensure_requisites(org)
    set_facsimile_prefs(
        req,
        pdf_default=bool(form.get("pdf_default")),
        embed_docx=bool(form.get("embed_docx")),
    )
    org.requisites = dict(req)
    db.add(org)
    record_event(
        db,
        type="branding_prefs",
        org_id=org.id,
        user_id=user.id,
        details={"pdf_default": bool(form.get("pdf_default")), "embed_docx": bool(form.get("embed_docx"))},
    )
    db.commit()
    return RedirectResponse("/cabinet/settings/branding?ok=Настройки+сохранены", status_code=303)


@router.post("/branding/{slot}/upload", response_class=HTMLResponse)
async def settings_branding_upload(
    slot: str,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    denied = _require_org_settings_admin(request, user, db)
    if denied is not None:
        return denied
    from urllib.parse import quote

    from app.services.branding import BrandingError, SLOTS, process_and_save
    from app.services.branding_access import assert_can_manage_branding

    org = get_org_for_user(db, user)
    gate = assert_can_manage_branding(db, org.id)
    if gate is not None:
        return gate
    if slot not in SLOTS:
        return RedirectResponse("/cabinet/settings/branding?err=Неизвестный+слот", status_code=303)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    upload = form.get("file")
    if upload is None or not getattr(upload, "filename", None):
        return RedirectResponse(
            f"/cabinet/settings/branding?err={quote('Выберите файл')}", status_code=303
        )
    data = await upload.read()
    remove_bg = bool(form.get("remove_bg", "1"))
    try:
        threshold = int(form.get("bg_threshold") or 240)
    except (TypeError, ValueError):
        threshold = 240
    try:
        _, verdict = process_and_save(
            org.id, slot, data, remove_bg=remove_bg, bg_threshold=threshold
        )
    except BrandingError as exc:
        return RedirectResponse(
            f"/cabinet/settings/branding?err={quote(str(exc))}", status_code=303
        )
    record_event(
        db,
        type="branding_upload",
        org_id=org.id,
        user_id=user.id,
        details={"slot": slot, "verdict": verdict.level.value, "message": verdict.message},
    )
    db.commit()
    msg = quote(f"Загружено: {verdict.message}")
    return RedirectResponse(f"/cabinet/settings/branding?ok={msg}", status_code=303)


@router.post("/branding/{slot}/delete", response_class=HTMLResponse)
async def settings_branding_delete(
    slot: str,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    denied = _require_org_settings_admin(request, user, db)
    if denied is not None:
        return denied
    from app.services.branding import BrandingError, delete_slot
    from app.services.branding_access import assert_can_manage_branding

    org = get_org_for_user(db, user)
    gate = assert_can_manage_branding(db, org.id)
    if gate is not None:
        return gate
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    try:
        delete_slot(org.id, slot)
    except BrandingError as exc:
        from urllib.parse import quote

        return RedirectResponse(
            f"/cabinet/settings/branding?err={quote(str(exc))}", status_code=303
        )
    record_event(
        db,
        type="branding_delete",
        org_id=org.id,
        user_id=user.id,
        details={"slot": slot},
    )
    db.commit()
    return RedirectResponse("/cabinet/settings/branding?ok=Удалено", status_code=303)


@router.get("/branding/{slot}/preview.png")
def settings_branding_preview(
    slot: str,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    from fastapi.responses import Response

    from app.services.branding import preview_png_bytes

    org = get_org_for_user(db, user)
    if not user.is_org_admin:
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    data = preview_png_bytes(org.id, slot)
    if not data:
        raise HTTPException(status_code=404, detail="Нет изображения")
    return Response(content=data, media_type="image/png")


@router.post("/branding/check", response_class=HTMLResponse)
async def settings_branding_check(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    """Мгновенная проверка файла (HTMX) — вердикт без сохранения."""
    denied = _require_org_settings_admin(request, user, db)
    if denied is not None:
        return denied
    from app.services.branding import BrandingError, SLOTS, VerdictLevel, evaluate_image

    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    slot = str(form.get("slot") or "")
    if slot not in SLOTS:
        return PlainTextResponse("Неизвестный слот", status_code=400)
    upload = form.get("file")
    if upload is None:
        return PlainTextResponse("Нет файла", status_code=400)
    data = await upload.read()
    kind = SLOTS[slot]["kind"]
    try:
        verdict = evaluate_image(data, kind=kind)
    except BrandingError as exc:
        return HTMLResponse(
            f'<p class="badge danger">✗ Не принято — {exc}</p>', status_code=200
        )
    if verdict.level == VerdictLevel.excellent:
        cls, mark = "ok", "✓ Отлично"
    elif verdict.level == VerdictLevel.usable:
        cls, mark = "warn", "⚠ Пригодно, но…"
    else:
        cls, mark = "danger", "✗ Не принято"
    return HTMLResponse(
        f'<p class="badge {cls}">{mark} — {verdict.message}</p>'
        f'<p class="muted">{verdict.width}×{verdict.height} px ≈ '
        f"{verdict.print_w_mm}×{verdict.print_h_mm} мм при 300 dpi</p>"
    )


@router.get("/price", response_class=HTMLResponse)
def settings_price(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    denied = _require_org_settings_admin(request, user, db)
    if denied is not None:
        return denied
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
    denied = _require_org_settings_admin(request, user, db)
    if denied is not None:
        return denied
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
    denied = _require_org_settings_admin(request, user, db)
    if denied is not None:
        return denied
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
    denied = _require_org_settings_admin(request, user, db)
    if denied is not None:
        return denied
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
    return _page(
        request,
        user,
        org,
        db,
        "безопасность",
        retention_days=get_retention_days(org),
        security_base="/cabinet/settings/security",
        show_retention=True,
        show_admin_2fa_hint=False,
        security_events=events,
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
        flash_ok=flash_ok,
        flash_error=flash_error,
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


@router.post("/security/password", response_class=HTMLResponse)
async def settings_set_password(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    """Задать пароль (для аккаунтов, вошедших только через OAuth)."""
    org = get_org_for_user(db, user)
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
            return templates.TemplateResponse(
                request=request,
                name="cabinet/settings.html",
                context=_security_ctx(
                    request, user, org, db, flash_error="Неверный текущий пароль."
                ),
                status_code=401,
            )
    err = validate_password(password, email=user.email)
    if err:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_security_ctx(request, user, org, db, flash_error=err),
            status_code=400,
        )
    if password != password2:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_security_ctx(
                request, user, org, db, flash_error="Пароли не совпадают."
            ),
            status_code=400,
        )
    db_user.password_hash = hash_password(password)
    record_event(
        db,
        type="password_set",
        org_id=user.org_id,
        user_id=user.id,
        details={"ip": client_ip(request)},
        commit=False,
    )
    db.commit()
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings.html",
        context=_security_ctx(request, user, org, db, flash_ok="Пароль сохранён."),
    )


@router.post("/security/yandex/link")
async def settings_yandex_link(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    if not yandex_button_visible(db):
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_security_ctx(
                request, user, org, db, flash_error="Привязка Яндекс ID сейчас недоступна."
            ),
            status_code=503,
        )
    db_user = db.get(User, user.id)
    assert db_user is not None
    if user_yandex_identity(db, user.id):
        return RedirectResponse("/cabinet/settings/security", status_code=303)
    if not user_has_password(db_user):
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_security_ctx(
                request,
                user,
                org,
                db,
                flash_error="Сначала задайте пароль, затем привяжите Яндекс ID.",
            ),
            status_code=400,
        )
    password = str(form.get("password") or "")
    if not verify_password(password, db_user.password_hash):
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_security_ctx(
                request, user, org, db, flash_error="Неверный пароль."
            ),
            status_code=401,
        )
    request.session["yandex_oauth_intent"] = "link"
    request.session["yandex_oauth_link_user_id"] = user.id
    return RedirectResponse("/auth/yandex/start", status_code=303)


@router.post("/security/yandex/unlink", response_class=HTMLResponse)
async def settings_yandex_unlink(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    db_user = db.get(User, user.id)
    assert db_user is not None
    try:
        unlink_yandex(db, db_user)
    except ValueError as exc:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_security_ctx(request, user, org, db, flash_error=str(exc)),
            status_code=400,
        )
    record_event(
        db,
        type="oauth_unlinked",
        org_id=user.org_id,
        user_id=user.id,
        details={"provider": "yandex", "ip": client_ip(request)},
        commit=False,
    )
    db.commit()
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings.html",
        context=_security_ctx(
            request, user, org, db, flash_ok="Яндекс ID отвязан."
        ),
    )
