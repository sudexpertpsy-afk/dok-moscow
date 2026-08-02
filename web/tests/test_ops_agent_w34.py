"""W-34: ops-agent whitelist и раздел /admin/server/."""

from __future__ import annotations

import os
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.ops_agent import WHITELIST_ACTIONS
from app.ops_agent.app import create_ops_agent_app
from app.ops_agent.mask import mask_log_line
from app.services.hostland import load_hostland, maybe_send_vds_reminders, save_hostland
from app.totp_2fa import enable_totp, generate_totp_secret, verify_totp_code
from conftest import csrf_from, login
import pyotp


def test_whitelist_inventory_matches_routes():
    """Аудит: каждый action из инвентаря обслуживается маршрутом (или наоборот)."""
    app = create_ops_agent_app()
    paths = {
        getattr(r, "path", None)
        for r in app.routes
        if getattr(r, "path", None)
    }
    expected = {
        "status": "/v1/status",
        "logs": "/v1/logs",
        "restart": "/v1/restart",
        "redeploy": "/v1/redeploy",
        "backup_now": "/v1/backup",
        "backups_list": "/v1/backups",
        "backup_download": "/v1/backups/file",
        "cert_renew": "/v1/cert_renew",
        "health": "/v1/health",
        "redeploy_status": "/v1/redeploy/status",
    }
    assert set(expected) == set(WHITELIST_ACTIONS)
    for action, path in expected.items():
        assert path in paths, f"{action} → {path} отсутствует"


def test_ops_agent_rejects_without_token(monkeypatch):
    monkeypatch.setenv("OPS_AGENT_TOKEN", "x" * 40)
    app = create_ops_agent_app()
    client = TestClient(app)
    r = client.get("/v1/status")
    assert r.status_code == 401
    r = client.get("/v1/nope-action")
    assert r.status_code in (401, 404)


def test_ops_agent_unknown_action_404(monkeypatch):
    token = "t" * 40
    monkeypatch.setenv("OPS_AGENT_TOKEN", token)
    app = create_ops_agent_app()
    client = TestClient(app)
    r = client.get("/v1/exec", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404
    assert r.json().get("error") == "unknown_action"


def test_mask_log_hides_secrets():
    line = mask_log_line("password=SuperSecret123 Authorization: Bearer abc.def")
    assert "SuperSecret123" not in line
    assert "abc.def" not in line
    assert "***" in line


def test_admin_server_page_without_agent(app):
    client, _ = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    r = client.get("/admin/server/")
    assert r.status_code == 200
    assert "Сервер" in r.text
    assert "Ops-agent" in r.text or "OPS_AGENT" in r.text


def test_admin_server_mutations_require_2fa(app, monkeypatch):
    client, dbmod = app
    monkeypatch.setenv("OPS_AGENT_ENABLED", "true")
    monkeypatch.setenv("OPS_AGENT_TOKEN", "z" * 40)
    from app.config import get_settings

    get_settings.cache_clear()
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    token = csrf_from(client, "/admin/server/")
    r = client.post(
        "/admin/server/restart",
        data={
            "csrf_token": token,
            "service": "worker",
            "totp_code": "000000",
            "confirm": "RESTART",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "2FA" in (r.headers.get("location") or "")


def test_admin_server_calls_agent_with_totp(app, monkeypatch):
    client, dbmod = app
    secret = generate_totp_secret()
    db = dbmod.SessionLocal()
    try:
        from sqlalchemy import select
        from app.models import User

        admin = db.scalar(select(User).where(User.email == "admin@dok.moscow"))
        enable_totp(admin, secret=secret, backup_codes=["ABCD-EFGH-IJKL"])
        db.commit()
    finally:
        db.close()

    calls: list[tuple] = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"ok": True, "service": "worker"}

    monkeypatch.setattr(
        "app.routers.admin_server.ops_agent_configured", lambda: True
    )
    monkeypatch.setattr("app.routers.admin_server.agent_request", fake_request)

    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    code = pyotp.TOTP(secret).now()
    assert verify_totp_code(secret, code)
    token2 = csrf_from(client, "/login/2fa")
    assert client.post(
        "/login/2fa",
        data={"csrf_token": token2, "code": code},
        follow_redirects=False,
    ).status_code == 303
    token = csrf_from(client, "/admin/server/")
    r = client.post(
        "/admin/server/restart",
        data={
            "csrf_token": token,
            "service": "worker",
            "totp_code": code,
            "confirm": "RESTART",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "ok=" in (r.headers.get("location") or "")
    assert any(c[:2] == ("POST", "/v1/restart") for c in calls)


def test_hostland_vds_reminder(app, monkeypatch, tmp_path):
    client, dbmod = app
    monkeypatch.setenv("FILES_ROOT", str(tmp_path / "files"))
    monkeypatch.setenv("ADMIN_NOTIFY_EMAIL", "owner@example.com")
    from app.config import get_settings

    get_settings.cache_clear()
    sent: list[dict] = []

    def fake_send(settings, *, to_addr, subject, body):
        sent.append({"to": to_addr, "subject": subject, "body": body})
        return True

    monkeypatch.setattr("app.services.hostland.send_email", fake_send)
    save_hostland(vds_paid_until=date.today() + timedelta(days=14))
    db = dbmod.SessionLocal()
    try:
        fired = maybe_send_vds_reminders(db)
    finally:
        db.close()
    assert "vds_remind_14" in fired
    assert sent and "14" in sent[0]["subject"]
    # повтор в тот же день — нет
    fired2 = maybe_send_vds_reminders(dbmod.SessionLocal())
    assert fired2 == []


def test_nav_has_admin_server():
    from app.navigation import NAV_REGISTRY

    assert any(i.key == "admin_server" for i in NAV_REGISTRY)
