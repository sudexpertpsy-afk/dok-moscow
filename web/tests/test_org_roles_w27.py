"""W-27: роли организации, гейты настроек, сотрудники."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import Invite, OrgRole, Organization, TariffCode, User, UserRole, utcnow
from app.navigation import NAV_REGISTRY, roles_for_user, resolve_nav, NavRole
from app.security import hash_password
from app.services.billing import ensure_beta_subscriptions, ensure_tariffs
from app.services.staff import create_org_invite, transfer_org_admin
from conftest import csrf_from, login


def _seed_pair(dbmod, *, tariff: TariffCode = TariffCode.organization):
    """org_admin + org_member в одной организации."""
    from app.models import Subscription, SubscriptionPeriod, SubscriptionStatus, Tariff

    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        org = Organization(name="Staff Org", requisites={})
        db.add(org)
        db.flush()
        admin = User(
            org_id=org.id,
            email="admin27@example.com",
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
            org_role=OrgRole.org_admin,
            is_active=True,
        )
        member = User(
            org_id=org.id,
            email="member27@example.com",
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
            org_role=OrgRole.org_member,
            is_active=True,
        )
        db.add_all([admin, member])
        t = db.scalar(select(Tariff).where(Tariff.code == tariff))
        assert t is not None
        db.add(
            Subscription(
                org_id=org.id,
                tariff_id=t.id,
                period=SubscriptionPeriod.month,
                starts_at=utcnow() - timedelta(days=1),
                ends_at=utcnow() + timedelta(days=30),
                status=SubscriptionStatus.active,
                auto_renew=False,
                is_beta=False,
            )
        )
        db.commit()
        return org.id, admin.id, member.id
    finally:
        db.close()


def test_roles_for_user_respects_org_admin():
    assert NavRole.org_admin in roles_for_user(
        is_service_admin=False, has_org=True, is_org_admin=True
    )
    assert NavRole.org_admin not in roles_for_user(
        is_service_admin=False, has_org=True, is_org_admin=False
    )
    assert NavRole.org_user in roles_for_user(
        is_service_admin=False, has_org=True, is_org_admin=False
    )


def test_nav_matrix_admin_member_guest():
    """Для пунктов кабинета — доступность по ролям/тарифу."""
    admin_roles = roles_for_user(is_service_admin=False, has_org=True, is_org_admin=True)
    member_roles = roles_for_user(is_service_admin=False, has_org=True, is_org_admin=False)
    guest_t = TariffCode.guest
    org_t = TariffCode.organization

    admin_org = {r.item.key: r for r in resolve_nav(roles=admin_roles, tariff=org_t, area="cabinet")}
    member_org = {r.item.key: r for r in resolve_nav(roles=member_roles, tariff=org_t, area="cabinet")}
    admin_guest = {r.item.key: r for r in resolve_nav(roles=admin_roles, tariff=guest_t, area="cabinet", include_upsell=True)}

    assert "staff" in admin_org and admin_org["staff"].allowed
    assert "staff" not in member_org
    assert "templates" in admin_org and admin_org["templates"].allowed
    assert "templates" not in member_org
    assert "settings" in member_org
    assert "documents" in member_org
    # guest: staff/templates — upsell или скрыты без тарифа
    if "staff" in admin_guest:
        assert admin_guest["staff"].upsell or not admin_guest["staff"].allowed


def test_member_cannot_edit_settings(app):
    client, dbmod = app
    _seed_pair(dbmod)
    assert login(client, "member27@example.com", "Passw0rd!").status_code == 303
    r = client.get("/cabinet/settings/")
    assert r.status_code == 200
    assert "Просмотр реквизитов" in r.text or "readonly" in r.text.lower() or "disabled" in r.text
    token = csrf_from(client, "/cabinet/settings/")
    r = client.post(
        "/cabinet/settings/",
        data={"csrf_token": token, "наименование": "Hack"},
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert "администратор" in r.text.lower() or "Недостаточно" in r.text

    r = client.get("/cabinet/settings/bank")
    assert r.status_code == 403

    r = client.get("/cabinet/settings/security")
    assert r.status_code == 200


def test_member_cannot_open_staff_or_templates(app):
    client, dbmod = app
    _seed_pair(dbmod)
    assert login(client, "member27@example.com", "Passw0rd!").status_code == 303
    assert client.get("/cabinet/staff/").status_code == 403
    assert client.get("/cabinet/templates/").status_code == 403


def test_admin_invite_and_transfer(app):
    client, dbmod = app
    org_id, admin_id, member_id = _seed_pair(dbmod)
    assert login(client, "admin27@example.com", "Passw0rd!").status_code == 303
    r = client.get("/cabinet/staff/")
    assert r.status_code == 200
    assert "member27@example.com" in r.text

    token = csrf_from(client, "/cabinet/staff/")
    r = client.post(
        "/cabinet/staff/invite",
        data={"csrf_token": token, "email": "new27@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "ok=invite" in r.headers["location"]

    db = dbmod.SessionLocal()
    try:
        inv = db.scalar(
            select(Invite).where(Invite.org_id == org_id, Invite.email == "new27@example.com")
        )
        assert inv is not None
        admin = db.get(User, admin_id)
        member = db.get(User, member_id)
        transfer_org_admin(db, org=admin.organization, actor=admin, target=member, confirm=True)
        db.refresh(admin)
        db.refresh(member)
        assert admin.org_role == OrgRole.org_member
        assert member.org_role == OrgRole.org_admin
    finally:
        db.close()


def test_invite_over_limit_upsell(app):
    client, dbmod = app
    org_id, _, _ = _seed_pair(dbmod, tariff=TariffCode.organization)
    db = dbmod.SessionLocal()
    try:
        # добиваем до лимита 5 активных
        for i in range(3):
            db.add(
                User(
                    org_id=org_id,
                    email=f"u{i}@lim27.example.com",
                    password_hash=hash_password("Passw0rd!"),
                    role=UserRole.user,
                    org_role=OrgRole.org_member,
                    is_active=True,
                )
            )
        db.commit()
    finally:
        db.close()

    assert login(client, "admin27@example.com", "Passw0rd!").status_code == 303
    token = csrf_from(client, "/cabinet/staff/")
    r = client.post(
        "/cabinet/staff/invite",
        data={"csrf_token": token, "email": "overflow@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/cabinet/billing/" in r.headers["location"]


def test_migration_backfill_logic():
    """Логика миграции: старейший → admin (юнит на ORM-уровне)."""
    from app.org_roles import effective_org_role

    class U:
        org_id = 1
        org_role = None

    assert effective_org_role(U()) == OrgRole.org_admin
    U.org_role = OrgRole.org_member
    assert effective_org_role(U()) == OrgRole.org_member
