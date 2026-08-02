"""Админка «Единое окно» (W-36): лендинг, тарифы, промокоды, объявления."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, require_csrf, require_service_admin
from app.models import (
    Announcement,
    ContentBlock,
    ContentBlockVersion,
    Payment,
    PromoCode,
    Tariff,
    TariffPriceLog,
)
from app.routers.admin import _ctx
from app.routers.admin_server import _require_totp
from app.services.analytics import (
    AnalyticsValidationError,
    ensure_analytics_settings,
    save_analytics_settings,
)
from app.services.cms import (
    CONTENT_SLOT_KEYS,
    format_price_rub,
    publish_content_block,
    rollback_content_block,
    safe_markdown,
    tariff_features,
    upsert_announcement,
    upsert_promo_code,
    update_tariff_prices,
)
from app.templating import templates

router = APIRouter(prefix="/admin/cms", tags=["admin-cms"])


def _to_dict(form) -> dict[str, object]:
    return {str(k): v for k, v in form.multi_items()}


def _redirect(path: str, *, ok: str = "", err: str = "") -> RedirectResponse:
    query = ""
    if ok:
        query = f"?ok={quote(ok, safe='')}"
    if err:
        query = f"?err={quote(err, safe='')}"
    return RedirectResponse(f"{path}{query}", status_code=303)


@router.get("/", response_class=HTMLResponse)
def cms_home(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    analytics = ensure_analytics_settings(db)
    db.commit()
    return templates.TemplateResponse(
        request=request,
        name="admin/cms_index.html",
        context=_ctx(
            request,
            user,
            "admin_cms",
            tariffs_count=db.scalar(select(func.count()).select_from(Tariff)) or 0,
            content_count=db.scalar(select(func.count()).select_from(ContentBlock)) or 0,
            promos_count=db.scalar(select(func.count()).select_from(PromoCode)) or 0,
            announcements_count=db.scalar(select(func.count()).select_from(Announcement)) or 0,
            analytics=analytics,
            public_base_url=get_settings().public_base_url.rstrip("/"),
            flash_ok=request.query_params.get("ok"),
            flash_error=request.query_params.get("err"),
        ),
    )


@router.post("/analytics")
async def cms_analytics_save(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    form = _to_dict(await request.form())
    try:
        save_analytics_settings(db, user_id=user.id, form=form)
        db.commit()
    except AnalyticsValidationError as exc:
        db.rollback()
        return _redirect("/admin/cms/", err=str(exc))
    except Exception:
        db.rollback()
        return _redirect("/admin/cms/", err="Не удалось сохранить настройки аналитики")
    return _redirect("/admin/cms/", ok="Настройки аналитики сохранены")


@router.get("/tariffs", response_class=HTMLResponse)
def cms_tariffs(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    rows = db.scalars(select(Tariff).order_by(Tariff.price_month_kop, Tariff.id)).all()
    logs = db.scalars(select(TariffPriceLog).order_by(TariffPriceLog.id.desc()).limit(20)).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/cms_tariffs.html",
        context=_ctx(
            request,
            user,
            "admin_cms",
            tariffs=rows,
            logs=logs,
            format_price_rub=format_price_rub,
            tariff_features=tariff_features,
            flash_ok=request.query_params.get("ok"),
            flash_error=request.query_params.get("err"),
        ),
    )


@router.post("/tariffs")
async def cms_tariffs_save(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    form = _to_dict(await request.form())
    if form.get("confirm_1") != "UPDATE_TARIFFS" or form.get("confirm_2") != "YES":
        return _redirect("/admin/cms/tariffs", err="Нужны оба подтверждения")
    err = _require_totp(db, user, str(form.get("totp_code") or ""))
    if err:
        return _redirect("/admin/cms/tariffs", err=err)
    changed = update_tariff_prices(db, form, user_id=user.id)
    db.commit()
    return _redirect("/admin/cms/tariffs", ok=f"Сохранено тарифов: {changed}")


@router.get("/content", response_class=HTMLResponse)
def cms_content(
    request: Request,
    key: str = Query("hero_headline"),
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    if key not in CONTENT_SLOT_KEYS:
        key = "hero_headline"
    blocks = {row.key: row for row in db.scalars(select(ContentBlock)).all()}
    current = blocks.get(key)
    versions = db.scalars(
        select(ContentBlockVersion)
        .where(ContentBlockVersion.block_key == key)
        .order_by(ContentBlockVersion.version.desc())
        .limit(10)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/cms_content.html",
        context=_ctx(
            request,
            user,
            "admin_cms",
            slot_keys=CONTENT_SLOT_KEYS,
            selected_key=key,
            blocks=blocks,
            current=current,
            versions=versions,
            preview=safe_markdown(current.body_md if current else "", inline=False),
            flash_ok=request.query_params.get("ok"),
            flash_error=request.query_params.get("err"),
        ),
    )


@router.post("/content/preview", response_class=HTMLResponse)
async def cms_content_preview(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    _: None = Depends(require_csrf),
):
    form = await request.form()
    return HTMLResponse(str(safe_markdown(str(form.get("body_md") or ""), inline=False)))


@router.post("/content/publish")
async def cms_content_publish(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    form = _to_dict(await request.form())
    key = str(form.get("key") or "")
    try:
        row = publish_content_block(
            db,
            key=key,
            title=str(form.get("title") or key),
            body_md=str(form.get("body_md") or ""),
            status=str(form.get("status") or "draft"),
            user_id=user.id,
        )
        db.commit()
        return _redirect(f"/admin/cms/content?key={quote(row.key)}", ok="Слот сохранён")
    except ValueError as exc:
        db.rollback()
        return _redirect("/admin/cms/content", err=str(exc))


@router.post("/content/{key}/rollback/{version}")
def cms_content_rollback(
    key: str,
    version: int,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    try:
        row = rollback_content_block(db, key=key, version=version, user_id=user.id)
        db.commit()
        return _redirect(f"/admin/cms/content?key={quote(row.key)}", ok="Версия восстановлена")
    except ValueError as exc:
        db.rollback()
        return _redirect(f"/admin/cms/content?key={quote(key)}", err=str(exc))


@router.get("/promos", response_class=HTMLResponse)
def cms_promos(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    promos = db.scalars(select(PromoCode).order_by(PromoCode.id.desc())).all()
    payment_counts = dict(
        db.execute(
            select(Payment.promo_code_id, func.count())
            .where(Payment.promo_code_id.is_not(None))
            .group_by(Payment.promo_code_id)
        ).all()
    )
    return templates.TemplateResponse(
        request=request,
        name="admin/cms_promos.html",
        context=_ctx(
            request,
            user,
            "admin_cms",
            promos=promos,
            payment_counts=payment_counts,
            flash_ok=request.query_params.get("ok"),
            flash_error=request.query_params.get("err"),
        ),
    )


@router.post("/promos")
async def cms_promos_save(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    form = _to_dict(await request.form())
    try:
        promo = upsert_promo_code(db, form)
        db.commit()
        return _redirect("/admin/cms/promos", ok=f"Промокод {promo.code} сохранён")
    except Exception as exc:
        db.rollback()
        return _redirect("/admin/cms/promos", err=str(exc))


@router.post("/promos/{promo_id}/delete")
def cms_promos_delete(
    promo_id: int,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    promo = db.get(PromoCode, promo_id)
    if promo is not None:
        promo.is_active = False
        db.commit()
    return _redirect("/admin/cms/promos", ok="Промокод выключен")


@router.get("/announcements", response_class=HTMLResponse)
def cms_announcements(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
):
    rows = db.scalars(select(Announcement).order_by(Announcement.id.desc())).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/cms_announcements.html",
        context=_ctx(
            request,
            user,
            "admin_cms",
            announcements=rows,
            flash_ok=request.query_params.get("ok"),
            flash_error=request.query_params.get("err"),
        ),
    )


@router.post("/announcements")
async def cms_announcements_save(
    request: Request,
    user: CurrentUser = Depends(require_service_admin),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    form = _to_dict(await request.form())
    row = upsert_announcement(db, form, user_id=user.id)
    db.commit()
    return _redirect("/admin/cms/announcements", ok=f"Объявление #{row.id} сохранено")
