"""HTMX/JSON подсказки и linked-поля форм (T8)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import CurrentUser, require_org_user
from app.org_scope import require_org_id
from app.services.form_assist import (
    HISTORY_FIELDS,
    history_suggest,
    linked_values,
    peek_numbers_for,
)
from app.templating import templates

router = APIRouter(prefix="/cabinet/form-assist", tags=["form-assist"])


@router.get("/suggest", response_class=HTMLResponse)
def suggest_history(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    field: str = Query(""),
    q: str = Query(""),
):
    org_id = require_org_id(user)
    field = (field or "").strip()
    if field not in HISTORY_FIELDS:
        return HTMLResponse("")
    items = history_suggest(db, org_id, field, q, limit=8)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/partials/suggest_field.html",
        context={"items": items, "field": field},
    )


@router.get("/linked")
def linked_recalc(
    user: CurrentUser = Depends(require_org_user),
    src: str = Query(""),
    value: str = Query(""),
    targets: str = Query(""),
    only_empty: int = Query(1),
):
    """JSON: пересчёт связанных полей."""
    tgt = [t.strip() for t in (targets or "").split(",") if t.strip()]
    result = linked_values(
        src.strip(),
        value,
        targets=tgt or None,
        only_empty=bool(only_empty),
    )
    return JSONResponse(result)


@router.get("/peek-numbers")
def peek_numbers_api(
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    fields: str = Query(""),
):
    org_id = require_org_id(user)
    names = [f.strip() for f in (fields or "").split(",") if f.strip()]
    return JSONResponse(peek_numbers_for(db, org_id, names))
