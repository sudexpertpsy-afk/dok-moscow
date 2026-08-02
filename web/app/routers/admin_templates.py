"""Админка: каталог шаблонов DOCX — загрузка, переименование, удаление."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import CurrentUser, require_csrf, require_service_admin
from app.routers.admin import _ctx
from app.services.audit import record_event
from app.services.template_admin import (
    CONTRACT_TYPE_LABELS,
    TemplateAdminError,
    delete_template,
    ensure_writable_templates_dir,
    list_admin_templates,
    rename_template,
    save_upload,
    set_contract_membership,
)
from app.templating import templates

router = APIRouter(prefix="/admin", tags=["admin-templates"])


def _page(request: Request, user: CurrentUser, **extra):
    writable, write_detail = ensure_writable_templates_dir()
    items = list_admin_templates()
    for it in items:
        if it.get("mtime"):
            it["mtime_label"] = datetime.fromtimestamp(
                it["mtime"], tz=timezone.utc
            ).strftime("%d.%m.%Y %H:%M")
        else:
            it["mtime_label"] = "—"
        it["size_kb"] = max(1, int(round((it.get("size") or 0) / 1024))) if it.get("size") else 0
    return _ctx(
        request,
        user,
        "templates",
        items=items,
        contract_type_labels=CONTRACT_TYPE_LABELS,
        writable=writable,
        write_detail=write_detail,
        **extra,
    )


@router.get("/templates", response_class=HTMLResponse)
def templates_page(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
):
    ok = request.query_params.get("ok")
    err = request.query_params.get("err")
    flash_ok = None
    flash_error = None
    if ok == "upload":
        flash_ok = "Шаблон загружен."
    elif ok == "rename":
        flash_ok = "Название шаблона обновлено."
    elif ok == "delete":
        flash_ok = "Шаблон удалён."
    elif ok == "role":
        flash_ok = "Привязка к комплекту обновлена."
    if err:
        flash_error = err
    return templates.TemplateResponse(
        request=request,
        name="admin/templates.html",
        context=_page(request, user, flash_ok=flash_ok, flash_error=flash_error),
    )


@router.get("/templates/fields", response_class=HTMLResponse)
def system_fields_page(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
):
    from app.services.org_fields import FIELD_TYPE_LABELS
    from app.services.templates import ensure_core_on_path

    ensure_core_on_path()
    from docfiller_core.system_fields import list_standard_fields

    ok = request.query_params.get("ok")
    flash_ok = {
        "upsert": "Поле сохранено в системный реестр.",
        "delete": "Поле удалено из реестра.",
    }.get(ok or "")
    return templates.TemplateResponse(
        request=request,
        name="admin/system_fields.html",
        context=_page(
            request,
            user,
            flash_ok=flash_ok,
            fields=list_standard_fields(),
            field_type_labels={k.value: v for k, v in FIELD_TYPE_LABELS.items()},
        ),
    )


@router.post("/templates/fields/upsert", response_class=HTMLResponse)
def system_fields_upsert(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    name: str = Form(...),
    label: str = Form(...),
    field_type: str = Form("string"),
    required: str = Form(""),
    default: str = Form(""),
    hint: str = Form(""),
    options: str = Form(""),
    _: None = Depends(require_csrf),
):
    from app.services.org_fields import parse_options
    from app.services.templates import ensure_core_on_path

    ensure_core_on_path()
    from docfiller_core.system_fields import FIELD_TYPES, upsert_standard_field

    n = (name or "").strip()
    if not n:
        return _error(request, user, "Укажите имя поля")
    ftype = (field_type or "string").strip()
    if ftype not in FIELD_TYPES:
        return _error(request, user, "Неверный тип поля")
    opts = parse_options(options)
    try:
        upsert_standard_field(
            {
                "name": n,
                "label": label,
                "type": ftype,
                "required": required in {"1", "on", "true", "да"},
                "default": default,
                "hint": hint,
                "options": opts or [],
            }
        )
    except ValueError as exc:
        return _error(request, user, str(exc))
    record_event(
        db,
        type="admin_system_field_upsert",
        org_id=None,
        user_id=user.id,
        details={"name": n, "type": ftype},
    )
    return RedirectResponse("/admin/templates/fields?ok=upsert", status_code=303)


@router.post("/templates/fields/delete", response_class=HTMLResponse)
def system_fields_delete(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    name: str = Form(...),
    _: None = Depends(require_csrf),
):
    from app.services.templates import ensure_core_on_path

    ensure_core_on_path()
    from docfiller_core.system_fields import delete_standard_field

    n = (name or "").strip()
    try:
        delete_standard_field(n)
    except KeyError:
        return _error(request, user, "Поле не найдено в реестре")
    record_event(
        db,
        type="admin_system_field_delete",
        org_id=None,
        user_id=user.id,
        details={"name": n},
    )
    return RedirectResponse("/admin/templates/fields?ok=delete", status_code=303)


@router.post("/templates/upload", response_class=HTMLResponse)
async def templates_upload(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    contract_type: str = Form(""),
    _: None = Depends(require_csrf),
):
    from app.services.docx_upload import DocxUploadError, read_upload_limited

    try:
        raw = await read_upload_limited(file)
        name = save_upload(
            filename=file.filename or "template.docx",
            data=raw,
            contract_type=(contract_type or "").strip(),
        )
    except (TemplateAdminError, DocxUploadError) as exc:
        return _error(request, user, str(exc))
    record_event(
        db,
        type="admin_template_upload",
        org_id=None,
        user_id=user.id,
        details={"name": name, "contract_type": contract_type or ""},
    )
    return RedirectResponse("/admin/templates?ok=upload", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/templates/rename", response_class=HTMLResponse)
def templates_rename(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    name: str = Form(...),
    new_title: str = Form(...),
    csrf_token: str = Form(""),
    _: None = Depends(require_csrf),
):
    try:
        new_name = rename_template(db, old_name=name, new_title=new_title)
        db.commit()
    except TemplateAdminError as exc:
        db.rollback()
        return _error(request, user, str(exc))
    except FileNotFoundError:
        db.rollback()
        return _error(request, user, "Шаблон не найден")
    record_event(
        db,
        type="admin_template_rename",
        org_id=None,
        user_id=user.id,
        details={"old": name, "new": new_name},
    )
    return RedirectResponse("/admin/templates?ok=rename", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/templates/delete", response_class=HTMLResponse)
def templates_delete(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    name: str = Form(...),
    csrf_token: str = Form(""),
    _: None = Depends(require_csrf),
):
    try:
        delete_template(db, name=name)
        db.commit()
    except TemplateAdminError as exc:
        db.rollback()
        return _error(request, user, str(exc))
    except FileNotFoundError:
        db.rollback()
        return _error(request, user, "Шаблон не найден")
    record_event(
        db,
        type="admin_template_delete",
        org_id=None,
        user_id=user.id,
        details={"name": name},
    )
    return RedirectResponse("/admin/templates?ok=delete", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/templates/role", response_class=HTMLResponse)
def templates_role(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    name: str = Form(...),
    contract_type: str = Form(""),
    csrf_token: str = Form(""),
    _: None = Depends(require_csrf),
):
    try:
        set_contract_membership(name=name, contract_type=(contract_type or "").strip())
    except TemplateAdminError as exc:
        return _error(request, user, str(exc))
    except FileNotFoundError:
        return _error(request, user, "Шаблон не найден")
    record_event(
        db,
        type="admin_template_role",
        org_id=None,
        user_id=user.id,
        details={"name": name, "contract_type": contract_type or ""},
    )
    return RedirectResponse("/admin/templates?ok=role", status_code=status.HTTP_303_SEE_OTHER)


def _error(request: Request, user: CurrentUser, message: str):
    return templates.TemplateResponse(
        request=request,
        name="admin/templates.html",
        context=_page(request, user, flash_error=message),
        status_code=400,
    )
