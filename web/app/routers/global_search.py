"""API глобального поиска и палитры Cmd+K (W-23)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import CurrentUser, get_current_user
from app.services.global_search import global_search

router = APIRouter(prefix="/api", tags=["search"])


@router.get("/global-search")
def api_global_search(
    q: str = Query("", max_length=200),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = global_search(db, user, q)
    return JSONResponse(
        {
            "query": result.query,
            "empty": result.empty,
            "groups": [
                {
                    "key": g.key,
                    "title": g.title,
                    "items": [
                        {
                            "title": i.title,
                            "url": i.url,
                            "subtitle": i.subtitle,
                            "badge": i.badge,
                            "upsell": i.upsell,
                        }
                        for i in g.items
                    ],
                }
                for g in result.groups
            ],
            "sitemap": [
                {"group": name, "items": items} for name, items in result.sitemap
            ],
            "hint": (
                "Не нашлось. Посмотрите каталог разделов"
                if result.empty and len(result.query) >= 2
                else ""
            ),
        }
    )
