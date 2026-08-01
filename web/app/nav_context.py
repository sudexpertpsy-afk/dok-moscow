"""Контекст навигации для шаблонов кабинета и админки."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.deps import CurrentUser
from app.navigation import admin_menu_tuples, cabinet_menu_tuples, roles_for_user
from app.services.billing import get_tariff_limits


def cabinet_nav(db: Session, user: CurrentUser) -> list[tuple[str, str, str]]:
    roles = roles_for_user(is_service_admin=user.is_service_admin, has_org=user.org_id is not None)
    tariff = None
    if user.org_id is not None:
        tariff = get_tariff_limits(db, user.org_id).tariff_code
    return cabinet_menu_tuples(roles=roles, tariff=tariff)


def admin_nav() -> list[tuple[str, str, str]]:
    return admin_menu_tuples()
