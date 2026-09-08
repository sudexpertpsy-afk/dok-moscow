"""Публичный лендинг dok.moscow: главная, ПДн, контакты, заявки, SEO."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.rate_limit import LoginRateLimiter
from app.security import check_csrf, get_csrf_token
from app.services.cms import (
    active_announcement,
    format_price_rub,
    get_content_slots,
    list_public_tariffs,
    tariff_blurb,
    tariff_features,
    tariff_price_label,
)
from app.services.landing_demo import (
    beta_promo_copy,
    landing_stats,
    load_content_hooks,
    load_demo_examples,
)
from app.services.leads import create_lead, normalize_lead_inn, notify_admin_new_lead
from app.templating import templates

router = APIRouter(tags=["landing"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_INN_RE = re.compile(r"^(\d{10}|\d{12})$")
lead_limiter = LoginRateLimiter(limit=5, window_sec=60 * 60, name="lead")

PROFILES = (
    "Экспертная организация (СРО)",
    "Судебно-экспертное учреждение",
    "Независимый эксперт / ИП",
    "Юридическая компания",
    "Другое",
)


def _public_ctx(request: Request, db: Session | None = None, **extra):
    from app.services.analytics import get_analytics_public

    settings = get_settings()
    public_tariffs = []
    content_slots = {}
    announcement = None
    analytics_public = get_analytics_public(db)
    if db is not None:
        public_tariffs = list_public_tariffs(db)
        content_slots = get_content_slots(db)
        announcement = active_announcement(
            db,
            dismissed_id=request.cookies.get("dok_announcement_dismissed"),
        )
    data = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": settings.app_name,
        "public_base_url": settings.public_base_url.rstrip("/"),
        "app_base_url": settings.app_base_url.rstrip("/"),
        "analytics_public": analytics_public,
        "profiles": PROFILES,
        "flash_error": None,
        "flash_ok": None,
        "form_email": "",
        "form_profile": "",
        "form_inn": "",
        "form_comment": "",
        "public_tariffs": public_tariffs,
        "content_slots": content_slots,
        "announcement": announcement,
        "format_price_rub": format_price_rub,
        "tariff_price_label": tariff_price_label,
        "tariff_blurb": tariff_blurb,
        "tariff_features": tariff_features,
        "beta_promo": beta_promo_copy(db),
        "landing_stats": landing_stats(db),
        "demo_examples": load_demo_examples(),
        "content_hooks": load_content_hooks(),
    }
    data.update(extra)
    return data


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


@router.get("/", response_class=HTMLResponse)
def landing_home(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request=request,
        name="landing/index.html",
        context=_public_ctx(request, db),
    )


def _apply_form_ctx(
    request: Request,
    db: Session,
    *,
    email: str = "",
    profile: str = "",
    inn: str = "",
    comment: str = "",
    **extra,
):
    return _public_ctx(
        request,
        db,
        form_email=email,
        form_profile=profile,
        form_inn=inn,
        form_comment=comment,
        **extra,
    )


@router.post("/apply", response_class=HTMLResponse)
def landing_apply(
    request: Request,
    email: str = Form(""),
    profile: str = Form(""),
    inn: str = Form(""),
    comment: str = Form(""),
    website: str = Form(""),  # honeypot
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    if not check_csrf(request, csrf_token):
        return templates.TemplateResponse(
            request=request,
            name="landing/index.html",
            context=_apply_form_ctx(
                request,
                db,
                email=email,
                profile=profile,
                inn=inn,
                comment=comment,
                flash_error="Сессия устарела. Обновите страницу и отправьте форму снова.",
            ),
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # боты заполняют скрытое поле
    if website.strip():
        return RedirectResponse("/#apply", status_code=status.HTTP_303_SEE_OTHER)

    key = f"lead:{_client_key(request)}"
    limit = settings.lead_rate_limit
    window = settings.lead_rate_window_sec
    if lead_limiter.limit != limit or lead_limiter.window_sec != window:
        lead_limiter.limit = limit
        lead_limiter.window_sec = window

    if lead_limiter.is_blocked(key):
        return templates.TemplateResponse(
            request=request,
            name="landing/index.html",
            context=_apply_form_ctx(
                request,
                db,
                email=email,
                profile=profile,
                inn=inn,
                comment=comment,
                flash_error="Слишком много заявок с вашего адреса. Попробуйте позже.",
            ),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    email_n = email.strip().lower()
    profile_n = profile.strip()
    comment_n = comment.strip()[:2000]
    inn_n = normalize_lead_inn(inn)

    if not _EMAIL_RE.match(email_n):
        return templates.TemplateResponse(
            request=request,
            name="landing/index.html",
            context=_apply_form_ctx(
                request,
                db,
                email=email,
                profile=profile,
                inn=inn,
                comment=comment,
                flash_error="Укажите корректный e-mail.",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not profile_n or profile_n not in PROFILES:
        return templates.TemplateResponse(
            request=request,
            name="landing/index.html",
            context=_apply_form_ctx(
                request,
                db,
                email=email,
                profile=profile,
                inn=inn,
                comment=comment,
                flash_error="Выберите профиль деятельности.",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if inn_n is not None and not _INN_RE.match(inn_n):
        return templates.TemplateResponse(
            request=request,
            name="landing/index.html",
            context=_apply_form_ctx(
                request,
                db,
                email=email,
                profile=profile,
                inn=inn,
                comment=comment,
                flash_error="ИНН — 10 или 12 цифр, либо оставьте поле пустым.",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    lead, _is_new = create_lead(
        db,
        email=email_n,
        profile=profile_n,
        comment=comment_n or None,
        inn=inn_n,
    )
    notify_admin_new_lead(settings, lead, db=db)
    lead_limiter.register_failure(key)  # считаем успешные отправки в окне

    return templates.TemplateResponse(
        request=request,
        name="landing/index.html",
        context=_public_ctx(
            request,
            db,
            flash_ok="Заявка принята. Мы свяжемся с вами по e-mail, когда откроем доступ.",
        ),
        status_code=status.HTTP_201_CREATED,
    )


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request=request,
        name="landing/privacy.html",
        context=_public_ctx(request, db),
    )


@router.get("/offer", response_class=HTMLResponse)
def offer_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request=request,
        name="landing/offer.html",
        context=_public_ctx(request, db),
    )


@router.get("/requisites", response_class=HTMLResponse)
def requisites_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request=request,
        name="landing/requisites.html",
        context=_public_ctx(request, db),
    )


@router.get("/tariffs", response_class=HTMLResponse)
def tariffs_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request=request,
        name="landing/tariffs.html",
        context=_public_ctx(request, db),
    )


@router.get("/contacts", response_class=HTMLResponse)
def contacts_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request=request,
        name="landing/contacts.html",
        context=_public_ctx(request, db),
    )


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt(request: Request):
    from app.hosting import host_role, request_host

    settings = get_settings()
    base = settings.public_base_url.rstrip("/")
    role = host_role(request_host(request.headers.get("host")))
    if role == "app":
        return PlainTextResponse("User-agent: *\nDisallow: /\n")
    return PlainTextResponse(
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /cabinet\n"
        "Disallow: /admin\n"
        "Disallow: /invite\n"
        "Disallow: /apply\n"
        "Disallow: /manifest.json\n"
        "Disallow: /login\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )


@router.get("/sitemap.xml")
def sitemap_xml(request: Request, db: Session = Depends(get_db)):
    from app.hosting import host_role, redirect_url_for_path, request_host
    from app.models import ActVersionStatus, LegalAct, LegalActMode, LegalActStatus
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload

    role = host_role(request_host(request.headers.get("host")))
    if role == "app":
        return RedirectResponse(redirect_url_for_path("/sitemap.xml"), status_code=301)

    base = get_settings().public_base_url.rstrip("/")
    from app.services.public_catalog import all_obraztsy_paths

    paths = [
        "/",
        "/privacy",
        "/offer",
        "/requisites",
        "/tariffs",
        "/contacts",
        "/zakon/",
        "/praktika/",
    ]
    paths.extend(all_obraztsy_paths())
    body = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path in paths:
        body.append(f"  <url><loc>{base}{path}</loc><changefreq>weekly</changefreq></url>")
    # SEO: только активные акты с опубликованной редакцией (или card со ссылкой)
    acts = db.scalars(
        select(LegalAct)
        .options(joinedload(LegalAct.versions))
        .where(LegalAct.status == LegalActStatus.active)
        .order_by(LegalAct.sort_order, LegalAct.id)
    ).unique().all()
    for act in acts:
        published = next(
            (v for v in (act.versions or []) if v.status == ActVersionStatus.published),
            None,
        )
        if published is None and act.mode != LegalActMode.card:
            continue
        lastmod = ""
        if published is not None:
            ts = published.reviewed_at or published.loaded_at or published.created_at
            if ts is not None:
                lastmod = f"<lastmod>{ts.date().isoformat()}</lastmod>"
        body.append(
            f"  <url><loc>{base}/zakon/{act.slug}</loc>"
            f"{lastmod}<changefreq>weekly</changefreq></url>"
        )
    body.append("</urlset>")
    return Response("\n".join(body) + "\n", media_type="application/xml")