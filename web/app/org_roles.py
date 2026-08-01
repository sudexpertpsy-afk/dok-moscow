"""Роли внутри организации (W-27)."""

from __future__ import annotations

from app.models import OrgRole, User, UserRole


def effective_org_role(user: User | None) -> OrgRole | None:
    """Эффективная роль в org. None без организации.

    Если колонка ещё не заполнена (старые фикстуры) — считаем org_admin,
    чтобы не ломать одиночные тестовые учётки до явного org_member.
    """
    if user is None or user.org_id is None:
        return None
    if user.org_role is not None:
        return user.org_role
    return OrgRole.org_admin


def is_org_admin(user: User | None) -> bool:
    return effective_org_role(user) == OrgRole.org_admin


def is_org_member(user: User | None) -> bool:
    return effective_org_role(user) == OrgRole.org_member


def default_org_role_for_new_member() -> OrgRole:
    return OrgRole.org_member


def org_role_for_org_creator() -> OrgRole:
    return OrgRole.org_admin


def user_has_service_role(user: User) -> bool:
    return user.role == UserRole.service_admin
