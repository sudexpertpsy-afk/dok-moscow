"""API глобального поиска и палитры Cmd+K (W-23/W-29), порядок меню."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import CurrentUser, get_current_user
from app.rate_limit import LoginRateLimiter
from app.security import check_csrf
from app.services.nav_order import NAV_AREAS, save_nav_order
from app.services.search import result_to_api_dict, search

router = APIRouter(prefix="/api", tags=["search"])

# F-06: поиск — 60 запросов / мин на пользователя
search_limiter = LoginRateLimiter(60, 60, name="global_search")


@router.get("/global-search")
def api_global_search(
    q: str = Query("", max_length=200),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    key = f"user:{user.id}"
    if search_limiter.is_blocked(key):
        raise HTTPException(status_code=429, detail="Слишком много запросов поиска")
    search_limiter.register_failure(key)
    result = search(db, user, q, limit=8)
    show_all = f"/cabinet/search?q={quote(result.query)}" if len(result.query) >= 2 else None
    return JSONResponse(result_to_api_dict(result, show_all_url=show_all))


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
    return JSONResponse({"ok": True, "area": area, "order": saved.get(area, [])})
