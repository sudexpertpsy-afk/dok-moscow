"""W-49 A: мастер импорта контрагентов."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, require_org_user
from app.models import Counterparty, CounterpartyType, JobType
from app.nav_context import cabinet_nav
from app.org_scope import get_org_for_user, require_org_id
from app.security import check_csrf, get_csrf_token
from app.services.cp_import import (
    FIELD_KEYS,
    FIELD_LABELS,
    MAX_FILE_BYTES,
    SYNC_MAX_ROWS,
    DuplicateMode,
    ImportErrorMsg,
    auto_map_columns,
    build_template_xlsx,
    check_import_rate,
    classify_rows,
    commit_rows,
    consume_import_rate,
    load_meta,
    load_table_snapshot,
    new_token,
    parse_upload,
    row_limit_for_org,
    save_mapping,
    save_table_snapshot,
    summarize,
    token_dir,
)
from app.services.dadata import usage_today
from app.services.jobs import enqueue_job
from app.services.safe_paths import resolve_under_org
from app.templating import templates

router = APIRouter(prefix="/cabinet/counterparties", tags=["counterparties"])


def _page(request: Request, user: CurrentUser, org, db, **extra):
    ctx = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": get_settings().app_name,
        "user": user,
        "org": org,
        "nav": cabinet_nav(db, user),
        "active": "counterparties",
        "flash_error": None,
        "flash_ok": None,
        "field_keys": FIELD_KEYS,
        "field_labels": FIELD_LABELS,
    }
    ctx.update(extra)
    return ctx


def _token_from_session(request: Request) -> str:
    token = str(request.session.get("cp_import_token") or "")
    if not token:
        raise ImportErrorMsg("Сначала загрузите файл.")
    return token


def _parse_mapping(form) -> dict[str, int | None]:
    mapping: dict[str, int | None] = {}
    for key in FIELD_KEYS:
        raw = str(form.get(f"map_{key}") or "").strip()
        if raw in ("", "-1", "skip"):
            mapping[key] = None
        else:
            try:
                mapping[key] = int(raw)
            except ValueError:
                mapping[key] = None
    return mapping


@router.get("/import/template.xlsx")
def import_template(user: CurrentUser = Depends(require_org_user)):
    data = build_template_xlsx()
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                "attachment; filename*=UTF-8''"
                + quote("Шаблон_импорта_контрагентов.xlsx")
            )
        },
    )


@router.get("/import", response_class=HTMLResponse)
def import_start(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    limit = row_limit_for_org(db, org.id)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/cp_import_start.html",
        context=_page(
            request,
            user,
            org,
            db,
            row_limit=limit,
            flash_error=request.query_params.get("error"),
        ),
    )


@router.post("/import/upload", response_class=HTMLResponse)
async def import_upload(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    try:
        check_import_rate(db, org.id)
        upload = form.get("file")
        filename = getattr(upload, "filename", None) if upload is not None else None
        if upload is None or not hasattr(upload, "read") or not filename:
            raise ImportErrorMsg("Выберите файл CSV, XLSX или .journal.jsonl.")
        raw = await upload.read()
        if not raw:
            raise ImportErrorMsg("Файл пустой.")
        if len(raw) > MAX_FILE_BYTES:
            raise ImportErrorMsg("Файл больше 5 МБ. Разбейте на части или сохраните как CSV.")
        table = parse_upload(str(filename), raw)
        limit = row_limit_for_org(db, org.id)
        if len(table.rows) > limit:
            raise ImportErrorMsg(
                f"В файле {len(table.rows)} строк, на вашем тарифе лимит {limit} за один импорт."
            )
        token = new_token()
        dest = token_dir(org.id, token)
        dest.mkdir(parents=True, exist_ok=True)
        safe_name = (upload.filename or "import.csv").replace("/", "_")[:180]
        (dest / "source_name.txt").write_text(safe_name, encoding="utf-8")
        mapping = auto_map_columns(table.headers)
        save_table_snapshot(org.id, token, table, mapping)
        request.session["cp_import_token"] = token
    except ImportErrorMsg as exc:
        q = quote(str(exc))
        return RedirectResponse(f"/cabinet/counterparties/import?error={q}", status_code=303)
    return RedirectResponse("/cabinet/counterparties/import/map", status_code=303)


@router.get("/import/map", response_class=HTMLResponse)
def import_map(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    try:
        token = _token_from_session(request)
        table, mapping = load_table_snapshot(org.id, token)
    except ImportErrorMsg as exc:
        return RedirectResponse(
            f"/cabinet/counterparties/import?error={quote(str(exc))}", status_code=303
        )
    mapping = mapping or auto_map_columns(table.headers)
    sample = table.rows[:3]
    return templates.TemplateResponse(
        request=request,
        name="cabinet/cp_import_map.html",
        context=_page(
            request,
            user,
            org,
            db,
            headers=table.headers,
            mapping=mapping,
            sample=sample,
            source_kind=table.source_kind,
            row_count=len(table.rows),
        ),
    )


@router.post("/import/map", response_class=HTMLResponse)
async def import_map_save(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    try:
        token = _token_from_session(request)
        mapping = _parse_mapping(form)
        default_raw = str(form.get("default_type") or "auto")
        save_mapping(
            org.id,
            token,
            mapping,
            extra={"default_type": default_raw},
        )
    except ImportErrorMsg as exc:
        return RedirectResponse(
            f"/cabinet/counterparties/import?error={quote(str(exc))}", status_code=303
        )
    return RedirectResponse("/cabinet/counterparties/import/preview", status_code=303)


@router.get("/import/preview", response_class=HTMLResponse)
def import_preview(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    try:
        token = _token_from_session(request)
        table, mapping = load_table_snapshot(org.id, token)
        meta = load_meta(org.id, token)
    except ImportErrorMsg as exc:
        return RedirectResponse(
            f"/cabinet/counterparties/import?error={quote(str(exc))}", status_code=303
        )
    mapping = mapping or auto_map_columns(table.headers)
    default_raw = str(meta.get("default_type") or "auto")
    default_type = None
    if default_raw in ("fl", "ul", "expert"):
        default_type = CounterpartyType(default_raw)
    existing = list(db.scalars(select(Counterparty).where(Counterparty.org_id == org.id)).all())
    rows = classify_rows(table, mapping, default_type=default_type, existing=existing)
    settings = get_settings()
    used = usage_today(db, org.id)
    preview = summarize(rows, dadata_used=used, dadata_limit=settings.dadata_daily_limit)
    return templates.TemplateResponse(
        request=request,
        name="cabinet/cp_import_preview.html",
        context=_page(
            request,
            user,
            org,
            db,
            preview=preview,
            preview_rows=preview.rows[:80],
            async_job=len(table.rows) > SYNC_MAX_ROWS,
            default_type=default_raw,
        ),
    )


@router.post("/import/run", response_class=HTMLResponse)
async def import_run(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    form = await request.form()
    if not check_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    try:
        token = _token_from_session(request)
        consume_import_rate(db, org.id)
        table, mapping = load_table_snapshot(org.id, token)
        meta = load_meta(org.id, token)
        mapping = mapping or auto_map_columns(table.headers)
        default_raw = str(meta.get("default_type") or "auto")
        default_type = (
            CounterpartyType(default_raw) if default_raw in ("fl", "ul", "expert") else None
        )
        mode = DuplicateMode(str(form.get("duplicate_mode") or DuplicateMode.fill))
        enrich = str(form.get("enrich") or "") == "1"
        require_zero = str(form.get("require_zero_errors") or "") == "1"
        existing = list(db.scalars(select(Counterparty).where(Counterparty.org_id == org.id)).all())
        rows = classify_rows(table, mapping, default_type=default_type, existing=existing)
        src_name = "import"
        name_path = token_dir(org.id, token) / "source_name.txt"
        if name_path.is_file():
            src_name = name_path.read_text(encoding="utf-8").strip() or src_name

        if len(table.rows) > SYNC_MAX_ROWS:
            save_mapping(
                org.id,
                token,
                mapping,
                extra={
                    "default_type": default_raw,
                    "duplicate_mode": mode.value,
                    "enrich": enrich,
                    "require_zero_errors": require_zero,
                    "filename": src_name,
                },
            )
            job = enqueue_job(
                db,
                org_id=org.id,
                user_id=user.id,
                job_type=JobType.counterparty_import,
                payload={"token": token},
            )
            return RedirectResponse(f"/cabinet/jobs/{job.id}", status_code=303)

        result = commit_rows(
            db,
            org.id,
            rows,
            mode=mode,
            require_zero_errors=require_zero,
            user_id=user.id,
            filename=src_name,
            token=token,
            enrich=enrich,
        )
        db.commit()
        request.session["cp_import_result"] = {
            "created": result.created,
            "updated": result.updated,
            "skipped": result.skipped,
            "errors": result.errors,
            "report_rel": result.report_rel,
        }
    except ImportErrorMsg as exc:
        db.rollback()
        return RedirectResponse(
            f"/cabinet/counterparties/import/preview?error={quote(str(exc))}",
            status_code=303,
        )
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            f"/cabinet/counterparties/import/preview?error={quote(str(exc))}",
            status_code=303,
        )
    return RedirectResponse("/cabinet/counterparties/import/done", status_code=303)


@router.get("/import/done", response_class=HTMLResponse)
def import_done(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org = get_org_for_user(db, user)
    result = request.session.get("cp_import_result") or {}
    return templates.TemplateResponse(
        request=request,
        name="cabinet/cp_import_done.html",
        context=_page(request, user, org, db, result=result),
    )


@router.get("/import/report")
def import_report(
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    org_id = require_org_id(user)
    rel = str((request.session.get("cp_import_result") or {}).get("report_rel") or "")
    if not rel:
        raise HTTPException(status_code=404, detail="Отчёт не найден")
    try:
        path = resolve_under_org(org_id, rel)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Отчёт не найден") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Отчёт не найден")
    return Response(
        content=path.read_bytes(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="import-errors.xlsx"'},
    )