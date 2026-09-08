"""Публичная страница «Практика» (/praktika) — контент-хук лендинга."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.security import get_csrf_token
from app.services.cms import get_content_slots
from app.templating import templates

router = APIRouter(prefix="/praktika", tags=["praktika"])


def _ctx(request: Request, db: Session | None = None, **extra):
    from app.services.analytics import get_analytics_public

    settings = get_settings()
    content_slots = get_content_slots(db) if db is not None else {}
    data = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": settings.app_name,
        "public_base_url": settings.public_base_url.rstrip("/"),
        "app_base_url": settings.app_base_url.rstrip("/"),
        "analytics_public": get_analytics_public(db),
        "content_slots": content_slots,
        "announcement": None,
    }
    data.update(extra)
    return data


@router.api_route("", methods=["GET", "HEAD"], include_in_schema=False)
def praktika_noslash(request: Request):
    qs = request.url.query
    target = "/praktika/" + (f"?{qs}" if qs else "")
    return RedirectResponse(url=target, status_code=301)


@router.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def praktika_index(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request=request,
        name="landing/praktika.html",
        context=_ctx(request, db),
    )
