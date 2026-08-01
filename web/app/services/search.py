"""Единая поисковая служба кабинета (W-29).

Палитра Cmd+K и страница /cabinet/search используют один движок.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.deps import CurrentUser
from app.hosting import public_url
from app.services.global_search import (
    GlobalSearchResult,
    SearchGroup,
    SearchGroupItem,
    global_search as _global_search,
)


@dataclass
class PageSearchResult:
    core: GlobalSearchResult
    page: int
    per_group: int
    show_all_url: str


def search(
    db: Session,
    user: CurrentUser,
    query: str,
    *,
    limit: int = 8,
) -> GlobalSearchResult:
    """Поиск для палитры (короткий лимит)."""
    result = _global_search(db, user, query, limit=limit)
    return _absolutize_legal(result)


def search_page(
    db: Session,
    user: CurrentUser,
    query: str,
    *,
    page: int = 1,
    per_group: int = 20,
) -> PageSearchResult:
    """Полная страница результатов — та же группировка, больше строк."""
    page = max(1, page)
    per_group = min(50, max(5, per_group))
    # Берём расширенный лимит, затем режем «страницами» внутри групп
    full = _global_search(db, user, query, limit=per_group * page)
    full = _absolutize_legal(full)
    start = (page - 1) * per_group
    end = start + per_group
    paged_groups: list[SearchGroup] = []
    for g in full.groups:
        slice_items = g.items[start:end]
        if slice_items or page == 1:
            paged_groups.append(
                SearchGroup(key=g.key, title=g.title, items=slice_items or g.items[:per_group])
            )
    # Для page>1 пустые группы отбрасываем
    if page > 1:
        paged_groups = [g for g in paged_groups if g.items]
    full.groups = paged_groups
    full.empty = not full.groups and len((query or "").strip()) >= 2
    q = (query or "").strip()
    return PageSearchResult(
        core=full,
        page=page,
        per_group=per_group,
        show_all_url=f"/cabinet/search?q={q}" if q else "/cabinet/search",
    )


def _absolutize_legal(result: GlobalSearchResult) -> GlobalSearchResult:
    """Ссылки на /zakon ведут на публичный хост."""
    for g in result.groups:
        if g.key != "legal":
            continue
        fixed: list[SearchGroupItem] = []
        for it in g.items:
            url = it.url
            if url.startswith("/zakon"):
                url = public_url(url)
            fixed.append(
                SearchGroupItem(
                    title=it.title,
                    url=url,
                    subtitle=it.subtitle,
                    badge=it.badge,
                    upsell=it.upsell,
                )
            )
        g.items = fixed
    return result


def result_to_api_dict(result: GlobalSearchResult, *, show_all_url: str | None = None) -> dict:
    payload = {
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
        "sitemap": [{"group": name, "items": items} for name, items in result.sitemap],
        "hint": (
            "Не нашлось. Посмотрите каталог разделов"
            if result.empty and len(result.query) >= 2
            else ""
        ),
    }
    if show_all_url and result.query and len(result.query) >= 2 and not result.empty:
        payload["show_all_url"] = show_all_url
        payload["show_all_label"] = "Показать все результаты"
    return payload
