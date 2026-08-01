"""Журнал документов и поиск (W-06)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, require_org_user
from app.org_scope import get_org_for_user, list_counterparty_options, require_org_id
from app.routers.cabinet import NAV
from app.security import get_csrf_token
from app.services.journal import distinct_templates, list_journal, parse_date, search_all
from app.templating import templates

router = APIRouter(prefix="/cabinet", tags=["journal"])


def _page(request: Request, user: CurrentUser, org, active: str, **extra):
    ctx = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": user,
        "org": org,
        "nav": NAV,
        "active": active,
        "flash_error": None,
        "flash_ok": None,
    }
    ctx.update(extra)
    return ctx


@router.get("/journal", response_class=HTMLResponse)
def journal_page(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    org_id = require_org_id(user)
    q = request.query_params
    date_from = parse_date(q.get("from"))
    date_to = parse_date(q.get("to"))
    template = (q.get("template") or "").strip() or None
    cp_raw = (q.get("counterparty_id") or "").strip()
    counterparty_id = int(cp_raw) if cp_raw.isdigit() else None
    page = int(q.get("page") or "1")
    per_page = 20

    rows, total = list_journal(
        db,
        org_id,
        date_from=date_from,
        date_to=date_to,
        template=template,
        counterparty_id=counterparty_id,
        page=page,
        per_page=per_page,
    )
    pages = max(1, (total + per_page - 1) // per_page)
    cps = list_counterparty_options(db, org_id)
    cp_map = {c.id: (c.name or c.fio or f"#{c.id}") for c in cps}

    return templates.TemplateResponse(
        request=request,
        name="cabinet/journal.html",
        context=_page(
            request,
            user,
            org,
            "journal",
            rows=rows,
            total=total,
            page=page,
            pages=pages,
            templates_list=distinct_templates(db, org_id),
            counterparties=cps,
            cp_map=cp_map,
            filters={
                "from": q.get("from") or "",
                "to": q.get("to") or "",
                "template": template or "",
                "counterparty_id": cp_raw,
            },
        ),
    )


@router.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    q = (request.query_params.get("q") or "").strip()
    result = search_all(db, require_org_id(user), q)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/search.html",
        context=_page(request, user, org, "journal", result=result, q=q),
    )
