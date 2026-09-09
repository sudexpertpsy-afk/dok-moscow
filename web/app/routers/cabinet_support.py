"""Кабинет: поддержка и предложения улучшений."""

from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, require_csrf, require_org_user
from app.nav_context import cabinet_nav
from app.org_scope import get_org_for_user
from app.security import get_csrf_token
from app.services import support as support_svc
from app.services.support import (
    KIND_LABELS,
    STATUS_LABELS,
    SupportError,
    SupportRateLimitError,
    SupportTicketKind,
)
from app.templating import templates

router = APIRouter(prefix="/cabinet/support", tags=["cabinet-support"])


def _ctx(request: Request, user: CurrentUser, org, db: Session, **extra):
    settings = get_settings()
    base = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "user": user,
        "org": org,
        "nav": cabinet_nav(db, user),
        "active": "support",
        "flash_error": None,
        "flash_ok": None,
        "kind_labels": KIND_LABELS,
        "status_labels": STATUS_LABELS,
    }
    base.update(extra)
    return base


async def _read_upload(file: UploadFile | None) -> tuple[bytes | None, str | None]:
    if file is None or not file.filename:
        return None, None
    data = await file.read()
    if not data:
        return None, None
    return data, file.filename


@router.get("/", response_class=HTMLResponse)
def support_home(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    tickets = support_svc.list_for_org(db, org.id)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/support.html",
        context=_ctx(
            request,
            user,
            org,
            db,
            tickets=tickets,
            form_kind=SupportTicketKind.support.value,
            form_subject="",
            form_body="",
            ok=request.query_params.get("ok"),
            flash_ok=(
                "Обращение отправлено. Мы ответим в этом разделе и на e-mail."
                if request.query_params.get("ok") == "1"
                else None
            ),
        ),
    )


@router.get("/improve", response_class=HTMLResponse)
def support_improve(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/support_improve.html",
        context=_ctx(
            request,
            user,
            org,
            db,
            form_kind=SupportTicketKind.improvement.value,
            form_subject="",
            form_body="",
            page_url_default=str(request.headers.get("referer") or "/cabinet/"),
            ok=request.query_params.get("ok"),
            flash_ok=(
                "Предложение отправлено. Спасибо!"
                if request.query_params.get("ok") == "1"
                else None
            ),
        ),
    )


@router.post("/", response_class=HTMLResponse)
async def support_create(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    _csrf=Depends(require_csrf),
    kind: str = Form(...),
    subject: str = Form(""),
    body: str = Form(""),
    page_url: str = Form(""),
    attachment: UploadFile | None = File(None),
):
    org = get_org_for_user(db, user)
    settings = get_settings()
    att_bytes, att_name = await _read_upload(attachment)
    ua = (request.headers.get("user-agent") or "")[:512]
    try:
        ticket = support_svc.create_ticket(
            db,
            org_id=org.id,
            user_id=user.id,
            kind=kind,
            subject=subject,
            body=body,
            page_url=page_url or str(request.headers.get("referer") or ""),
            app_version=settings.app_version,
            user_agent=ua,
            attachment_bytes=att_bytes,
            attachment_filename=att_name,
        )
    except SupportRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except SupportError as exc:
        template = (
            "cabinet/support_improve.html"
            if (kind or "").strip() == SupportTicketKind.improvement.value
            else "cabinet/support.html"
        )
        tickets = support_svc.list_for_org(db, org.id)
        return templates.TemplateResponse(
            request=request,
            name=template,
            context=_ctx(
                request,
                user,
                org,
                db,
                tickets=tickets,
                form_kind=kind,
                form_subject=subject,
                form_body=body,
                page_url_default=page_url,
                flash_error=str(exc),
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    support_svc.notify_admin_new_ticket(settings, ticket)
    dest = (
        "/cabinet/support/improve?ok=1"
        if ticket.kind == SupportTicketKind.improvement
        else f"/cabinet/support/{ticket.id}?ok=1"
    )
    return RedirectResponse(dest, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{ticket_id:int}", response_class=HTMLResponse)
def support_view(
    ticket_id: int,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    ticket = support_svc.get_for_org(db, org.id, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    return templates.TemplateResponse(
        request=request,
        name="cabinet/support_view.html",
        context=_ctx(
            request,
            user,
            org,
            db,
            ticket=ticket,
            flash_ok=(
                "Обращение отправлено."
                if request.query_params.get("ok") == "1"
                else None
            ),
        ),
    )


@router.get("/{ticket_id:int}/attachment")
def support_attachment(
    ticket_id: int,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    ticket = support_svc.get_for_org(db, org.id, ticket_id)
    if ticket is None or not ticket.attachment_path:
        raise HTTPException(status_code=404, detail="Вложение не найдено")
    try:
        path = support_svc.resolve_attachment(org.id, ticket)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Вложение не найдено") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Вложение не найдено")
    media = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return FileResponse(path, media_type=media, filename=path.name)
