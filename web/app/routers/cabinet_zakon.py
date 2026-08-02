"""Законодательство внутри кабинета (W-35)."""

from __future__ import annotations

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import CurrentUser, require_csrf, require_org_user
from app.models import ActFragment, LegalActMode, Organization
from app.nav_context import cabinet_nav
from app.security import get_csrf_token
from app.services import cabinet_zakon as cz
from app.services.legal_public import (
    CATEGORY_LABEL,
    archived_versions,
    format_act_meta,
    get_act_by_slug,
    highlight,
    list_catalog,
    published_for,
    search_acts_grouped,
)
from app.services.legal_search import article_anchor
from app.services.sources.publication_api import PublicationClient
from app.templating import templates

router = APIRouter(prefix="/cabinet/zakon", tags=["cabinet-zakon"])

DISCLAIMER = (
    "Тексты приводятся в справочных целях по официальным источникам "
    "и не являются официальным опубликованием."
)
PAGE_SIZE = 20


def _org(db: Session, user: CurrentUser) -> Organization:
    org = db.get(Organization, user.org_id)
    if org is None:
        raise RuntimeError("org missing")
    return org


def _base_ctx(request: Request, user: CurrentUser, db: Session, **extra):
    settings = get_settings()
    org = _org(db, user)
    access = cz.paid_access(db, user.org_id)
    data = {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "app_name": settings.app_name,
        "user": user,
        "org": org,
        "nav": cabinet_nav(db, user),
        "active": "zakon_nav",
        "public_base_url": settings.public_base_url.rstrip("/"),
        "app_base_url": settings.app_base_url.rstrip("/"),
        "disclaimer": DISCLAIMER,
        "category_label": CATEGORY_LABEL,
        "article_anchor": article_anchor,
        "paid": access.allowed,
        "is_guest_tariff": access.is_guest,
        "billing_url": "/cabinet/billing/",
    }
    data.update(extra)
    return data


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def _rewrite_search_hits(hits, groups_search):
    """Переписать URL хитов на кабинетные."""
    for h in hits:
        slug = h.act.slug
        h_url = cz.cabinet_hit_url(slug, h.article_ref or "")
        # LegalSearchHit.url — property; подменим через monkey на объекте нельзя.
        # В шаблоне используем cabinet_url helper.
        setattr(h, "cabinet_url", h_url)
    for act, act_hits in groups_search:
        for h in act_hits:
            setattr(h, "cabinet_url", cz.cabinet_hit_url(act.slug, h.article_ref or ""))


@router.get("", include_in_schema=False)
def cabinet_zakon_index_trailing_slash(request: Request):
    """Один 301 /cabinet/zakon → /cabinet/zakon/ (относительный Location)."""
    qs = request.url.query
    target = "/cabinet/zakon/" + (f"?{qs}" if qs else "")
    return RedirectResponse(url=target, status_code=301)


@router.get("/", response_class=HTMLResponse)
def cabinet_zakon_index(
    request: Request,
    q: str = Query(""),
    category: str = Query(""),
    authority: str = Query(""),
    status: str = Query(""),
    revision_from: str = Query(""),
    revision_to: str = Query(""),
    page: int = Query(1, ge=1),
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    query = (q or "").strip()
    status_norm = (status or "").strip().casefold()
    if status_norm in ("архив", "archive", "repealed"):
        status_norm = "repealed"
    elif status_norm in ("действует", "active", ""):
        status_norm = "active" if status_norm else ""

    result = None
    hits = []
    groups_search = []
    total = 0
    pages = 1
    if query:
        offset = (page - 1) * PAGE_SIZE
        result = search_acts_grouped(
            db,
            query,
            limit=PAGE_SIZE,
            offset=offset,
            category=category or None,
            authority=authority or None,
            status=status_norm or None,
            revision_from=_parse_date(revision_from),
            revision_to=_parse_date(revision_to),
        )
        hits = result.hits
        groups_search = result.groups
        total = result.total
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        _rewrite_search_hits(hits, groups_search)

    catalog = list_catalog(db) if not query else []
    bookmarks = cz.list_bookmarks_rich(db, user.id)
    history = cz.recent_views(db, user.id)
    watches = cz.list_watches(db, user.id) if cz.paid_access(db, user.org_id).allowed else []
    return templates.TemplateResponse(
        request=request,
        name="cabinet/zakon_index.html",
        context=_base_ctx(
            request,
            user,
            db,
            query=query,
            hits=hits,
            groups_search=groups_search,
            groups=catalog,
            highlight=highlight,
            filters={
                "category": category,
                "authority": authority,
                "status": status,
                "revision_from": revision_from,
                "revision_to": revision_to,
            },
            page=page,
            pages=pages,
            total=total,
            direct=result.direct if result else None,
            bookmarks=bookmarks,
            history=history,
            watches=watches,
        ),
    )


@router.get("/search", response_class=HTMLResponse)
def cabinet_zakon_search(
    request: Request,
    q: str = Query(""),
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    """Явный alias поиска — те же параметры, что у каталога."""
    qs = request.url.query
    target = "/cabinet/zakon/"
    if qs:
        target = f"{target}?{qs}"
    elif q:
        target = f"{target}?q={quote(q)}"
    return RedirectResponse(target, status_code=303)


@router.get("/{slug}", response_class=HTMLResponse)
def cabinet_zakon_act(
    slug: str,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    act = get_act_by_slug(db, slug)
    if act is None:
        return templates.TemplateResponse(
            request=request,
            name="cabinet/placeholder.html",
            context=_base_ctx(
                request,
                user,
                db,
                title="Акт не найден",
                hint="Проверьте адрес или вернитесь в каталог.",
            ),
            status_code=404,
        )
    published = published_for(act)
    archives = archived_versions(act)
    fragments = sorted(act.fragments or [], key=lambda f: f.sort_order)
    pdf_url = None
    if act.eo_number:
        pdf_url = PublicationClient().pdf_url(act.eo_number)
    cz.record_view(db, user_id=user.id, act_id=act.id)
    db.commit()
    access = cz.paid_access(db, user.org_id)
    notes = cz.notes_for_act(db, user.id, act.id) if access.allowed else {}
    return templates.TemplateResponse(
        request=request,
        name="cabinet/zakon_act.html",
        context=_base_ctx(
            request,
            user,
            db,
            act=act,
            published=published,
            archives=archives,
            fragments=fragments,
            meta_line=format_act_meta(act),
            mode_full=LegalActMode.full_text,
            mode_fragments=LegalActMode.fragments,
            mode_card=LegalActMode.card,
            pdf_url=pdf_url,
            bookmarked=cz.bookmarked_keys(db, user.id, act.id),
            bookmark_key_fn=cz.bookmark_key,
            watching=cz.is_watching(db, user.id, act.id),
            notes=notes,
        ),
    )


@router.post("/{slug}/bookmark")
def cabinet_zakon_bookmark(
    slug: str,
    request: Request,
    fragment_id: int | None = Form(None),
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    act = get_act_by_slug(db, slug)
    if act is None:
        return RedirectResponse("/cabinet/zakon/", status_code=303)
    frag_id = fragment_id
    if frag_id is not None:
        frag = db.get(ActFragment, frag_id)
        if frag is None or frag.act_id != act.id:
            frag_id = None
    on = cz.toggle_bookmark(db, user_id=user.id, act_id=act.id, fragment_id=frag_id)
    db.commit()
    if request.headers.get("HX-Request"):
        return HTMLResponse("★" if on else "☆")
    anchor = ""
    if frag_id:
        frag = db.get(ActFragment, frag_id)
        if frag:
            anchor = f"#{article_anchor(frag.article_ref)}"
    return RedirectResponse(f"/cabinet/zakon/{slug}{anchor}", status_code=303)


@router.post("/{slug}/watch")
def cabinet_zakon_watch(
    slug: str,
    request: Request,
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    act = get_act_by_slug(db, slug)
    if act is None:
        return RedirectResponse("/cabinet/zakon/", status_code=303)
    access = cz.paid_access(db, user.org_id)
    if not access.allowed:
        return RedirectResponse("/cabinet/billing/?error=zakon_watch", status_code=303)
    on = cz.toggle_watch(db, user_id=user.id, act_id=act.id)
    db.commit()
    if request.headers.get("HX-Request"):
        return HTMLResponse("🔔 вкл." if on else "🔔 выкл.")
    return RedirectResponse(f"/cabinet/zakon/{slug}", status_code=303)


@router.post("/{slug}/note/{fragment_id}")
def cabinet_zakon_note(
    slug: str,
    fragment_id: int,
    body: str = Form(""),
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
    _: None = Depends(require_csrf),
):
    act = get_act_by_slug(db, slug)
    frag = db.get(ActFragment, fragment_id)
    if act is None or frag is None or frag.act_id != act.id:
        return RedirectResponse("/cabinet/zakon/", status_code=303)
    access = cz.paid_access(db, user.org_id)
    if not access.allowed:
        return RedirectResponse("/cabinet/billing/?error=zakon_notes", status_code=303)
    cz.upsert_note(db, user_id=user.id, fragment_id=fragment_id, body=body)
    db.commit()
    return RedirectResponse(
        f"/cabinet/zakon/{slug}#{article_anchor(frag.article_ref)}",
        status_code=303,
    )


@router.get("/{slug}/quote")
def cabinet_zakon_quote(
    slug: str,
    fragment_id: int | None = Query(None),
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    act = get_act_by_slug(db, slug)
    if act is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    published = published_for(act)
    frag = None
    if fragment_id is not None:
        frag = db.get(ActFragment, fragment_id)
        if frag is None or frag.act_id != act.id:
            frag = None
    text = cz.quote_for_fragment(act, frag, published)
    return JSONResponse({"text": text})


@router.get("/{slug}/export.docx")
def cabinet_zakon_export(
    slug: str,
    fragment_id: int | None = Query(None),
    user: CurrentUser = Depends(require_org_user),
    db: Session = Depends(get_db),
):
    access = cz.paid_access(db, user.org_id)
    if not access.allowed:
        return RedirectResponse("/cabinet/billing/?error=zakon_export", status_code=303)
    act = get_act_by_slug(db, slug)
    if act is None:
        return RedirectResponse("/cabinet/zakon/", status_code=303)
    published = published_for(act)
    fragments = sorted(act.fragments or [], key=lambda f: f.sort_order)
    if fragment_id is not None:
        fragments = [f for f in fragments if f.id == fragment_id]
        if not fragments:
            return RedirectResponse(f"/cabinet/zakon/{slug}", status_code=303)
    elif not fragments and published:
        # full_text: одна «псевдостатья» из body
        pseudo = ActFragment(
            id=0,
            act_id=act.id,
            article_ref=act.number or "Текст",
            title=act.title,
            body_html=published.body_html or "",
            sort_order=0,
        )
        fragments = [pseudo]
    data = cz.export_fragments_docx(act, fragments, published)
    filename = f"{act.slug}.docx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
