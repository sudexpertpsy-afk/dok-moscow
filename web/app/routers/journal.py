"""Журнал документов и поиск (W-06)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, require_org_user
from app.org_scope import get_org_for_user, list_counterparty_options, require_org_id
from app.nav_context import cabinet_nav
from app.security import get_csrf_token
from app.services.journal import distinct_templates, list_journal, parse_date
from app.services.templates import ensure_core_on_path, templates_dir
from app.templating import templates

router = APIRouter(prefix="/cabinet", tags=["journal"])


def _kinds_map(rows) -> dict[int, str]:
    ensure_core_on_path()
    from docfiller_core.contracts_registry import document_kind_from_registry
    from docfiller_core.template_manifest import document_kind

    root = templates_dir()
    out: dict[int, str] = {}
    for d in rows:
        kind = document_kind_from_registry(root, d.template) or document_kind(
            d.template, templates_dir=root
        )
        if kind and kind != "документ":
            out[d.id] = kind
    return out


def _page(request: Request, user: CurrentUser, org, db, active: str, **extra):
    ctx = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": user,
        "org": org,
        "nav": cabinet_nav(db, user),
        "active": active,
        "flash_error": None,
        "flash_ok": None,
    }
    ctx.update(extra)
    return ctx


def _journal_filters(request: Request):
    q = request.query_params
    date_from = parse_date(q.get("from"))
    date_to = parse_date(q.get("to"))
    template = (q.get("template") or "").strip() or None
    cp_raw = (q.get("counterparty_id") or "").strip()
    counterparty_id = int(cp_raw) if cp_raw.isdigit() else None
    try:
        page = int(q.get("page") or "1")
    except ValueError:
        page = 1
    return date_from, date_to, template, counterparty_id, cp_raw, max(1, page)


@router.get("/journal", response_class=HTMLResponse)
def journal_page(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    org_id = require_org_id(user)
    date_from, date_to, template, counterparty_id, cp_raw, page = _journal_filters(request)
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
        context=_page(request, user, org, db, "journal",
            rows=rows,
            total=total,
            page=page,
            pages=pages,
            templates_list=distinct_templates(db, org_id),
            counterparties=cps,
            cp_map=cp_map,
            kinds_map=_kinds_map(rows),
            filters={
                "from": request.query_params.get("from") or "",
                "to": request.query_params.get("to") or "",
                "template": template or "",
                "counterparty_id": cp_raw,
            },
        ),
    )


@router.get("/journal/rows", response_class=HTMLResponse)
def journal_rows(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    """HTMX-фрагмент следующей страницы журнала (W-31)."""
    org_id = require_org_id(user)
    date_from, date_to, template, counterparty_id, cp_raw, page = _journal_filters(request)
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
        name="cabinet/partials/journal_rows.html",
        context={
            "request": request,
            "csrf_token": get_csrf_token(request),
            "rows": rows,
            "page": page,
            "pages": pages,
            "cp_map": cp_map,
            "kinds_map": _kinds_map(rows),
            "filters": {
                "from": request.query_params.get("from") or "",
                "to": request.query_params.get("to") or "",
                "template": template or "",
                "counterparty_id": cp_raw,
            },
        },
    )


@router.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    from app.services.search import search_page as run_search_page

    org = get_org_for_user(db, user)
    q = (request.query_params.get("q") or "").strip()
    try:
        page = int(request.query_params.get("page") or "1")
    except ValueError:
        page = 1
    page_result = run_search_page(db, user, q, page=page, per_group=20)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/search.html",
        context=_page(
            request,
            user,
            org,
            db,
            "cabinet_search",
            result=page_result.core,
            q=q,
            page=page_result.page,
        ),
    )
