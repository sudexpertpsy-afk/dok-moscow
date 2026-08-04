"""W-31/W-32: кэш-заголовки, ops-маркеры, admin/status."""

from __future__ import annotations

from app.services.ops import (
    check_alerts,
    clear_timings,
    latency_stats,
    ops_dir,
    record_timing,
    record_webhook_fail,
    record_webhook_ok,
    status_snapshot,
    write_marker,
)
from conftest import login


def _admin(dbmod):
    from app.models import User, UserRole
    from app.security import hash_password

    db = dbmod.SessionLocal()
    try:
        u = User(
            org_id=None,
            email="ops-admin@example.com",
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.service_admin,
            is_active=True,
        )
        db.add(u)
        db.commit()
    finally:
        db.close()


def test_cache_headers_cabinet_and_landing(app):
    client, _ = app
    r = client.get("/")
    assert r.status_code == 200
    assert "max-age=300" in (r.headers.get("cache-control") or "")

    r2 = client.get("/cabinet/", follow_redirects=False)
    cc = (r2.headers.get("cache-control") or "").lower()
    assert "max-age=300" not in cc


def test_vary_hx_request(app):
    client, dbmod = app
    from app.defaults import empty_requisites
    from app.models import Organization, User, UserRole
    from app.security import hash_password

    db = dbmod.SessionLocal()
    try:
        org = Organization(name="OpsOrg", requisites=empty_requisites())
        db.add(org)
        db.flush()
        db.add(
            User(
                org_id=org.id,
                email="ops-user@example.com",
                password_hash=hash_password("Passw0rd!"),
                role=UserRole.user,
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()
    assert login(client, "ops-user@example.com", "Passw0rd!").status_code == 303
    r = client.get("/cabinet/journal", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "HX-Request" in (r.headers.get("vary") or "")


def test_latency_ring_and_status_page(app):
    client, dbmod = app
    clear_timings()
    record_timing("/cabinet/documents/", 12.0)
    record_timing("/cabinet/documents/", 40.0)
    record_timing("/zakon", 8.0)
    stats = latency_stats(window_sec=3600)
    assert any(s.group == "cabinet" and s.count == 2 for s in stats)

    write_marker("worker_heartbeat")
    record_webhook_ok()
    _admin(dbmod)
    assert login(client, "ops-admin@example.com", "Passw0rd!").status_code == 303
    r = client.get("/admin/status")
    assert r.status_code == 200
    assert "Worker heartbeat" in r.text
    assert "Латентность" in r.text
    assert "P50" in r.text

    db = dbmod.SessionLocal()
    try:
        snap = status_snapshot(db)
        assert "disk" in snap
        assert snap["webhook"]["ok"] is True
    finally:
        db.close()


def test_webhook_fail_streak_and_alerts(app):
    _, dbmod = app
    d = ops_dir()
    for p in d.glob("*.json"):
        p.unlink()
    write_marker("backup_ok")  # W-45: иначе сработает backup_missing
    for _ in range(3):
        record_webhook_fail("token")
    db = dbmod.SessionLocal()
    try:
        fired = check_alerts(db)
        assert "webhook_fail" in fired
        assert "backup_missing" not in fired
    finally:
        db.close()


def test_backup_marker_stale_and_fresh(app):
    """W-45/G-01: устаревший маркер → алерт; свежий → статус зелёный."""
    from datetime import datetime, timedelta, timezone
    import json
    from pathlib import Path

    _, dbmod = app
    d = ops_dir()
    for p in d.glob("*.json"):
        p.unlink()

    stale = {
        "at": (datetime.now(timezone.utc) - timedelta(hours=50)).isoformat(),
        "file": "/tmp/x.tar.age",
        "stamp": "stale",
    }
    (d / "backup_ok.json").write_text(json.dumps(stale), encoding="utf-8")
    db = dbmod.SessionLocal()
    try:
        snap = status_snapshot(db)
        backup_row = [x for x in snap["background"] if x[0].startswith("Бэкап")][0]
        assert backup_row[2] is False
        fired = check_alerts(db)
        assert "backup_stale" in fired
    finally:
        db.close()

    write_marker("backup_ok", file="/tmp/ok.tar.age", stamp="fresh")
    # сбросить cooldown алерта
    for p in d.glob("alert_backup_*.json"):
        p.unlink()
    db = dbmod.SessionLocal()
    try:
        snap = status_snapshot(db)
        backup_row = [x for x in snap["background"] if x[0].startswith("Бэкап")][0]
        assert backup_row[2] is True
        fired = check_alerts(db)
        assert "backup_stale" not in fired
        assert "backup_missing" not in fired
    finally:
        db.close()


def test_backup_marker_missing_alerts(app):
    _, dbmod = app
    d = ops_dir()
    for p in d.glob("*.json"):
        p.unlink()
    db = dbmod.SessionLocal()
    try:
        fired = check_alerts(db)
        assert "backup_missing" in fired
        snap = status_snapshot(db)
        backup_row = [x for x in snap["background"] if x[0].startswith("Бэкап")][0]
        assert backup_row[2] is False
    finally:
        db.close()


def test_static_url_fallback():
    from app.static_assets import clear_manifest_cache, static_url

    clear_manifest_cache()
    # если локально собран manifest — URL с хэшем; иначе исходное имя
    url = static_url("app.css")
    assert url.startswith("/static/")
    assert "app" in url and url.endswith(".css")


def test_error_pages_html(app):
    client, _ = app
    r = client.get("/no-such-page-xyz", headers={"Accept": "text/html"})
    assert r.status_code == 404
    assert "Страница не найдена" in r.text
    assert "На главную" in r.text
    r_cab = client.get("/cabinet/no-such", headers={"Accept": "text/html"})
    # без сессии — редирект на логин или 401/403; при 404 кабинета — app-шаблон
    if r_cab.status_code == 404:
        assert "В кабинет" in r_cab.text or "Страница не найдена" in r_cab.text


def test_route_exception_notes_cover_set():
    from app.navigation import ROUTE_EXCEPTION_NOTES, ROUTE_EXCEPTIONS

    assert ROUTE_EXCEPTIONS == frozenset(ROUTE_EXCEPTION_NOTES)
    assert "/cabinet/org/{org_id}" not in ROUTE_EXCEPTIONS
    assert "/cabinet/journal/rows" in ROUTE_EXCEPTION_NOTES
