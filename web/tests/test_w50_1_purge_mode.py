"""W-50.1 §3: PURGE_MODE dry|live + переключатель."""

from __future__ import annotations

from datetime import timedelta

from conftest import csrf_from, login
from sqlalchemy import select

from app.defaults import empty_requisites
from app.models import Event, Organization, utcnow
from app.services.billing import ensure_payment_settings
from app.services.org_purge import purge_empty_orgs_daily, set_purge_mode


def _empty_old_org(db, name: str) -> int:
    org = Organization(name=name, requisites=empty_requisites())
    db.add(org)
    db.flush()
    org.created_at = utcnow() - timedelta(days=10)
    db.commit()
    return org.id


def test_purge_dry_keeps_orgs_and_writes_event(app):
    _client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        ps = ensure_payment_settings(db)
        ps.purge_mode = "dry"
        ps.purge_mode_changed_at = utcnow()
        org_id = _empty_old_org(db, "Dry candidate")
        stats = purge_empty_orgs_daily(db)
        assert stats["mode"] == "dry"
        assert stats["purged"] == 0
        assert stats["candidates"] >= 1
        assert db.get(Organization, org_id) is not None
        ev = db.scalar(
            select(Event).where(Event.type == "org.purge_candidate").order_by(Event.id.desc())
        )
        assert ev is not None
        assert (ev.details or {}).get("org_id") == org_id
    finally:
        db.close()


def test_purge_live_deletes(app):
    _client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        ps = ensure_payment_settings(db)
        ps.purge_mode = "live"
        ps.purge_mode_changed_at = utcnow()
        db.commit()
        org_id = _empty_old_org(db, "Live purge me")
        stats = purge_empty_orgs_daily(db)
        assert stats["mode"] == "live"
        assert stats["purged"] >= 1
        assert db.get(Organization, org_id) is None
    finally:
        db.close()


def test_purge_mode_toggle_event_and_org_admin_forbidden(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    token = csrf_from(client, "/admin/organizations")
    r = client.post(
        "/admin/organizations/purge-mode",
        data={"csrf_token": token, "mode": "live"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    db = dbmod.SessionLocal()
    try:
        ps = ensure_payment_settings(db)
        assert ps.purge_mode == "live"
        ev = db.scalar(
            select(Event)
            .where(Event.type == "settings.purge_mode_changed")
            .order_by(Event.id.desc())
        )
        assert ev is not None
        assert (ev.details or {}).get("to") == "live"
        # идемпотентность set_purge_mode
        set_purge_mode(db, "live", actor_id=1)
    finally:
        db.close()

    # org_admin → 403
    from app.models import OrgRole, User, UserRole
    from app.security import hash_password

    db = dbmod.SessionLocal()
    try:
        org = Organization(name="OA org", requisites=empty_requisites())
        db.add(org)
        db.flush()
        db.add(
            User(
                org_id=org.id,
                email="oa-purge@example.com",
                password_hash=hash_password("UserPass123!"),
                role=UserRole.user,
                org_role=OrgRole.org_admin,
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    client.post("/logout", data={"csrf_token": csrf_from(client, "/admin/")})
    assert login(client, "oa-purge@example.com", "UserPass123!").status_code == 303
    token = csrf_from(client, "/cabinet/")
    r = client.post(
        "/admin/organizations/purge-mode",
        data={"csrf_token": token, "mode": "dry"},
        follow_redirects=False,
    )
    assert r.status_code == 403

    page = client.get("/admin/organizations")
    # org_admin не видит админку
    assert page.status_code == 403
