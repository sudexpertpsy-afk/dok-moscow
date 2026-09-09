"""Публичная страница «Практика» (/praktika) — статьи + маркетинг."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.security import get_csrf_token
from app.services.cms import get_content_slots
from app.services.praktika import get_praktika_article, list_praktika_articles
from app.services.public_catalog import get_catalog_item
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
    articles = list_praktika_articles()
    # Есть статьи — индекс статей; иначе маркетинговая заглушка.
    name = "landing/praktika_index.html" if articles else "landing/praktika.html"
    return templates.TemplateResponse(
        request=request,
        name=name,
        context=_ctx(request, db, articles=articles),
    )


@router.api_route("/{slug}", methods=["GET", "HEAD"], response_class=HTMLResponse)
def praktika_detail(slug: str, request: Request, db: Session = Depends(get_db)):
    article = get_praktika_article(slug)
    if article is None:
        raise HTTPException(status_code=404, detail="Статья не найдена")
    obraztsy_links = []
    for s in article.related_obraztsy:
        item = get_catalog_item(s)
        if item is not None:
            obraztsy_links.append(item)
    zakon_links = [{"slug": s, "title": s} for s in article.related_zakon]
    # Подтянуть заголовки актов, если есть в БД
    if article.related_zakon:
        from sqlalchemy import select

        from app.models import LegalAct, LegalActStatus

        acts = {
            a.slug: a.title
            for a in db.scalars(
                select(LegalAct).where(
                    LegalAct.slug.in_(list(article.related_zakon)),
                    LegalAct.status == LegalActStatus.active,
                )
            ).all()
        }
        zakon_links = [
            {"slug": s, "title": acts.get(s, s)} for s in article.related_zakon
        ]
    return templates.TemplateResponse(
        request=request,
        name="landing/praktika_detail.html",
        context=_ctx(
            request,
            db,
            article=article,
            obraztsy_links=obraztsy_links,
            zakon_links=zakon_links,
        ),
    )
