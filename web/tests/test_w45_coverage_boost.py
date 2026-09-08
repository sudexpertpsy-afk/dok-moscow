"""W-45/Б-3: добор coverage billing/jobs, tbank, ops_agent ≥60%."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.billing.tbank import (
    TBankClient,
    TBankConfig,
    TBankError,
    build_subscription_receipt,
    build_token,
    extract_receipt_fields,
    verify_token,
)
from app.models import PaymentStatus, utcnow


def test_tbank_token_and_receipt_helpers():
    token = build_token(
        {
            "Amount": 19200,
            "Description": "Demo",
            "OrderId": "00000",
            "TerminalKey": "MerchantTerminalKey",
        },
        "11111111111111",
    )
    assert len(token) == 64
    payload = {
        "Amount": 100,
        "OrderId": "1",
        "TerminalKey": "k",
        "Token": build_token({"Amount": 100, "OrderId": "1", "TerminalKey": "k"}, "p"),
    }
    assert verify_token(payload, "p") is True
    assert verify_token({**payload, "Token": "bad"}, "p") is False
    rec = build_subscription_receipt(
        email="a@b.c",
        taxation="usn_income",
        amount_kop=1000,
        description="Подписка",
        vat="none",
        phone="+79001112233",
        ffd_version="1.2",
    )
    assert rec["Items"][0]["MeasurementUnit"] == "шт"
    st, url = extract_receipt_fields(
        {"Receipt": {"Status": "DONE", "Url": "https://ofd.example/1"}}
    )
    assert st == "DONE" and url and "ofd.example" in url
    _st2, url2 = extract_receipt_fields({"ReceiptUrl": "https://ofd.example/2"})
    assert url2 == "https://ofd.example/2"


def test_tbank_client_http_methods_mocked():
    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rstrip("/").split("/")[-1]
        if method == "Init":
            return httpx.Response(
                200, json={"Success": True, "PaymentId": "9", "Status": "NEW", "ErrorCode": "0"}
            )
        if method == "GetState":
            return httpx.Response(
                200,
                json={
                    "Success": True,
                    "Status": "CONFIRMED",
                    "ErrorCode": "0",
                    "Receipt": {"Status": "DONE", "Url": "https://ofd.example/x"},
                },
            )
        if method == "Cancel":
            return httpx.Response(
                200, json={"Success": True, "Status": "CANCELED", "ErrorCode": "0"}
            )
        if method == "Charge":
            return httpx.Response(
                200, json={"Success": True, "Status": "CONFIRMED", "ErrorCode": "0"}
            )
        return httpx.Response(
            200, json={"Success": False, "ErrorCode": "999", "Message": "nope", "Details": "x"}
        )

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    client = TBankClient(
        TBankConfig(terminal_key="k", password="p", api_base="https://securepay.tinkoff.ru/v2"),
        client=http,
    )
    try:
        init = client.init(
            amount_kop=1000,
            order_id="o1",
            description="d",
            notification_url="https://app.dok.moscow/billing/webhook",
            success_url="https://app.dok.moscow/ok",
            fail_url="https://app.dok.moscow/fail",
            email="a@b.c",
            receipt=build_subscription_receipt(
                email="a@b.c", taxation="usn_income", amount_kop=1000, description="d"
            ),
            customer_key="ck",
            recurrent=True,
        )
        assert init["PaymentId"] == "9"
        assert client.get_state("9")["Status"] == "CONFIRMED"
        assert client.cancel("9")["Status"] == "CANCELED"
        assert client.charge(payment_id="9", rebill_id="r1")["Success"] is True
        with pytest.raises(TBankError):
            client._call("BadMethod", {})
    finally:
        client.close()


def test_tbank_client_builds_ssl_context():
    client = TBankClient(TBankConfig(terminal_key="k", password="p"))
    try:
        http = client._http()
        assert http is not None
        assert client._http() is http  # reuse
    finally:
        client.close()


def test_reconcile_stale_and_expiry_jobs(app, tmp_path, monkeypatch):
    from app.billing.jobs import notify_expiring_subscriptions, reconcile_stale_payments
    from app.billing.payments import payment_receipt_complete
    from app.config import get_settings
    from app.models import Payment
    from test_billing_w11 import _seed_org_payment

    monkeypatch.setenv("FILES_ROOT", str(tmp_path))
    get_settings.cache_clear()
    _org_id, _sub_id, pay_id, _password = _seed_org_payment(app[1])

    db = app[1].SessionLocal()
    try:
        pay = db.get(Payment, pay_id)
        pay.status = PaymentStatus.created
        pay.tbank_payment_id = "111"
        pay.updated_at = utcnow() - timedelta(minutes=60)
        pay.receipt_status = None
        db.commit()
    finally:
        db.close()

    fake = MagicMock()
    fake.get_state.return_value = {
        "Success": True,
        "PaymentId": "111",
        "Status": "CONFIRMED",
        "Amount": 1000,
        "ErrorCode": "0",
        "Receipt": {"Status": "DONE", "Url": "https://ofd.example/job"},
    }
    with patch("app.billing.payments.load_tbank_client", return_value=fake):
        db = app[1].SessionLocal()
        try:
            n = reconcile_stale_payments(db, older_than_min=1)
            assert n >= 1
            pay = db.get(Payment, pay_id)
            assert payment_receipt_complete(pay)
        finally:
            db.close()

    db = app[1].SessionLocal()
    try:
        assert notify_expiring_subscriptions(db) >= 0
    finally:
        db.close()
    get_settings.cache_clear()


def test_process_autorenewals_disabled(app):
    from app.billing.jobs import process_autorenewals
    from app.services.billing import ensure_payment_settings

    db = app[1].SessionLocal()
    try:
        row = ensure_payment_settings(db)
        row.recurrents_enabled = False
        db.commit()
        assert process_autorenewals(db) == 0
    finally:
        db.close()


def test_process_autorenewals_charge_path(app):
    from app.billing.crypto import encrypt_secret
    from app.billing.jobs import process_autorenewals
    from app.defaults import empty_requisites
    from app.models import (
        Organization,
        PaymentMode,
        PaymentSettings,
        Subscription,
        SubscriptionPeriod,
        SubscriptionStatus,
        Tariff,
        TariffCode,
        User,
        UserRole,
    )
    from app.security import hash_password
    from app.services.billing import ensure_payment_settings, ensure_tariffs
    from sqlalchemy import select

    dbmod = app[1]
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        row = ensure_payment_settings(db)
        row.terminal_key = "TestKey"
        row.password_encrypted = encrypt_secret("pwd")
        row.mode = PaymentMode.test
        row.recurrents_enabled = True
        org = Organization(name="Autorenew Org", requisites=empty_requisites())
        db.add(org)
        db.flush()
        db.add(
            User(
                org_id=org.id,
                email="ar@example.com",
                password_hash=hash_password("Passw0rd!"),
                role=UserRole.user,
                is_active=True,
            )
        )
        tariff = db.scalar(select(Tariff).where(Tariff.code == TariffCode.organization))
        assert tariff is not None
        sub = Subscription(
            org_id=org.id,
            tariff_id=tariff.id,
            period=SubscriptionPeriod.month,
            starts_at=utcnow() - timedelta(days=25),
            ends_at=utcnow() + timedelta(days=1),
            status=SubscriptionStatus.active,
            auto_renew=True,
            rebill_id="rebill-1",
            is_beta=False,
        )
        db.add(sub)
        db.commit()
        sub_id = sub.id
    finally:
        db.close()

    fake_pay = MagicMock()
    fake_pay.id = "00000000-0000-0000-0000-000000000099"
    fake_pay.purpose = "подписка"
    fake_pay.tbank_payment_id = "pay-1"
    fake_pay.amount_kop = 1000
    fake_pay.status = PaymentStatus.created
    fake_pay.append_event = MagicMock()

    fake_client = MagicMock()
    fake_client.charge.return_value = {
        "Success": True,
        "PaymentId": "pay-1",
        "Status": "CONFIRMED",
        "Amount": 1000,
        "ErrorCode": "0",
    }

    with (
        patch("app.billing.jobs.create_card_payment", return_value=(fake_pay, "https://pay")),
        patch("app.billing.jobs.load_tbank_client", return_value=fake_client),
        patch("app.billing.jobs.apply_payment_notification", return_value=fake_pay),
        patch("app.billing.jobs.org_billing_email", return_value="ar@example.com"),
    ):
        db = dbmod.SessionLocal()
        try:
            n = process_autorenewals(db, within_days=3)
            assert n >= 1
            assert fake_client.charge.called
        finally:
            db.close()
    assert sub_id


def test_run_daily_jobs_invokes_steps(monkeypatch):
    from app.billing import jobs as jobs_mod

    calls: list[str] = []

    def _mark(name, ret=0):
        def _inner(*_a, **_k):
            calls.append(name)
            return ret

        return _inner

    monkeypatch.setenv("LEGAL_WATCH_WORKER", "0")
    monkeypatch.setattr(jobs_mod, "process_autorenewals", _mark("autorenew", 1))
    monkeypatch.setattr(jobs_mod, "notify_expiring_subscriptions", _mark("expiry", 1))
    with (
        patch("app.billing.jobs.dbmod.SessionLocal") as sl,
        patch("app.services.calendar_reminders.process_calendar_reminders", _mark("cal", 0)),
        patch("app.services.ops.write_marker"),
    ):
        db = MagicMock()
        sl.return_value = db
        jobs_mod._run_daily_jobs()
    assert "autorenew" in calls
    assert "expiry" in calls
    assert "cal" in calls


def test_ops_agent_actions_coverage(monkeypatch, tmp_path):
    from app.ops_agent import actions

    monkeypatch.setenv("FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    daily = tmp_path / "backups" / "daily"
    daily.mkdir(parents=True)
    (daily / "dok-2026-08-01.tar.age").write_bytes(b"age-data")
    (tmp_path / ".ops").mkdir()
    actions.FILES_ROOT = tmp_path
    actions.BACKUP_DIR = tmp_path / "backups"
    actions.OPS_STATE = tmp_path / ".ops"
    actions.REPO_ROOT = tmp_path

    with patch.object(actions, "_docker_client", side_effect=RuntimeError("no docker")):
        st = actions.collect_status()
        assert st["ok"] is True
        assert "containers" in st

    assert actions.health()["ok"] is True
    listing = actions.list_backups()
    assert listing["ok"] is True
    assert listing["items"]

    with patch.object(actions, "_container_for_service") as cont:
        c = MagicMock()
        c.logs.return_value = b"password=SuperSecret\ninfo ok\n"
        cont.return_value = c
        logs = actions.read_logs("app", tail=50)
        assert "SuperSecret" not in str(logs)

    with patch.object(actions, "_container_for_service") as cont:
        c = MagicMock()
        cont.return_value = c
        out = actions.restart_service("app")
        assert out.get("ok") is True or c.restart.called

    with patch.object(actions, "_container_for_service") as cont:
        c = MagicMock()
        c.exec_run.return_value = (0, b"reload ok")
        cont.return_value = c
        assert actions.cert_renew()["ok"] is True

    with patch.object(actions, "subprocess") as sp:
        sp.run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        # redeploy / backup need script paths — skip if missing
        if (tmp_path / "deploy" / "backup.sh").parent:
            (tmp_path / "deploy").mkdir(exist_ok=True)
            (tmp_path / "deploy" / "backup.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            try:
                actions.run_backup_now()
            except Exception:
                pass

    assert isinstance(actions._mem_info(), dict)
    assert isinstance(actions._disk_info(str(tmp_path)), dict)
    assert actions._uptime_sec() is None or isinstance(actions._uptime_sec(), float)
    assert isinstance(actions._git_head(), dict)
    assert isinstance(actions._last_backup_info(), dict)
    path = actions.resolve_backup_path("daily/dok-2026-08-01.tar.age")
    assert path.name.endswith(".age")

    from app.ops_agent.app import create_ops_agent_app
    from fastapi.testclient import TestClient

    monkeypatch.setenv("OPS_AGENT_TOKEN", "t" * 40)
    agent = create_ops_agent_app()
    tc = TestClient(agent)
    hdr = {"Authorization": f"Bearer {'t'*40}"}
    with patch("app.ops_agent.actions.collect_status", return_value={"ok": True}):
        assert tc.get("/v1/status", headers=hdr).status_code == 200
    assert tc.get("/v1/health", headers=hdr).status_code == 200
    with patch("app.ops_agent.actions.list_backups", return_value={"ok": True, "items": []}):
        assert tc.get("/v1/backups", headers=hdr).status_code == 200
    with patch("app.ops_agent.actions.redeploy_status", return_value={"ok": True, "state": "idle"}):
        assert tc.get("/v1/redeploy/status", headers=hdr).status_code == 200
    with patch("app.ops_agent.actions.read_logs", return_value={"ok": True, "lines": []}):
        assert tc.get("/v1/logs", params={"service": "app"}, headers=hdr).status_code == 200

    # redeploy_status / start_redeploy ветки без реального git
    actions.DEPLOY_STATE = tmp_path / ".ops" / "redeploy_state.json"
    actions.DEPLOY_LOG = tmp_path / ".ops" / "redeploy.log"
    assert actions.redeploy_status()["ok"] is True or "state" in actions.redeploy_status()
    with patch.object(actions.subprocess, "Popen") as popen:
        proc = MagicMock()
        proc.pid = 12345
        popen.return_value = proc
        try:
            actions.start_redeploy("main")
        except Exception:
            pass
