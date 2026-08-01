"""API глобального поиска и палитры Cmd+K (W-23), порядок меню."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import CurrentUser, get_current_user
from app.security import check_csrf
from app.services.global_search import global_search
from app.services.nav_order import NAV_AREAS, save_nav_order

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


@router.post("/nav-order")
async def api_nav_order(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Сохранить порядок пунктов бокового меню (cabinet | admin)."""
    csrf = request.headers.get("x-csrf-token")
    if not check_csrf(request, csrf):
        raise HTTPException(status_code=403, detail="Неверный CSRF-токен")
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Некорректный JSON") from exc
    area = str(payload.get("area") or "").strip()
    order = payload.get("order")
    if area not in NAV_AREAS:
        raise HTTPException(status_code=400, detail="Неизвестная область меню")
    if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
        raise HTTPException(status_code=400, detail="order должен быть списком ключей")
    if area == "admin" and not user.is_service_admin:
        raise HTTPException(status_code=403, detail="Только администратор сервиса")
    if area == "cabinet" and user.org_id is None:
        raise HTTPException(status_code=403, detail="Нет организации")
    saved = save_nav_order(db, user.id, area, order)
    # обновить снимок в сессии не требуется — CurrentUser читается из БД
    return JSONResponse({"ok": True, "area": area, "order": saved.get(area, [])})
