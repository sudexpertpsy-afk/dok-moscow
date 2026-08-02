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
from app.services.leads import create_lead, notify_admin_new_lead
from app.templating import templates

router = APIRouter(tags=["landing"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
lead_limiter = LoginRateLimiter(limit=5, window_sec=60 * 60, name="lead")

PROFILES = (
    "Экспертная организация (СРО)",
    "Судебно-экспертное учреждение",
    "Независимый эксперт / ИП",
    "Юридическая компания",
    "Другое",
)


def _public_ctx(request: Request, **extra):
    settings = get_settings()
    data = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": settings.app_name,
        "public_base_url": settings.public_base_url.rstrip("/"),
        "app_base_url": settings.app_base_url.rstrip("/"),
        "yandex_metrika_id": settings.yandex_metrika_id.strip(),
        "profiles": PROFILES,
        "flash_error": None,
        "flash_ok": None,
        "form_email": "",
        "form_profile": "",
        "form_comment": "",
    }
    data.update(extra)
    return data


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


@router.get("/", response_class=HTMLResponse)
def landing_home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="landing/index.html",
        context=_public_ctx(request),
    )


@router.post("/apply", response_class=HTMLResponse)
def landing_apply(
    request: Request,
    email: str = Form(""),
    profile: str = Form(""),
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
            context=_public_ctx(
                request,
                flash_error="Сессия устарела. Обновите страницу и отправьте форму снова.",
                form_email=email,
                form_profile=profile,
                form_comment=comment,
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
            context=_public_ctx(
                request,
                flash_error="Слишком много заявок с вашего адреса. Попробуйте позже.",
                form_email=email,
                form_profile=profile,
                form_comment=comment,
            ),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    email_n = email.strip().lower()
    profile_n = profile.strip()
    comment_n = comment.strip()[:2000]

    if not _EMAIL_RE.match(email_n):
        return templates.TemplateResponse(
            request=request,
            name="landing/index.html",
            context=_public_ctx(
                request,
                flash_error="Укажите корректный e-mail.",
                form_email=email,
                form_profile=profile,
                form_comment=comment,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not profile_n or profile_n not in PROFILES:
        return templates.TemplateResponse(
            request=request,
            name="landing/index.html",
            context=_public_ctx(
                request,
                flash_error="Выберите профиль деятельности.",
                form_email=email,
                form_profile=profile,
                form_comment=comment,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    lead = create_lead(db, email=email_n, profile=profile_n, comment=comment_n or None)
    notify_admin_new_lead(settings, lead)
    lead_limiter.register_failure(key)  # считаем успешные отправки в окне

    return templates.TemplateResponse(
        request=request,
        name="landing/index.html",
        context=_public_ctx(
            request,
            flash_ok="Заявка принята. Мы свяжемся с вами по e-mail, когда откроем доступ.",
        ),
        status_code=status.HTTP_201_CREATED,
    )


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="landing/privacy.html",
        context=_public_ctx(request),
    )


@router.get("/offer", response_class=HTMLResponse)
def offer_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="landing/offer.html",
        context=_public_ctx(request),
    )


@router.get("/requisites", response_class=HTMLResponse)
def requisites_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="landing/requisites.html",
        context=_public_ctx(request),
    )


@router.get("/tariffs", response_class=HTMLResponse)
def tariffs_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="landing/tariffs.html",
        context=_public_ctx(request),
    )


@router.get("/contacts", response_class=HTMLResponse)
def contacts_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="landing/contacts.html",
        context=_public_ctx(request),
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
    from app.models import LegalAct, LegalActStatus
    from sqlalchemy import select

    role = host_role(request_host(request.headers.get("host")))
    if role == "app":
        return RedirectResponse(redirect_url_for_path("/sitemap.xml"), status_code=301)

    base = get_settings().public_base_url.rstrip("/")
    paths = ["/", "/privacy", "/offer", "/requisites", "/tariffs", "/contacts", "/zakon/"]
    body = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path in paths:
        body.append(f"  <url><loc>{base}{path}</loc><changefreq>weekly</changefreq></url>")
    slugs = db.scalars(
        select(LegalAct.slug).where(LegalAct.status == LegalActStatus.active)
    ).all()
    for slug in slugs:
        body.append(
            f"  <url><loc>{base}/zakon/{slug}</loc><changefreq>weekly</changefreq></url>"
        )
    body.append("</urlset>")
    return Response("\n".join(body) + "\n", media_type="application/xml")