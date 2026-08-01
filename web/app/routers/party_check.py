"""Раздел кабинета «Проверка контрагента» (W-21)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, require_org_user
from app.models import CounterpartyType
from app.org_scope import get_org_for_user, list_counterparties
from app.nav_context import cabinet_nav
from app.security import check_csrf, get_csrf_token
from app.services.dadata import PartyCard, parse_party_suggestion
from app.services.package_master import SESSION_KEY, core_from_counterparty, CP_TO_TYPE
from app.services.party_check import (
    access_state,
    build_party_card_pdf,
    find_counterparty_by_inn,
    get_party_check,
    has_paid_access,
    list_party_checks,
    load_party_by_inn,
    record_party_check,
    search_parties,
    upsert_counterparty_from_card,
)
from app.templating import templates

router = APIRouter(prefix="/cabinet/party-check", tags=["party-check"])


def _page(request: Request, user: CurrentUser, org, db, **extra):
    ctx = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": user,
        "org": org,
        "nav": cabinet_nav(db, user),
        "active": "party_check",
        "flash_error": None,
        "flash_ok": None,
        "access": None,
    }
    ctx.update(extra)
    return ctx


def _card_from_session(request: Request) -> PartyCard | None:
    raw = request.session.get("party_check_card")
    if not isinstance(raw, dict):
        return None
    return parse_party_suggestion(raw)


def _store_card(request: Request, card: PartyCard) -> None:
    request.session["party_check_card"] = card.raw
    request.session["party_check_inn"] = card.inn


@router.get("/", response_class=HTMLResponse)
def party_check_home(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    state = access_state(db, org.id)
    if not has_paid_access(db, org.id):
        return templates.TemplateResponse(
            request=request,
            name="cabinet/party_check_paywall.html",
            context=_page(request, user, org, db, access=state),
        )
    return templates.TemplateResponse(
        request=request,
        name="cabinet/party_check_search.html",
        context=_page(request, user, org, db, access=state,
            query="",
            candidates=None,
            card=None,
        ),
    )


@router.post("/search", response_class=HTMLResponse)
async def party_check_search(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")

    if not has_paid_access(db, org.id):
        return RedirectResponse("/cabinet/party-check/", status_code=303)

    query = str(form.get("query") or "").strip()
    card, candidates, error = search_parties(
        db, org_id=org.id, user_id=user.id, query=query
    )
    state = access_state(db, org.id)

    if card is not None:
        _store_card(request, card)
        record_party_check(
            db, org_id=org.id, user_id=user.id, card=card, query=query
        )
        return RedirectResponse(
            f"/cabinet/party-check/card?inn={card.inn}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return templates.TemplateResponse(
        request=request,
        name="cabinet/party_check_search.html",
        context=_page(request, user, org, db, access=state,
            query=query,
            candidates=candidates or [],
            card=None,
            flash_error=error,
        ),
        status_code=400 if error and not candidates else 200,
    )


@router.get("/card", response_class=HTMLResponse)
def party_check_card(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    if not has_paid_access(db, org.id):
        return RedirectResponse("/cabinet/party-check/", status_code=303)

    inn = str(request.query_params.get("inn") or request.session.get("party_check_inn") or "")
    inn = "".join(ch for ch in inn if ch.isdigit())
    card = _card_from_session(request)
    if card is None or (inn and card.inn != inn):
        if not inn:
            return RedirectResponse("/cabinet/party-check/", status_code=303)
        card, _check, error = load_party_by_inn(
            db, org_id=org.id, user_id=user.id, inn=inn, query=inn
        )
        if card is None:
            state = access_state(db, org.id)
            return templates.TemplateResponse(
                request=request,
                name="cabinet/party_check_search.html",
                context=_page(request, user, org, db, access=state,
                    query=inn,
                    candidates=[],
                    flash_error=error or "Не найдено",
                ),
                status_code=404,
            )
        _store_card(request, card)

    existing = find_counterparty_by_inn(db, org.id, card.inn)
    state = access_state(db, org.id)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/party_check_card.html",
        context=_page(request, user, org, db, access=state,
            card=card,
            existing=existing,
            diffs=None,
        ),
    )


@router.post("/select", response_class=HTMLResponse)
async def party_check_select(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    if not has_paid_access(db, org.id):
        return RedirectResponse("/cabinet/party-check/", status_code=303)

    inn = "".join(ch for ch in str(form.get("inn") or "") if ch.isdigit())
    card, _check, error = load_party_by_inn(
        db, org_id=org.id, user_id=user.id, inn=inn, query=inn
    )
    if card is None:
        state = access_state(db, org.id)
        return templates.TemplateResponse(
            request=request,
            name="cabinet/party_check_search.html",
            context=_page(request, user, org, db, access=state,
                query=inn,
                candidates=[],
                flash_error=error or "Не найдено",
            ),
            status_code=404,
        )
    _store_card(request, card)
    return RedirectResponse(
        f"/cabinet/party-check/card?inn={card.inn}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/save", response_class=HTMLResponse)
async def party_check_save(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    if not has_paid_access(db, org.id):
        return RedirectResponse("/cabinet/party-check/", status_code=303)

    card = _card_from_session(request)
    if card is None:
        inn = "".join(ch for ch in str(form.get("inn") or "") if ch.isdigit())
        card, _, error = load_party_by_inn(
            db, org_id=org.id, user_id=user.id, inn=inn, query=inn
        )
        if card is None:
            raise HTTPException(status_code=400, detail=error or "Нет карточки")
        _store_card(request, card)

    cp, diffs, created = upsert_counterparty_from_card(db, org_id=org.id, card=card)
    state = access_state(db, org.id)
    flash = (
        "Контрагент добавлен в картотеку."
        if created
        else "Карточка контрагента обновлена."
    )
    return templates.TemplateResponse(
        request=request,
        name="cabinet/party_check_card.html",
        context=_page(request, user, org, db, access=state,
            card=card,
            existing=cp,
            diffs=diffs,
            flash_ok=flash,
        ),
    )


@router.get("/pdf")
def party_check_pdf(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    if not has_paid_access(db, org.id):
        raise HTTPException(status_code=403, detail="Недоступно на тарифе «Гость»")

    card = _card_from_session(request)
    if card is None:
        inn = "".join(ch for ch in str(request.query_params.get("inn") or "") if ch.isdigit())
        if not inn:
            raise HTTPException(status_code=404, detail="Нет карточки")
        card, _, error = load_party_by_inn(
            db, org_id=org.id, user_id=user.id, inn=inn, query=inn, record_journal=False
        )
        if card is None:
            raise HTTPException(status_code=404, detail=error or "Не найдено")

    pdf = build_party_card_pdf(card)
    filename = f"proverka_{card.inn or 'party'}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/package", response_class=HTMLResponse)
async def party_check_package(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    if not has_paid_access(db, org.id):
        return RedirectResponse("/cabinet/party-check/", status_code=303)

    card = _card_from_session(request)
    if card is None:
        inn = "".join(ch for ch in str(form.get("inn") or "") if ch.isdigit())
        card, _, error = load_party_by_inn(
            db, org_id=org.id, user_id=user.id, inn=inn, query=inn
        )
        if card is None:
            raise HTTPException(status_code=400, detail=error or "Нет карточки")

    cp, _, _ = upsert_counterparty_from_card(db, org_id=org.id, card=card)
    тип = CP_TO_TYPE.get(cp.type, "Юрлицо")
    if cp.type == CounterpartyType.fl and card.party_type == "INDIVIDUAL":
        тип = "Физлицо"
    elif cp.type == CounterpartyType.ul:
        тип = "Юрлицо"

    wizard = {
        "тип": тип,
        "counterparty_id": cp.id,
        "core_values": core_from_counterparty(тип, cp),
    }
    request.session[SESSION_KEY] = wizard
    return RedirectResponse("/cabinet/package/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/journal", response_class=HTMLResponse)
def party_check_journal(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    if not has_paid_access(db, org.id):
        return RedirectResponse("/cabinet/party-check/", status_code=303)

    cp_id_raw = str(request.query_params.get("counterparty_id") or "").strip()
    inn = str(request.query_params.get("inn") or "").strip()
    counterparty_id = int(cp_id_raw) if cp_id_raw.isdigit() else None
    rows = list_party_checks(
        db, org.id, counterparty_id=counterparty_id, inn=inn or None
    )
    cps = [c for c in list_counterparties(db, org.id) if c.inn]
    state = access_state(db, org.id)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/party_check_journal.html",
        context=_page(request, user, org, db, access=state,
            rows=rows,
            counterparties=cps,
            filter_cp_id=counterparty_id,
            filter_inn=inn,
        ),
    )


@router.get("/journal/{check_id}", response_class=HTMLResponse)
def party_check_journal_item(
    check_id: int,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    if not has_paid_access(db, org.id):
        return RedirectResponse("/cabinet/party-check/", status_code=303)
    row = get_party_check(db, org.id, check_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    card = parse_party_suggestion(row.snapshot) if row.snapshot else None
    state = access_state(db, org.id)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/party_check_card.html",
        context=_page(request, user, org, db, access=state,
            card=card,
            existing=find_counterparty_by_inn(db, org.id, row.inn) if row.inn else None,
            diffs=None,
            journal_row=row,
            flash_ok=f"Снимок проверки от {row.checked_at.strftime('%d.%m.%Y %H:%M')} UTC",
        ),
    )
