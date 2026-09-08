"""Картотека контрагентов + HTMX-подсказки DaData (W-05)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, require_org_user
from app.models import Counterparty, CounterpartySource, CounterpartyType, Document
from app.org_scope import get_counterparty_for_org, get_org_for_user, list_counterparties, require_org_id
from app.privacy import counterparty_list_item, mask_address, mask_passport
from app.nav_context import cabinet_nav
from app.rate_limit import LoginRateLimiter
from app.security import check_csrf, get_csrf_token
from app.services.counterparties import (
    apply_fields,
    form_to_fields,
    parse_type,
    validate_counterparty_form,
)
from app.services.dadata import (
    bank_to_fields,
    party_to_counterparty_fields,
    suggest,
)
from app.templating import templates

# F-06: DaData-прокси — 30 запросов / мин на пользователя (поверх дневного лимита org)
dadata_limiter = LoginRateLimiter(30, 60, name="dadata_suggest")


def _dadata_rate_ok(user: CurrentUser) -> bool:
    key = f"user:{user.id}"
    if dadata_limiter.is_blocked(key):
        return False
    dadata_limiter.register_failure(key)
    return True

router = APIRouter(prefix="/cabinet/counterparties", tags=["counterparties"])

TYPE_LABELS = {
    CounterpartyType.fl: "Физлицо",
    CounterpartyType.ul: "Юрлицо",
    CounterpartyType.expert: "Эксперт",
}


def _page(request: Request, user: CurrentUser, org, db, **extra):
    ctx = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": user,
        "org": org,
        "nav": cabinet_nav(db, user),
        "active": "counterparties",
        "flash_error": None,
        "flash_ok": None,
        "type_labels": TYPE_LABELS,
    }
    ctx.update(extra)
    return ctx


@router.get("/", response_class=HTMLResponse)
def cp_list(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    rows = list_counterparties(db, org.id)
    items = [counterparty_list_item(c) for c in rows]
    return templates.TemplateResponse(
        request=request,
        name="cabinet/counterparties_list.html",
        context=_page(request, user, org, db, items=items),
    )


@router.get("/new", response_class=HTMLResponse)
def cp_new_form(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    тип = parse_type(request.query_params.get("type") or "ul")
    return templates.TemplateResponse(
        request=request,
        name="cabinet/counterparty_form.html",
        context=_page(request, user, org, db, mode="new",
            cp=None,
            тип=тип,
            values={},
            dadata_enabled=bool(get_settings().dadata_key),
        ),
    )


@router.post("/new", response_class=HTMLResponse)
async def cp_create(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    тип = parse_type(str(form.get("type") or "fl"))
    fields = form_to_fields(form)
    source = (
        CounterpartySource.dadata
        if str(form.get("source") or "") == "dadata"
        else CounterpartySource.manual
    )
    errors = validate_counterparty_form(тип, fields)
    if errors:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/counterparty_form.html",
            context=_page(request, user, org, db, mode="new",
                cp=None,
                тип=тип,
                values=fields,
                flash_error="; ".join(errors),
                dadata_enabled=bool(get_settings().dadata_key),
            ),
            status_code=400,
        )
    cp = Counterparty(org_id=org.id, type=тип, source=source)
    apply_fields(cp, fields, source=source)
    db.add(cp)
    db.commit()
    db.refresh(cp)
    return RedirectResponse(f"/cabinet/counterparties/{cp.id}", status_code=303)


# HTMX DaData — до маршрутов /{cp_id}
@router.get("/suggest/party", response_class=HTMLResponse)
def suggest_party(
    request: Request,
    q: str = "",
    inn: str = "",
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    if not _dadata_rate_ok(user):
        return HTMLResponse("<p class='muted'>Слишком много запросов. Подождите минуту.</p>", status_code=429)
    # hx-include с name="инн" из настроек организации
    inn_ru = str(request.query_params.get("инн") or "")
    items = suggest(
        db,
        org_id=require_org_id(user),
        user_id=user.id,
        kind="party",
        query=q or inn or inn_ru,
    )
    return templates.TemplateResponse(
        request=request,
        name="cabinet/partials/suggest_party.html",
        context={
            "request": request,
            "items": items,
            "mapped": [party_to_counterparty_fields(i) for i in items],
        },
    )


@router.get("/suggest/address", response_class=HTMLResponse)
def suggest_address(
    request: Request,
    q: str = "",
    address: str = "",
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    if not _dadata_rate_ok(user):
        return HTMLResponse("<p class='muted'>Слишком много запросов. Подождите минуту.</p>", status_code=429)
    # hx-include с кириллическими name из «Настройки → Реквизиты»
    addr_ru = str(
        request.query_params.get("юр_адрес")
        or request.query_params.get("почтовый_адрес")
        or ""
    )
    items = suggest(
        db,
        org_id=require_org_id(user),
        user_id=user.id,
        kind="address",
        query=q or address or addr_ru,
    )
    return templates.TemplateResponse(
        request=request,
        name="cabinet/partials/suggest_address.html",
        context={"request": request, "items": items},
    )


@router.get("/suggest/bank", response_class=HTMLResponse)
def suggest_bank(
    request: Request,
    q: str = "",
    bank_bik: str = "",
    bank_name: str = "",
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    if not _dadata_rate_ok(user):
        return HTMLResponse("<p class='muted'>Слишком много запросов. Подождите минуту.</p>", status_code=429)
    query = (q or bank_bik or bank_name or "").strip()
    items = suggest(
        db,
        org_id=require_org_id(user),
        user_id=user.id,
        kind="bank",
        query=query,
    )
    mapped = [bank_to_fields(i) for i in items]
    # Точный БИК (9 цифр) и один результат — сразу подставить без клика
    auto_apply = bool(mapped) and len(mapped) == 1 and query.isdigit() and len(query) == 9
    return templates.TemplateResponse(
        request=request,
        name="cabinet/partials/suggest_bank.html",
        context={
            "request": request,
            "items": items,
            "mapped": mapped,
            "auto_apply": auto_apply,
        },
    )


@router.get("/{cp_id}", response_class=HTMLResponse)
def cp_view(
    cp_id: int,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    cp = get_counterparty_for_org(db, require_org_id(user), cp_id)
    docs = list(
        db.scalars(
            select(Document)
            .where(Document.org_id == org.id, Document.counterparty_id == cp.id)
            .order_by(Document.id.desc())
            .limit(50)
        ).all()
    )
    return templates.TemplateResponse(
        request=request,
        name="cabinet/counterparty_view.html",
        context=_page(request, user, org, db, cp=cp,
            documents=docs,
            passport_masked=mask_passport(cp.passport_series, cp.passport_number),
            address_masked=mask_address(cp.address),
        ),
    )


@router.get("/{cp_id}/edit", response_class=HTMLResponse)
def cp_edit_form(
    cp_id: int,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    cp = get_counterparty_for_org(db, require_org_id(user), cp_id)
    values = {k: getattr(cp, k) for k in form_to_fields({}).keys()}
    return templates.TemplateResponse(
        request=request,
        name="cabinet/counterparty_form.html",
        context=_page(request, user, org, db, mode="edit",
            cp=cp,
            тип=cp.type,
            values=values,
            dadata_enabled=bool(get_settings().dadata_key),
        ),
    )


@router.post("/{cp_id}", response_class=HTMLResponse)
async def cp_update(
    cp_id: int,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    cp = get_counterparty_for_org(db, require_org_id(user), cp_id)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    тип = parse_type(str(form.get("type") or cp.type.value))
    fields = form_to_fields(form)
    errors = validate_counterparty_form(тип, fields)
    if errors:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/counterparty_form.html",
            context=_page(request, user, org, db, mode="edit",
                cp=cp,
                тип=тип,
                values=fields,
                flash_error="; ".join(errors),
                dadata_enabled=bool(get_settings().dadata_key),
            ),
            status_code=400,
        )
    cp.type = тип
    apply_fields(cp, fields)
    db.commit()
    return RedirectResponse(f"/cabinet/counterparties/{cp.id}", status_code=303)


@router.post("/{cp_id}/delete")
async def cp_delete(
    cp_id: int,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    cp = get_counterparty_for_org(db, require_org_id(user), cp_id)
    db.delete(cp)
    db.commit()
    return RedirectResponse("/cabinet/counterparties/", status_code=303)
