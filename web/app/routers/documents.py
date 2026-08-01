"""Страницы генерации отдельных документов (W-03)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, require_org_user
from app.models import Document, DocumentFormat
from app.org_scope import get_document_for_org, get_org_for_user, require_org_id
from app.security import check_csrf, get_csrf_token
from app.services.counters import allocate_number
from app.services.gotenberg import GotenbergError, convert_docx_to_pdf
from app.services.limits import assert_can_generate, needs_watermark
from app.services.templates import (
    absolute_file,
    generate_docx,
    list_templates_for_org,
    template_variables,
)
from app.services.watermark import apply_guest_watermark
from app.templating import templates
from app.routers.cabinet import NAV

router = APIRouter(prefix="/cabinet/documents", tags=["documents"])

_NUMBER_FIELDS = {
    "номер_договора": "dogovor",
    "номер_счёта": "schet",
    "номер_акта": "akt",
    "номер_пко": "pko",
    "номер_допсоглашения": "dopsogl",
}


def _page(request: Request, user: CurrentUser, org, **extra):
    ctx = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": user,
        "org": org,
        "nav": NAV,
        "active": "documents",
        "flash_error": None,
        "flash_ok": None,
    }
    ctx.update(extra)
    return ctx


@router.get("/", response_class=HTMLResponse)
def documents_index(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/documents_index.html",
        context=_page(
            request,
            user,
            org,
            templates_list=list_templates_for_org(org.id),
        ),
    )


@router.get("/new/{template_name}", response_class=HTMLResponse)
def document_form(
    template_name: str,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    try:
        variables = template_variables(template_name, org.requisites, org_id=org.id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Шаблон не найден") from exc
    return templates.TemplateResponse(
        request=request,
        name="cabinet/document_form.html",
        context=_page(
            request,
            user,
            org,
            template_name=template_name,
            variables=variables,
            values={},
        ),
    )


@router.post("/new/{template_name}", response_class=HTMLResponse)
async def document_generate(
    template_name: str,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    org_id = require_org_id(user)
    try:
        variables = template_variables(template_name, org.requisites, org_id=org.id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Шаблон не найден") from exc

    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")

    assert_can_generate(db, org_id)

    context: dict = {}
    for var in variables:
        context[var] = str(form.get(var) or "").strip()

    number = None
    for field, key in _NUMBER_FIELDS.items():
        if field in context:
            raw = context[field]
            if not raw or raw.lower() in {"auto", "авто", "+"}:
                _, formatted = allocate_number(db, org_id, key, prefix="")
                context[field] = formatted
                if field == "номер_договора" or number is None:
                    number = formatted
            else:
                number = number or raw

    try:
        doc = generate_docx(
            db=db,
            org=org,
            user_id=user.id,
            template_name=template_name,
            context=context,
            number=number,
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/document_form.html",
            context=_page(
                request,
                user,
                org,
                template_name=template_name,
                variables=variables,
                values=context,
                flash_error=f"Ошибка генерации: {exc}",
            ),
            status_code=400,
        )

    return RedirectResponse(
        f"/cabinet/documents/{doc.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/{doc_id}", response_class=HTMLResponse)
def document_view(
    doc_id: int,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    doc = get_document_for_org(db, require_org_id(user), doc_id)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/document_view.html",
        context=_page(request, user, org, doc=doc),
    )


@router.get("/{doc_id}/download")
def document_download(
    doc_id: int,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    doc = get_document_for_org(db, require_org_id(user), doc_id)
    path = absolute_file(doc)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.post("/{doc_id}/pdf")
async def document_to_pdf(
    doc_id: int,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")

    org_id = require_org_id(user)
    doc = get_document_for_org(db, org_id, doc_id)
    docx_path = absolute_file(doc)
    pdf_path = docx_path.with_suffix(".pdf")
    try:
        convert_docx_to_pdf(docx_path, pdf_path)
        if needs_watermark(db, org_id):
            apply_guest_watermark(pdf_path)
    except GotenbergError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    settings = get_settings()
    rel = str(pdf_path.relative_to(settings.files_root))
    pdf_doc = Document(
        org_id=org_id,
        contract_id=doc.contract_id,
        counterparty_id=doc.counterparty_id,
        template=doc.template,
        number=doc.number,
        file_path=rel,
        format=DocumentFormat.pdf,
        context=doc.context,
        created_by=user.id,
    )
    db.add(pdf_doc)
    db.commit()
    db.refresh(pdf_doc)
    return RedirectResponse(
        f"/cabinet/documents/{pdf_doc.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
