"""Кабинет: свои шаблоны документов организации."""

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
from app.services.org_templates import (
    CONTRACT_TYPE_LABELS,
    OrgTemplateError,
    assert_can_manage_org_templates,
    can_manage_org_templates,
    delete_org_template,
    list_org_templates,
    rename_org_template,
    save_org_upload,
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
    }
    data.update(extra)
    return data


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
    return templates.TemplateResponse(
        request=request,
        name="cabinet/org_templates.html",
        context=_page(request, user, org, db, flash_ok=flash_ok),
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
        name = save_org_upload(
            org_id=org.id,
            filename=file.filename or "template.docx",
            data=raw,
            contract_type=(contract_type or "").strip(),
        )
    except (OrgTemplateError, DocxUploadError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/org_templates.html",
            context=_page(request, user, org, db, flash_error=str(exc)),
            status_code=400,
        )
    record_event(
        db,
        type="org_template_upload",
        org_id=org.id,
        user_id=user.id,
        details={"name": name, "contract_type": contract_type or ""},
    )
    return RedirectResponse("/cabinet/templates/?ok=upload", status_code=status.HTTP_303_SEE_OTHER)


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
            context=_page(request, user, org, db, flash_error=str(exc)),
            status_code=400,
        )
    except FileNotFoundError:
        db.rollback()
        return templates.TemplateResponse(
            request=request,
            name="cabinet/org_templates.html",
            context=_page(request, user, org, db, flash_error="Шаблон не найден"),
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
            context=_page(request, user, org, db, flash_error=str(exc)),
            status_code=400,
        )
    except FileNotFoundError:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/org_templates.html",
            context=_page(request, user, org, db, flash_error="Шаблон не найден"),
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
            context=_page(request, user, org, db, flash_error=str(exc)),
            status_code=400,
        )
    except FileNotFoundError:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/org_templates.html",
            context=_page(request, user, org, db, flash_error="Шаблон не найден"),
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
