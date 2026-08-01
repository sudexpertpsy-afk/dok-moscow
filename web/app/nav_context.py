"""Контекст навигации для шаблонов кабинета и админки."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.deps import CurrentUser
from app.navigation import admin_menu_tuples, cabinet_menu_tuples, roles_for_user
from app.services.billing import get_tariff_limits
from app.services.nav_order import apply_nav_order, order_for_area


def cabinet_nav(db: Session, user: CurrentUser) -> list[tuple[str, str, str]]:
    roles = roles_for_user(
        is_service_admin=user.is_service_admin,
        has_org=user.org_id is not None,
        is_org_admin=user.is_org_admin,
    )
    tariff = None
    if user.org_id is not None:
        tariff = get_tariff_limits(db, user.org_id).tariff_code
    items = cabinet_menu_tuples(roles=roles, tariff=tariff)
    return apply_nav_order(items, order_for_area(user.nav_order, "cabinet"))


def admin_nav(user: CurrentUser | None = None) -> list[tuple[str, str, str]]:
    items = admin_menu_tuples()
    if user is None:
        return items
    return apply_nav_order(items, order_for_area(user.nav_order, "admin"))
