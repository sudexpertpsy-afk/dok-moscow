"""Публичный каталог образцов (/obraztsy) — W-44 / W-45."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.security import get_csrf_token
from app.services.public_catalog import (
    catalog_grouped,
    get_catalog_item,
    normative_acts_for_item,
)
from app.templating import templates

router = APIRouter(tags=["obraztsy"])


def _ctx(request: Request, db: Session | None = None, **extra):
    from app.services.analytics import get_analytics_public

    settings = get_settings()
    data = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": settings.app_name,
        "public_base_url": settings.public_base_url.rstrip("/"),
        "app_base_url": settings.app_base_url.rstrip("/"),
        "analytics_public": get_analytics_public(db),
        "seo_year": date.today().year,
    }
    data.update(extra)
    return data


@router.get("/obraztsy", include_in_schema=False)
def obraztsy_noslash(request: Request):
    qs = request.url.query
    target = "/obraztsy/" + (f"?{qs}" if qs else "")
    return RedirectResponse(url=target, status_code=301)


@router.get("/obraztsy/", response_class=HTMLResponse)
def obraztsy_index(request: Request, db: Session = Depends(get_db)):
    groups = catalog_grouped()
    total = sum(len(items) for _, items in groups)
    return templates.TemplateResponse(
        request=request,
        name="landing/obraztsy_index.html",
        context=_ctx(request, db, groups=groups, total=total),
    )


@router.get("/obraztsy/{slug}", response_class=HTMLResponse)
def obraztsy_detail(slug: str, request: Request, db: Session = Depends(get_db)):
    item = get_catalog_item(slug)
    if item is None:
        return templates.TemplateResponse(
            request=request,
            name="landing/404.html",
            context=_ctx(request, db),
            status_code=404,
        )
    normative_acts = normative_acts_for_item(db, item)
    return templates.TemplateResponse(
        request=request,
        name="landing/obraztsy_detail.html",
        context=_ctx(
            request,
            db,
            item=item,
            normative_acts=normative_acts,
        ),
    )
