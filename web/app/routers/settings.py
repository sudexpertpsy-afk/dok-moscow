"""Настройки организации и счётчики (W-06)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, require_org_user
from app.org_scope import get_org_for_user, list_events, require_org_id
from app.nav_context import cabinet_nav
from app.security import check_csrf, get_csrf_token
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

_AUTH_EVENT_TYPES = {
    "login_success",
    "login_failure",
    "logout",
    "password_reset_request",
    "password_reset",
    "retention_purge",
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


@router.get("/security", response_class=HTMLResponse)
def settings_security(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    events = [
        e
        for e in list_events(db, require_org_id(user), limit=80)
        if e.type in _AUTH_EVENT_TYPES
    ]
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings.html",
        context=_page(request, user, org, db, "безопасность",
            retention_days=get_retention_days(org),
            security_events=events,
        ),
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
        events = [
            e
            for e in list_events(db, require_org_id(user), limit=80)
            if e.type in _AUTH_EVENT_TYPES
        ]
        return templates.TemplateResponse(
            request=request,
            name="cabinet/settings.html",
            context=_page(request, user, org, db, "безопасность",
                retention_days=get_retention_days(org),
                security_events=events,
                flash_error="Укажите срок в днях от 0 (не удалять) до 36500.",
            ),
            status_code=400,
        )
    set_retention_days(org, days)
    db.add(org)
    db.commit()
    db.refresh(org)
    events = [
        e
        for e in list_events(db, require_org_id(user), limit=80)
        if e.type in _AUTH_EVENT_TYPES
    ]
    return templates.TemplateResponse(
        request=request,
        name="cabinet/settings.html",
        context=_page(request, user, org, db, "безопасность",
            retention_days=get_retention_days(org),
            security_events=events,
            flash_ok="Срок хранения файлов сохранён.",
        ),
    )
