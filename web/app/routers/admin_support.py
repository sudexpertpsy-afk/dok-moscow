"""Админка: очередь обращений поддержки и улучшений."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, require_csrf, require_service_admin
from app.nav_context import admin_nav
from app.security import get_csrf_token
from app.services import support as support_svc
from app.services.leads import count_new_leads
from app.services.support import (
    KIND_FILTERS,
    KIND_LABELS,
    STATUS_FILTERS,
    STATUS_LABELS,
    SupportError,
)
from app.templating import templates

router = APIRouter(prefix="/admin/support", tags=["admin-support"])


def _ctx(request: Request, user: CurrentUser, db: Session, **extra):
    data = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": user,
        "admin_nav": admin_nav(user),
        "active": "admin_support",
        "flash_error": None,
        "flash_ok": None,
        "new_leads_count": count_new_leads(db),
        "new_support_count": support_svc.count_new(db),
        "kind_labels": KIND_LABELS,
        "status_labels": STATUS_LABELS,
        "kind_filters": KIND_FILTERS,
        "status_filters": STATUS_FILTERS,
    }
    data.update(extra)
    return data


@router.get("/", response_class=HTMLResponse)
def admin_support_list(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    kind: str = Query(""),
    status_filter: str = Query("", alias="status"),
):
    try:
        tickets = support_svc.list_admin(
            db,
            kind=kind or None,
            status=status_filter or None,
        )
    except SupportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request=request,
        name="admin/support.html",
        context=_ctx(
            request,
            user,
            db,
            tickets=tickets,
            filter_kind=kind or "",
            filter_status=status_filter or "",
            flash_ok=(
                "Обращение обновлено."
                if request.query_params.get("ok") == "1"
                else None
            ),
        ),
    )


@router.get("/{ticket_id:int}", response_class=HTMLResponse)
def admin_support_detail(
    ticket_id: int,
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    ticket = support_svc.get_admin(db, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    return templates.TemplateResponse(
        request=request,
        name="admin/support_detail.html",
        context=_ctx(
            request,
            user,
            db,
            ticket=ticket,
            flash_ok=(
                "Сохранено." if request.query_params.get("ok") == "1" else None
            ),
        ),
    )


@router.post("/{ticket_id:int}", response_class=HTMLResponse)
def admin_support_update(
    ticket_id: int,
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _csrf=Depends(require_csrf),
    status_value: str = Form(..., alias="status"),
    admin_note: str = Form(""),
    admin_reply: str = Form(""),
):
    ticket = support_svc.get_admin(db, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    try:
        ticket, status_changed = support_svc.update_ticket_admin(
            db,
            ticket,
            status=status_value,
            admin_note=admin_note,
            admin_reply=admin_reply,
            actor_user_id=user.id,
        )
    except SupportError as exc:
        return templates.TemplateResponse(
            request=request,
            name="admin/support_detail.html",
            context=_ctx(
                request,
                user,
                db,
                ticket=ticket,
                flash_error=str(exc),
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if status_changed:
        email = ticket.user.email if ticket.user is not None else None
        support_svc.notify_user_status_change(
            get_settings(), ticket, user_email=email
        )
    return RedirectResponse(
        f"/admin/support/{ticket.id}?ok=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/{ticket_id:int}/attachment")
def admin_support_attachment(
    ticket_id: int,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    ticket = support_svc.get_admin(db, ticket_id)
    if ticket is None or not ticket.attachment_path:
        raise HTTPException(status_code=404, detail="Вложение не найдено")
    try:
        path = support_svc.resolve_attachment(ticket.org_id, ticket)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Вложение не найдено") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Вложение не найдено")
    media = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return FileResponse(path, media_type=media, filename=path.name)
