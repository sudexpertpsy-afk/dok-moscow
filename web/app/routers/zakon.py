"""Публичный раздел «Законодательство» (/zakon) — W-18."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import LegalActMode
from app.security import get_csrf_token
from app.services.legal_public import (
    CATEGORY_LABEL,
    archived_versions,
    format_act_meta,
    get_act_by_slug,
    highlight,
    list_catalog,
    published_for,
    search_acts,
)
from app.services.sources.publication_api import PublicationClient
from app.templating import templates

router = APIRouter(prefix="/zakon", tags=["zakon"])

DISCLAIMER = (
    "Тексты приводятся в справочных целях по официальным источникам "
    "и не являются официальным опубликованием."
)


def _ctx(request: Request, **extra):
    settings = get_settings()
    data = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": settings.app_name,
        "public_base_url": settings.public_base_url.rstrip("/"),
        "app_base_url": settings.app_base_url.rstrip("/"),
        "yandex_metrika_id": (settings.yandex_metrika_id or "").strip(),
        "disclaimer": DISCLAIMER,
        "category_label": CATEGORY_LABEL,
    }
    data.update(extra)
    return data


@router.get("/", response_class=HTMLResponse)
def zakon_index(
    request: Request,
    q: str = Query(""),
    db: Session = Depends(get_db),
):
    query = (q or "").strip()
    hits = search_acts(db, query) if query else []
    groups = list_catalog(db) if not query else []
    return templates.TemplateResponse(
        request=request,
        name="zakon/index.html",
        context=_ctx(
            request,
            query=query,
            hits=hits,
            groups=groups,
            highlight=highlight,
        ),
    )


@router.get("/{slug}", response_class=HTMLResponse)
def zakon_act(slug: str, request: Request, db: Session = Depends(get_db)):
    act = get_act_by_slug(db, slug)
    if act is None:
        return templates.TemplateResponse(
            request=request,
            name="landing/404.html",
            context=_ctx(request),
            status_code=404,
        )
    published = published_for(act)
    archives = archived_versions(act)
    fragments = sorted(act.fragments or [], key=lambda f: f.sort_order)
    pdf_url = None
    if act.eo_number:
        pdf_url = PublicationClient().pdf_url(act.eo_number)
    return templates.TemplateResponse(
        request=request,
        name="zakon/act.html",
        context=_ctx(
            request,
            act=act,
            published=published,
            archives=archives,
            fragments=fragments,
            meta_line=format_act_meta(act),
            mode_full=LegalActMode.full_text,
            mode_fragments=LegalActMode.fragments,
            mode_card=LegalActMode.card,
            pdf_url=pdf_url,
        ),
    )
