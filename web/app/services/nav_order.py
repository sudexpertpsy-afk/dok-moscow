"""Пользовательский порядок пунктов бокового меню."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import User
from app.navigation import NAV_REGISTRY

NAV_AREAS = frozenset({"cabinet", "admin"})


def known_keys_for_area(area: str) -> set[str]:
    return {item.key for item in NAV_REGISTRY if item.area == area and item.menu}


def apply_nav_order(
    items: list[tuple[str, str, str]],
    preferred: list[str] | None,
) -> list[tuple[str, str, str]]:
    """Применить сохранённый порядок; неизвестные/новые ключи — в конце как в реестре."""
    if not preferred:
        return items
    by_key = {row[0]: row for row in items}
    ordered: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for key in preferred:
        row = by_key.get(key)
        if row is not None and key not in seen:
            ordered.append(row)
            seen.add(key)
    for row in items:
        if row[0] not in seen:
            ordered.append(row)
    return ordered


def order_for_area(nav_order: dict | None, area: str) -> list[str] | None:
    if not isinstance(nav_order, dict):
        return None
    raw = nav_order.get(area)
    if not isinstance(raw, list):
        return None
    return [str(x) for x in raw if isinstance(x, str) and x.strip()]


def sanitize_order(area: str, keys: list[str]) -> list[str]:
    allowed = known_keys_for_area(area)
    out: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key in allowed and key not in seen:
            out.append(key)
            seen.add(key)
    return out


def save_nav_order(db: Session, user_id: int, area: str, keys: list[str]) -> dict:
    if area not in NAV_AREAS:
        raise ValueError("Неизвестная область меню")
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("Пользователь не найден")
    cleaned = sanitize_order(area, keys)
    current = dict(user.nav_order) if isinstance(user.nav_order, dict) else {}
    current[area] = cleaned
    user.nav_order = current
    db.add(user)
    db.commit()
    db.refresh(user)
    return current
