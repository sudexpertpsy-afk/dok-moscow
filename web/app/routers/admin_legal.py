"""Админка «Законодательство» — подтверждение редакций (W-19)."""

from __future__ import annotations

from datetime import date, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from markupsafe import Markup
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import CurrentUser, require_csrf, require_service_admin
from app.models import ActVersionStatus, LegalActCategory, LegalActMode, LegalActStatus
from app.routers.admin import _ctx
from app.services.audit import record_event
from app.services.legal_admin import (
    HEALTH_LABEL,
    create_act,
    do_publish,
    do_reject,
    draft_diff_html,
    get_act_admin,
    list_acts_admin,
    manual_upload,
    publish_all_drafts,
    update_act_settings,
)
from app.services.legal_bootstrap import (
    acts_needing_fill,
    bootstrap_missing,
    pull_ips_to_draft,
)
from app.templating import templates

router = APIRouter(prefix="/admin/legal", tags=["admin-legal"])


def _parse_date(raw: str | None) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _err(act_id: int | None, msg: str) -> RedirectResponse:
    q = quote(str(msg), safe="")
    if act_id is None:
        return RedirectResponse(f"/admin/legal/?error={q}", status_code=303)
    return RedirectResponse(f"/admin/legal/{act_id}?error={q}", status_code=303)


@router.get("/", response_class=HTMLResponse)
def legal_list(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    rows = list_acts_admin(db)
    flash_ok = request.query_params.get("ok")
    flash_error = request.query_params.get("error")
    need = acts_needing_fill(db)
    draft_count = sum(1 for r in rows if r.draft is not None)
    return templates.TemplateResponse(
        request=request,
        name="admin/legal_list.html",
        context=_ctx(
            request,
            user,
            "legal",
            rows=rows,
            need_fill_count=len(need),
            draft_count=draft_count,
            health_label={k.value: v for k, v in HEALTH_LABEL.items()},
            flash_ok={
                "published": "Редакция опубликована",
                "rejected": "Черновик отклонён",
                "draft": "Черновик создан",
                "saved": "Сохранено",
                "created": "Акт добавлен",
                "bootstrap": request.query_params.get("msg") or "Наполнение запущено",
                "pulled": "Текст подтянут в черновик",
                "batch_publish": request.query_params.get("msg")
                or "Черновики опубликованы",
            }.get(flash_ok or "", flash_ok),
            flash_error=flash_error,
        ),
    )


@router.post("/bootstrap")
def legal_bootstrap_batch(
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
    replace_draft: str = Form(""),
):
    report = bootstrap_missing(
        db,
        user_id=user.id,
        only_without_published=True,
        replace_draft=replace_draft in ("1", "on", "true"),
    )
    record_event(
        db,
        type="legal_bootstrap_batch",
        org_id=None,
        user_id=user.id,
        details={
            "ok": report.ok_count,
            "fail": report.fail_count,
            "skip": report.skip_count,
            "drafts": [r.draft_id for r in report.results if r.draft_id],
        },
    )
    db.commit()
    msg = quote(
        f"Готово: черновиков {report.ok_count}, ошибок {report.fail_count}, пропусков {report.skip_count}",
        safe="",
    )
    return RedirectResponse(f"/admin/legal/?ok=bootstrap&msg={msg}", status_code=303)


@router.post("/publish-drafts")
def legal_publish_all_drafts(
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
    only_new: str = Form(""),
):
    """Пакетная публикация черновиков (осознанное действие админа)."""
    report = publish_all_drafts(
        db,
        user_id=user.id,
        only_without_published=only_new in ("1", "on", "true"),
    )
    record_event(
        db,
        type="legal_batch_publish",
        org_id=None,
        user_id=user.id,
        details={
            "ok": report.ok_count,
            "fail": report.fail_count,
            "skip": report.skip_count,
            "published": [
                {"slug": i.slug, "version_id": i.version_id}
                for i in report.items
                if i.ok
            ],
        },
    )
    db.commit()
    msg = quote(
        f"Опубликовано: {report.ok_count}; пропусков: {report.skip_count}; ошибок: {report.fail_count}",
        safe="",
    )
    return RedirectResponse(f"/admin/legal/?ok=batch_publish&msg={msg}", status_code=303)


@router.get("/new", response_class=HTMLResponse)
def legal_new_form(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
):
    return templates.TemplateResponse(
        request=request,
        name="admin/legal_new.html",
        context=_ctx(
            request,
            user,
            "legal",
            categories=list(LegalActCategory),
            modes=list(LegalActMode),
            flash_error=request.query_params.get("error"),
        ),
    )


@router.post("/new")
def legal_new_submit(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
    title: str = Form(""),
    slug: str = Form(""),
    category: str = Form("law"),
    mode: str = Form("full_text"),
    number: str = Form(""),
    act_kind: str = Form(""),
    authority: str = Form(""),
    source_url: str = Form(""),
    watch_enabled: str = Form(""),
    tracked_articles: str = Form(""),
):
    try:
        act = create_act(
            db,
            title=title,
            slug=slug,
            category=category,
            mode=mode,
            number=number,
            act_kind=act_kind,
            authority=authority,
            source_url=source_url,
            watch_enabled=watch_enabled in ("1", "on", "true"),
            tracked_articles_raw=tracked_articles,
        )
        record_event(
            db,
            type="legal_act_created",
            org_id=None,
            user_id=user.id,
            details={"act_id": act.id, "slug": act.slug},
        )
        db.commit()
    except ValueError as exc:
        return _err(None, str(exc)) if False else RedirectResponse(
            f"/admin/legal/new?error={quote(str(exc), safe='')}", status_code=303
        )
    return RedirectResponse(f"/admin/legal/{act.id}?ok=created", status_code=303)


@router.get("/{act_id}", response_class=HTMLResponse)
def legal_detail(
    act_id: int,
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    act = get_act_admin(db, act_id)
    if act is None:
        return _err(None, "Акт не найден")
    drafts = sorted(
        [v for v in act.versions if v.status == ActVersionStatus.draft],
        key=lambda v: v.id,
        reverse=True,
    )
    published = next((v for v in act.versions if v.status == ActVersionStatus.published), None)
    archived = sorted(
        [v for v in act.versions if v.status == ActVersionStatus.archived],
        key=lambda v: v.id,
        reverse=True,
    )[:10]
    draft = drafts[0] if drafts else None
    diff_html = Markup(draft_diff_html(db, act, draft)) if draft else None
    logs = sorted(act.watch_logs or [], key=lambda r: r.id, reverse=True)[:20]
    flash_ok = request.query_params.get("ok")
    return templates.TemplateResponse(
        request=request,
        name="admin/legal_detail.html",
        context=_ctx(
            request,
            user,
            "legal",
            act=act,
            draft=draft,
            published=published,
            archived=archived,
            diff_html=diff_html,
            logs=logs,
            modes=list(LegalActMode),
            statuses=list(LegalActStatus),
            tracked_articles_text="\n".join(act.tracked_articles or []),
            flash_ok={
                "published": "Редакция опубликована",
                "rejected": "Черновик отклонён",
                "draft": "Черновик создан — проверьте diff и опубликуйте",
                "saved": "Настройки акта сохранены",
                "created": "Акт добавлен в реестр",
                "pulled": "Текст из ИПС загружен в черновик — проверьте diff и опубликуйте",
            }.get(flash_ok or "", None),
            flash_error=request.query_params.get("error"),
        ),
    )


@router.post("/{act_id}/publish/{version_id}")
def legal_publish(
    act_id: int,
    version_id: int,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    try:
        version = do_publish(db, version_id, user.id)
        if version.act_id != act_id:
            raise ValueError("Редакция другого акта")
        record_event(
            db,
            type="legal_version_published",
            org_id=None,
            user_id=user.id,
            details={
                "act_id": act_id,
                "version_id": version.id,
                "change_basis": (version.change_basis or "")[:500],
            },
        )
        db.commit()
    except ValueError as exc:
        return _err(act_id, str(exc))
    return RedirectResponse(f"/admin/legal/{act_id}?ok=published", status_code=303)


@router.post("/{act_id}/reject/{version_id}")
def legal_reject(
    act_id: int,
    version_id: int,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    try:
        version = do_reject(db, version_id, user.id)
        if version.act_id != act_id:
            raise ValueError("Редакция другого акта")
        record_event(
            db,
            type="legal_version_rejected",
            org_id=None,
            user_id=user.id,
            details={"act_id": act_id, "version_id": version.id},
        )
        db.commit()
    except ValueError as exc:
        return _err(act_id, str(exc))
    return RedirectResponse(f"/admin/legal/{act_id}?ok=rejected", status_code=303)


@router.post("/{act_id}/pull-ips")
def legal_pull_ips(
    act_id: int,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
    replace_draft: str = Form(""),
):
    act = get_act_admin(db, act_id)
    if act is None:
        return _err(None, "Акт не найден")
    result = pull_ips_to_draft(
        db,
        act,
        user_id=user.id,
        replace_draft=replace_draft in ("1", "on", "true"),
    )
    if not result.ok:
        return _err(act_id, result.error or result.skipped or "не удалось")
    record_event(
        db,
        type="legal_ips_pulled",
        org_id=None,
        user_id=user.id,
        details={
            "act_id": act_id,
            "draft_id": result.draft_id,
            "fragments_filled": result.fragments_filled,
        },
    )
    db.commit()
    return RedirectResponse(f"/admin/legal/{act_id}?ok=pulled", status_code=303)


@router.post("/{act_id}/upload")
def legal_upload(
    act_id: int,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
    body_html: str = Form(""),
    source: str = Form(""),
    revision_date: str = Form(""),
    change_basis: str = Form(""),
):
    try:
        draft = manual_upload(
            db,
            act_id=act_id,
            body_html=body_html,
            source=source,
            user_id=user.id,
            revision_date=_parse_date(revision_date),
            change_basis=change_basis or None,
        )
        record_event(
            db,
            type="legal_version_draft_manual",
            org_id=None,
            user_id=user.id,
            details={"act_id": act_id, "version_id": draft.id, "source": source[:200]},
        )
        db.commit()
    except ValueError as exc:
        return _err(act_id, str(exc))
    return RedirectResponse(f"/admin/legal/{act_id}?ok=draft", status_code=303)


@router.post("/{act_id}/settings")
def legal_settings(
    act_id: int,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
    mode: str = Form("full_text"),
    status: str = Form("active"),
    watch_enabled: str = Form(""),
    tracked_articles: str = Form(""),
    notes: str = Form(""),
    source_url: str = Form(""),
):
    act = get_act_admin(db, act_id)
    if act is None:
        return _err(None, "Акт не найден")
    try:
        update_act_settings(
            db,
            act,
            mode=mode,
            status=status,
            watch_enabled=watch_enabled in ("1", "on", "true"),
            tracked_articles_raw=tracked_articles,
            notes=notes,
            source_url=source_url,
        )
        record_event(
            db,
            type="legal_act_updated",
            org_id=None,
            user_id=user.id,
            details={"act_id": act_id, "mode": mode, "status": status},
        )
        db.commit()
    except ValueError as exc:
        return _err(act_id, str(exc))
    return RedirectResponse(f"/admin/legal/{act_id}?ok=saved", status_code=303)
