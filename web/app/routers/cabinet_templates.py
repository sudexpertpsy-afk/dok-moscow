"""Кабинет: свои шаблоны документов организации + словарь полей (W-27 / W-41)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, forbidden_org_admin_page, require_csrf, require_org_user
from app.org_scope import get_org_for_user
from app.nav_context import cabinet_nav
from app.security import get_csrf_token
from app.services.audit import record_event
from app.services.org_fields import (
    FIELD_TYPE_LABELS,
    OrgFieldError,
    classify_template_variables,
    clear_staged,
    commit_staged_upload,
    create_org_field,
    delete_org_field,
    field_to_dict,
    list_org_fields,
    read_staged,
    stage_org_upload,
    templates_using_field,
    update_org_field,
)
from app.services.org_templates import (
    CONTRACT_TYPE_LABELS,
    OrgTemplateError,
    assert_can_manage_org_templates,
    can_manage_org_templates,
    delete_org_template,
    list_org_templates,
    rename_org_template,
    set_org_contract_role,
)
from app.templating import templates

router = APIRouter(prefix="/cabinet/templates", tags=["cabinet-templates"])


def _page(request: Request, user: CurrentUser, org, db: Session, **extra):
    allowed, deny_reason = can_manage_org_templates(db, org.id)
    items = list_org_templates(org.id) if allowed else []
    for it in items:
        if it.get("mtime"):
            it["mtime_label"] = datetime.fromtimestamp(
                it["mtime"], tz=timezone.utc
            ).strftime("%d.%m.%Y %H:%M")
        else:
            it["mtime_label"] = "—"
        it["size_kb"] = max(1, int(round((it.get("size") or 0) / 1024))) if it.get("size") else 0
    data = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": user,
        "org": org,
        "nav": cabinet_nav(db, user),
        "active": "templates",
        "flash_error": None,
        "flash_ok": None,
        "contract_type_labels": CONTRACT_TYPE_LABELS,
        "allowed": allowed,
        "deny_reason": deny_reason,
        "items": items,
        "field_type_labels": {k.value: v for k, v in FIELD_TYPE_LABELS.items()},
    }
    data.update(extra)
    return data


def _fields_help_context(db: Session, org_id: int) -> dict:
    from app.services.templates import ensure_core_on_path

    ensure_core_on_path()
    from docfiller_core.system_fields import list_standard_fields

    return {
        "standard_fields": list_standard_fields(),
        "org_fields": [field_to_dict(f) for f in list_org_fields(db, org_id)],
    }


@router.get("/", response_class=HTMLResponse)
def templates_home(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    if not user.is_org_admin:
        return forbidden_org_admin_page(request, user, db)
    org = get_org_for_user(db, user)
    ok = request.query_params.get("ok")
    flash_ok = {
        "upload": "Шаблон загружен.",
        "rename": "Название обновлено.",
        "delete": "Шаблон удалён.",
        "role": "Роль в комплекте обновлена.",
    }.get(ok or "")
    help_ctx = _fields_help_context(db, org.id)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/org_templates.html",
        context=_page(request, user, org, db, flash_ok=flash_ok, **help_ctx),
    )


@router.post("/upload", response_class=HTMLResponse)
async def templates_upload(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    contract_type: str = Form(""),
    _: None = Depends(require_csrf),
):
    if not user.is_org_admin:
        return forbidden_org_admin_page(request, user, db)
    org = get_org_for_user(db, user)
    assert_can_manage_org_templates(db, org.id)
    from app.services.docx_upload import DocxUploadError, read_upload_limited

    try:
        raw = await read_upload_limited(file)
        token, name = stage_org_upload(
            org_id=org.id,
            filename=file.filename or "template.docx",
            data=raw,
        )
    except (OrgFieldError, DocxUploadError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/org_templates.html",
            context=_page(
                request,
                user,
                org,
                db,
                flash_error=str(exc),
                **_fields_help_context(db, org.id),
            ),
            status_code=400,
        )
    q_ct = (contract_type or "").strip()
    return RedirectResponse(
        f"/cabinet/templates/upload/confirm?token={token}&contract_type={q_ct}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/upload/confirm", response_class=HTMLResponse)
def templates_upload_confirm_get(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    if not user.is_org_admin:
        return forbidden_org_admin_page(request, user, db)
    org = get_org_for_user(db, user)
    assert_can_manage_org_templates(db, org.id)
    token = (request.query_params.get("token") or "").strip()
    contract_type = (request.query_params.get("contract_type") or "").strip()
    try:
        data, name = read_staged(org.id, token)
        from app.services.templates import ensure_core_on_path

        ensure_core_on_path()
        from docfiller_core.template_security import assert_safe_docx_template

        variables = assert_safe_docx_template(data)
        classified = classify_template_variables(db, org.id, variables)
    except OrgFieldError as exc:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/org_templates.html",
            context=_page(
                request,
                user,
                org,
                db,
                flash_error=str(exc),
                **_fields_help_context(db, org.id),
            ),
            status_code=400,
        )
    from app.services.org_templates import org_templates_dir
    from app.services.safe_paths import resolve_under

    exists = resolve_under(org_templates_dir(org.id), name).is_file()
    return templates.TemplateResponse(
        request=request,
        name="cabinet/org_template_confirm.html",
        context=_page(
            request,
            user,
            org,
            db,
            token=token,
            staged_name=name,
            contract_type=contract_type,
            classified=classified,
            replace_exists=exists,
            active="templates",
        ),
    )


@router.post("/upload/confirm", response_class=HTMLResponse)
async def templates_upload_confirm_post(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    token: str = Form(...),
    contract_type: str = Form(""),
    replace_existing: str = Form(""),
    _: None = Depends(require_csrf),
):
    if not user.is_org_admin:
        return forbidden_org_admin_page(request, user, db)
    org = get_org_for_user(db, user)
    assert_can_manage_org_templates(db, org.id)
    form = await request.form()
    idxs = sorted(
        {
            str(k)[len("new_name_") :]
            for k in form.keys()
            if str(k).startswith("new_name_") and str(k)[len("new_name_") :].isdigit()
        },
        key=int,
    )
    defs = []
    for idx in idxs:
        n = str(form.get(f"new_name_{idx}") or "").strip()
        if not n:
            continue
        defs.append(
            {
                "name": n,
                "label": str(form.get(f"new_label_{idx}") or ""),
                "type": str(form.get(f"new_type_{idx}") or "string"),
                "required": str(form.get(f"new_required_{idx}") or "")
                in {"1", "on", "true", "да"},
                "default": str(form.get(f"new_default_{idx}") or ""),
                "hint": str(form.get(f"new_hint_{idx}") or ""),
                "options": str(form.get(f"new_options_{idx}") or ""),
            }
        )

    try:
        name = commit_staged_upload(
            db,
            org_id=org.id,
            user_id=user.id,
            token=token,
            contract_type=(contract_type or "").strip(),
            new_field_defs=defs,
            replace_existing=str(replace_existing or "") in {"1", "on", "true", "да"},
        )
        db.commit()
    except OrgFieldError as exc:
        db.rollback()
        try:
            data, name = read_staged(org.id, token)
            from app.services.templates import ensure_core_on_path

            ensure_core_on_path()
            from docfiller_core.template_security import assert_safe_docx_template

            variables = assert_safe_docx_template(data)
            classified = classify_template_variables(db, org.id, variables)
            from app.services.org_templates import org_templates_dir
            from app.services.safe_paths import resolve_under

            exists = resolve_under(org_templates_dir(org.id), name).is_file()
        except Exception:
            return RedirectResponse(
                f"/cabinet/templates/?err={str(exc)[:200]}",
                status_code=303,
            )
        return templates.TemplateResponse(
            request=request,
            name="cabinet/org_template_confirm.html",
            context=_page(
                request,
                user,
                org,
                db,
                token=token,
                staged_name=name,
                contract_type=contract_type,
                classified=classified,
                replace_exists=exists,
                flash_error=str(exc),
            ),
            status_code=400,
        )

    record_event(
        db,
        type="org_template_upload",
        org_id=org.id,
        user_id=user.id,
        details={
            "name": name,
            "contract_type": contract_type or "",
            "new_fields": [d["name"] for d in defs],
        },
    )
    return RedirectResponse("/cabinet/templates/?ok=upload", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/upload/cancel", response_class=HTMLResponse)
def templates_upload_cancel(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    token: str = Form(""),
    _: None = Depends(require_csrf),
):
    if not user.is_org_admin:
        return forbidden_org_admin_page(request, user, db)
    org = get_org_for_user(db, user)
    clear_staged(org.id, token)
    return RedirectResponse("/cabinet/templates/", status_code=303)


# --- Словарь полей (доступен любому пользователю организации) ---


@router.get("/fields", response_class=HTMLResponse)
def fields_home(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    ok = request.query_params.get("ok")
    flash_ok = {
        "create": "Поле добавлено в словарь организации.",
        "update": "Поле обновлено.",
        "delete": "Поле удалено.",
    }.get(ok or "")
    fields = [field_to_dict(f) for f in list_org_fields(db, org.id)]
    for f in fields:
        f["used_in"] = templates_using_field(org.id, f["name"])
    help_ctx = _fields_help_context(db, org.id)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/org_fields.html",
        context=_page(
            request,
            user,
            org,
            db,
            active="template_fields",
            flash_ok=flash_ok,
            fields=fields,
            **help_ctx,
        ),
    )


@router.post("/fields/create", response_class=HTMLResponse)
def fields_create(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    name: str = Form(...),
    label: str = Form(""),
    field_type: str = Form("string"),
    required: str = Form(""),
    default: str = Form(""),
    hint: str = Form(""),
    options: str = Form(""),
    _: None = Depends(require_csrf),
):
    org = get_org_for_user(db, user)
    try:
        row = create_org_field(
            db,
            org_id=org.id,
            user_id=user.id,
            name=name,
            label=label,
            field_type=field_type,
            required=required in {"1", "on", "true", "да"},
            default=default,
            hint=hint,
            options=options,
        )
        record_event(
            db,
            type="org_field_create",
            org_id=org.id,
            user_id=user.id,
            details={"name": row.name, "type": row.field_type.value},
        )
        db.commit()
    except OrgFieldError as exc:
        db.rollback()
        fields = [field_to_dict(f) for f in list_org_fields(db, org.id)]
        return templates.TemplateResponse(
            request=request,
            name="cabinet/org_fields.html",
            context=_page(
                request,
                user,
                org,
                db,
                active="template_fields",
                flash_error=str(exc),
                fields=fields,
                **_fields_help_context(db, org.id),
            ),
            status_code=400,
        )
    return RedirectResponse("/cabinet/templates/fields?ok=create", status_code=303)


@router.post("/fields/{field_id}/update", response_class=HTMLResponse)
def fields_update(
    field_id: int,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    label: str = Form(""),
    field_type: str = Form("string"),
    required: str = Form(""),
    default: str = Form(""),
    hint: str = Form(""),
    options: str = Form(""),
    _: None = Depends(require_csrf),
):
    org = get_org_for_user(db, user)
    from fastapi import HTTPException

    from app.models import OrgField

    row = db.get(OrgField, field_id)
    if row is None or row.org_id != org.id:
        raise HTTPException(status_code=404, detail="Поле не найдено")
    try:
        update_org_field(
            db,
            row,
            label=label,
            field_type=field_type,
            required=required in {"1", "on", "true", "да"},
            default=default,
            hint=hint,
            options=options,
        )
        record_event(
            db,
            type="org_field_update",
            org_id=org.id,
            user_id=user.id,
            details={"name": row.name, "type": row.field_type.value},
        )
        db.commit()
    except OrgFieldError as exc:
        db.rollback()
        fields = [field_to_dict(f) for f in list_org_fields(db, org.id)]
        return templates.TemplateResponse(
            request=request,
            name="cabinet/org_fields.html",
            context=_page(
                request,
                user,
                org,
                db,
                active="template_fields",
                flash_error=str(exc),
                fields=fields,
                **_fields_help_context(db, org.id),
            ),
            status_code=400,
        )
    return RedirectResponse("/cabinet/templates/fields?ok=update", status_code=303)


@router.post("/fields/{field_id}/delete", response_class=HTMLResponse)
def fields_delete(
    field_id: int,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    org = get_org_for_user(db, user)
    from app.models import OrgField
    from fastapi import HTTPException

    row = db.get(OrgField, field_id)
    if row is None or row.org_id != org.id:
        raise HTTPException(status_code=404, detail="Поле не найдено")
    name = row.name
    try:
        delete_org_field(db, row)
        record_event(
            db,
            type="org_field_delete",
            org_id=org.id,
            user_id=user.id,
            details={"name": name},
        )
        db.commit()
    except OrgFieldError as exc:
        db.rollback()
        fields = [field_to_dict(f) for f in list_org_fields(db, org.id)]
        return templates.TemplateResponse(
            request=request,
            name="cabinet/org_fields.html",
            context=_page(
                request,
                user,
                org,
                db,
                active="template_fields",
                flash_error=str(exc),
                fields=fields,
                **_fields_help_context(db, org.id),
            ),
            status_code=400,
        )
    return RedirectResponse("/cabinet/templates/fields?ok=delete", status_code=303)


@router.post("/rename", response_class=HTMLResponse)
def templates_rename(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    name: str = Form(...),
    new_title: str = Form(...),
    _: None = Depends(require_csrf),
):
    if not user.is_org_admin:
        return forbidden_org_admin_page(request, user, db)
    org = get_org_for_user(db, user)
    assert_can_manage_org_templates(db, org.id)
    try:
        new_name = rename_org_template(
            db, org_id=org.id, old_name=name, new_title=new_title
        )
        db.commit()
    except OrgTemplateError as exc:
        db.rollback()
        return templates.TemplateResponse(
            request=request,
            name="cabinet/org_templates.html",
            context=_page(
                request,
                user,
                org,
                db,
                flash_error=str(exc),
                **_fields_help_context(db, org.id),
            ),
            status_code=400,
        )
    except FileNotFoundError:
        db.rollback()
        return templates.TemplateResponse(
            request=request,
            name="cabinet/org_templates.html",
            context=_page(
                request,
                user,
                org,
                db,
                flash_error="Шаблон не найден",
                **_fields_help_context(db, org.id),
            ),
            status_code=404,
        )
    record_event(
        db,
        type="org_template_rename",
        org_id=org.id,
        user_id=user.id,
        details={"old": name, "new": new_name},
    )
    return RedirectResponse("/cabinet/templates/?ok=rename", status_code=303)


@router.post("/delete", response_class=HTMLResponse)
def templates_delete(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    name: str = Form(...),
    _: None = Depends(require_csrf),
):
    if not user.is_org_admin:
        return forbidden_org_admin_page(request, user, db)
    org = get_org_for_user(db, user)
    assert_can_manage_org_templates(db, org.id)
    try:
        delete_org_template(org_id=org.id, name=name)
    except OrgTemplateError as exc:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/org_templates.html",
            context=_page(
                request,
                user,
                org,
                db,
                flash_error=str(exc),
                **_fields_help_context(db, org.id),
            ),
            status_code=400,
        )
    except FileNotFoundError:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/org_templates.html",
            context=_page(
                request,
                user,
                org,
                db,
                flash_error="Шаблон не найден",
                **_fields_help_context(db, org.id),
            ),
            status_code=404,
        )
    record_event(
        db,
        type="org_template_delete",
        org_id=org.id,
        user_id=user.id,
        details={"name": name},
    )
    db.commit()
    return RedirectResponse("/cabinet/templates/?ok=delete", status_code=303)


@router.post("/role", response_class=HTMLResponse)
def templates_role(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    name: str = Form(...),
    contract_type: str = Form(""),
    _: None = Depends(require_csrf),
):
    if not user.is_org_admin:
        return forbidden_org_admin_page(request, user, db)
    org = get_org_for_user(db, user)
    assert_can_manage_org_templates(db, org.id)
    try:
        set_org_contract_role(
            org_id=org.id, name=name, contract_type=(contract_type or "").strip()
        )
    except OrgTemplateError as exc:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/org_templates.html",
            context=_page(
                request,
                user,
                org,
                db,
                flash_error=str(exc),
                **_fields_help_context(db, org.id),
            ),
            status_code=400,
        )
    except FileNotFoundError:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/org_templates.html",
            context=_page(
                request,
                user,
                org,
                db,
                flash_error="Шаблон не найден",
                **_fields_help_context(db, org.id),
            ),
            status_code=404,
        )
    record_event(
        db,
        type="org_template_role",
        org_id=org.id,
        user_id=user.id,
        details={"name": name, "contract_type": contract_type or ""},
    )
    db.commit()
    return RedirectResponse("/cabinet/templates/?ok=role", status_code=303)
