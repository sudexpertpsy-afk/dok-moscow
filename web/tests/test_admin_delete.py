"""Удаление организаций и пользователей из админки."""

from __future__ import annotations

from sqlalchemy import select

from app.defaults import empty_requisites
from app.models import Organization, User, UserRole
from app.security import hash_password
from conftest import csrf_from, login


def _seed_org_user(dbmod, *, email: str = "del-user@example.com") -> tuple[int, int]:
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="Орг На Удаление", requisites=empty_requisites())
        db.add(org)
        db.flush()
        u = User(
            org_id=org.id,
            email=email,
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
            is_active=True,
        )
        db.add(u)
        db.commit()
        return org.id, u.id
    finally:
        db.close()


def test_delete_organization_from_list(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    org_id, user_id = _seed_org_user(dbmod)

    r = client.get("/admin/organizations")
    assert r.status_code == 200
    assert "Удалить" in r.text
    assert f"/admin/organizations/{org_id}/delete" in r.text

    token = csrf_from(client, "/admin/organizations")
    r = client.post(
        f"/admin/organizations/{org_id}/delete",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "org-deleted" in r.headers["location"]

    db = dbmod.SessionLocal()
    try:
        assert db.get(Organization, org_id) is None
        assert db.get(User, user_id) is None
    finally:
        db.close()

    r = client.get("/admin/organizations?ok=org-deleted")
    assert r.status_code == 200
    assert "Организация удалена" in r.text


def test_delete_user_from_list(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    _org_id, user_id = _seed_org_user(dbmod, email="del-only-user@example.com")

    r = client.get("/admin/users")
    assert r.status_code == 200
    assert f"/admin/users/{user_id}/delete" in r.text

    token = csrf_from(client, "/admin/users")
    r = client.post(
        f"/admin/users/{user_id}/delete",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "user-deleted" in r.headers["location"]

    db = dbmod.SessionLocal()
    try:
        assert db.get(User, user_id) is None
    finally:
        db.close()


def test_cannot_delete_self(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    db = dbmod.SessionLocal()
    try:
        admin = db.scalar(select(User).where(User.email == "admin@dok.moscow"))
        assert admin is not None
        admin_id = admin.id
    finally:
        db.close()

    token = csrf_from(client, "/admin/users")
    r = client.post(
        f"/admin/users/{admin_id}/delete",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "err=" in r.headers["location"]
    db = dbmod.SessionLocal()
    try:
        assert db.get(User, admin_id) is not None
    finally:
        db.close()


def test_org_admin_forbidden_delete(app):
    client, dbmod = app
    org_id, user_id = _seed_org_user(dbmod, email="member@example.com")
    assert login(client, "member@example.com", "Passw0rd!").status_code == 303
    token = csrf_from(client, "/cabinet/")
    r = client.post(
        f"/admin/organizations/{org_id}/delete",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 403
    r = client.post(
        f"/admin/users/{user_id}/delete",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 403
