"""Страницы генерации отдельных документов (W-03)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, require_csrf, require_org_user
from app.models import Document, DocumentFormat, JobType
from app.org_scope import get_document_for_org, get_org_for_user, require_org_id
from app.security import check_csrf, get_csrf_token
from app.services.counters import allocate_number
from app.services.jobs import enqueue_job
from app.services.journal import delete_org_document
from app.services.limits import assert_can_generate
from app.services.templates import (
    absolute_file,
    generate_docx,
    list_templates_for_org,
    template_variables,
    templates_grouped,
)
from app.templating import templates
from app.nav_context import cabinet_nav

router = APIRouter(prefix="/cabinet/documents", tags=["documents"])

_NUMBER_FIELDS = {
    "номер_договора": "dogovor",
    "номер_счёта": "schet",
    "номер_акта": "akt",
    "номер_пко": "pko",
    "номер_допсоглашения": "dopsogl",
    "исх_номер": "ishod",
}


def _page(request: Request, user: CurrentUser, org, db, **extra):
    ctx = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": user,
        "org": org,
        "nav": cabinet_nav(db, user),
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
    items = list_templates_for_org(org.id)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/documents_index.html",
        context=_page(
            request,
            user,
            org,
            db,
            templates_list=items,
            templates_groups=templates_grouped(items),
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
    from app.models import LegalAct
    from app.services.legal_public import normative_for_template
    from sqlalchemy import select

    slugs = normative_for_template(template_name)
    normative_links = []
    for slug in slugs:
        act = db.scalar(select(LegalAct).where(LegalAct.slug == slug))
        if act:
            normative_links.append({"slug": act.slug, "title": act.title})
    from app.services.form_assist import enrich_form_context
    from app.services.settings_svc import bank_is_complete, ensure_requisites, is_bill_template

    assist = enrich_form_context(db, org.id, variables, {}, template_name=template_name)
    flash_error = None
    if is_bill_template(template_name) and not bank_is_complete(ensure_requisites(org)):
        flash_error = (
            "В настройках не заполнены банковские реквизиты организации "
            "(Банк → расчётный счёт, БИК, корр. счёт). Без них в счёте останутся пустые поля. "
            "Заполните раздел «Настройки → Банк»."
        )
    return templates.TemplateResponse(
        request=request,
        name="cabinet/document_form.html",
        context=_page(
            request,
            user,
            org,
            db,
            template_name=template_name,
            variables=variables,
            values=assist["values"],
            field_meta=assist["field_meta"],
            number_peeks=assist["number_peeks"],
            standard_vars=assist["standard_vars"],
            org_vars=assist["org_vars"],
            normative_links=normative_links,
            flash_error=flash_error,
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

    from app.services.form_assist import enrich_form_context
    from app.services.org_fields import org_field_map
    from app.services.settings_svc import bank_is_complete, ensure_requisites, is_bill_template

    org_map = org_field_map(db, org.id)
    assist_meta = enrich_form_context(db, org.id, variables, {}, template_name=template_name)
    field_meta = assist_meta["field_meta"]

    if is_bill_template(template_name) and not bank_is_complete(ensure_requisites(org)):
        assist = enrich_form_context(db, org.id, variables, {}, template_name=template_name)
        return templates.TemplateResponse(
            request=request,
            name="cabinet/document_form.html",
            context=_page(
                request,
                user,
                org,
                db,
                template_name=template_name,
                variables=variables,
                values=assist["values"],
                field_meta=assist["field_meta"],
                number_peeks=assist["number_peeks"],
                standard_vars=assist["standard_vars"],
                org_vars=assist["org_vars"],
                flash_error=(
                    "Сначала заполните банковские реквизиты в «Настройки → Банк» "
                    "(расчётный счёт, банк, БИК, корр. счёт) — иначе в счёте они будут пустыми."
                ),
            ),
            status_code=400,
        )

    context: dict = {}
    for var in variables:
        meta = field_meta.get(var) or {}
        if meta.get("is_checkbox"):
            context[var] = "да" if form.get(var) else ""
        else:
            context[var] = str(form.get(var) or "").strip()
        of = org_map.get(var)
        if of is not None and of.required and not str(context[var] or "").strip():
            assist = enrich_form_context(db, org.id, variables, context, template_name=template_name)
            return templates.TemplateResponse(
                request=request,
                name="cabinet/document_form.html",
                context=_page(
                    request,
                    user,
                    org,
                    db,
                    template_name=template_name,
                    variables=variables,
                    values=assist["values"],
                    field_meta=assist["field_meta"],
                    number_peeks=assist["number_peeks"],
                    standard_vars=assist["standard_vars"],
                    org_vars=assist["org_vars"],
                    flash_error=f"Заполните обязательное поле «{of.label}»",
                ),
                status_code=400,
            )

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

    from datetime import date as _date

    for var in variables:
        meta = field_meta.get(var) or {}
        key = meta.get("counter_key")
        if not key or var in _NUMBER_FIELDS:
            continue
        raw = context.get(var) or ""
        if not raw or str(raw).lower() in {"auto", "авто", "+"}:
            suffix = f"/{_date.today().strftime('%y')}" if meta.get("counter_year_suffix") else ""
            _, formatted = allocate_number(db, org_id, key, prefix="", suffix=suffix)
            context[var] = formatted
            if number is None:
                number = formatted

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
        assist = enrich_form_context(db, org.id, variables, context, template_name=template_name)
        return templates.TemplateResponse(
            request=request,
            name="cabinet/document_form.html",
            context=_page(
                request,
                user,
                org,
                db,
                template_name=template_name,
                variables=variables,
                values=assist["values"],
                field_meta=assist["field_meta"],
                number_peeks=assist["number_peeks"],
                standard_vars=assist["standard_vars"],
                org_vars=assist["org_vars"],
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
        context=_page(request, user, org, db, doc=doc),
    )


@router.get("/{doc_id}/download")
def document_download(
    doc_id: int,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    doc = get_document_for_org(db, require_org_id(user), doc_id)
    try:
        path = absolute_file(doc)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Файл не найден") from exc
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
    get_document_for_org(db, org_id, doc_id)
    job = enqueue_job(
        db,
        org_id=org_id,
        user_id=user.id,
        job_type=JobType.document_pdf,
        payload={"document_id": doc_id},
    )
    return RedirectResponse(
        f"/cabinet/jobs/{job.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{doc_id}/delete", response_class=HTMLResponse)
async def document_delete(
    doc_id: int,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    """Удалить готовый документ из журнала (файл + запись)."""
    org_id = require_org_id(user)
    doc = get_document_for_org(db, org_id, doc_id)
    try:
        delete_org_document(db, org_id=org_id, doc=doc, user_id=user.id)
        db.commit()
    except OSError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="Не удалось удалить файл документа"
        ) from exc

    # HTMX из журнала: убрать строку таблицы
    if request.headers.get("hx-request"):
        return HTMLResponse("")
    return RedirectResponse(
        "/cabinet/journal?ok=deleted",
        status_code=status.HTTP_303_SEE_OTHER,
    )
