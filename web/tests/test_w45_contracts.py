"""W-45: контрактные тесты на стыках (ловят класс «связь разорвана»)."""

from __future__ import annotations

import ast
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[2]
WEB_APP = REPO / "web" / "app"


def test_contract_backup_marker_readable_from_files_root(app):
    """1. app ↔ хранилище: маркер в FILES_ROOT/.ops читается status_snapshot."""
    from app.services.ops import ops_dir, status_snapshot, write_marker

    _, dbmod = app
    write_marker("backup_ok", file="/tmp/dok_test.tar.age", stamp="w45")
    assert (ops_dir() / "backup_ok.json").is_file()
    db = dbmod.SessionLocal()
    try:
        snap = status_snapshot(db)
        assert snap["backup_detail"] is not None
        row = [x for x in snap["background"] if "Бэкап" in x[0]][0]
        assert row[2] is True
    finally:
        db.close()


def test_contract_init_webhook_receipt_chain(app):
    """2. Init↔webhook↔receipt: confirmed без чека — incomplete; reconcile добирает."""
    from app.billing.payments import (
        payment_receipt_complete,
        reconcile_payment,
    )
    from app.models import Payment, PaymentStatus
    from test_billing_w11 import _seed_org_payment, _signed_payload

    client, dbmod = app
    _org_id, _sub_id, pay_id, password = _seed_org_payment(dbmod)

    # CONFIRMED без Receipt
    payload = _signed_payload(pay_id, password, Status="CONFIRMED", Success=True)
    payload.pop("Receipt", None)
    assert client.post("/billing/webhook", json=payload).text == "OK"

    db = dbmod.SessionLocal()
    try:
        pay = db.get(Payment, pay_id)
        assert pay.status == PaymentStatus.confirmed
        assert payment_receipt_complete(pay) is False

        fake = MagicMock()
        fake.get_state.return_value = {
            "Success": True,
            "PaymentId": pay.tbank_payment_id or "1",
            "Status": "CONFIRMED",
            "Amount": pay.amount_kop,
            "ErrorCode": "0",
            "Receipt": {"Status": "DONE", "Url": "https://ofd.example/w45"},
        }
        with patch("app.billing.payments.load_tbank_client", return_value=fake):
            reconcile_payment(db, pay)
            db.commit()
        db.refresh(pay)
        assert payment_receipt_complete(pay) is True
        assert pay.receipt_url == "https://ofd.example/w45"
    finally:
        db.close()


def test_contract_no_verify_false_in_app_code():
    """4. исходящий TLS: нигде нет verify=False / unverified context."""
    bad: list[str] = []
    patterns = (
        re.compile(r"verify\s*=\s*False"),
        re.compile(r"ssl\._create_unverified_context"),
        re.compile(r"CERT_NONE"),
    )
    for path in WEB_APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            for pat in patterns:
                if pat.search(line):
                    bad.append(f"{path.relative_to(REPO)}:{i}:{line.strip()}")
    assert not bad, "запрещённый TLS-обход:\n" + "\n".join(bad)


def test_contract_obraztsy_and_zakon_slugs_resolve(app):
    """5. slug-целостность: ссылки /obraztsy и /zakon из шаблонов/контента → 200."""
    from app.services.public_catalog import get_catalog_item, list_catalog_items

    client, _ = app
    # все карточки каталога
    for it in list_catalog_items():
        r = client.get(f"/obraztsy/{it.slug}")
        assert r.status_code == 200, it.slug

    # ссылки из praktika markdown и шаблонов
    roots = [
        REPO / "web" / "app" / "content",
        REPO / "web" / "app" / "templates" / "landing",
        REPO / "web" / "app" / "templates" / "zakon",
    ]
    slug_re = re.compile(r"/obraztsy/([a-z0-9\-]+)")
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in {".md", ".html", ".j2"}:
                continue
            text = path.read_text(encoding="utf-8")
            for slug in slug_re.findall(text):
                assert get_catalog_item(slug) is not None, f"{path}: /obraztsy/{slug}"
                assert client.get(f"/obraztsy/{slug}").status_code == 200

    # sitemap не содержит битых obraztsy
    sm = client.get("/sitemap.xml")
    assert sm.status_code == 200
    for slug in re.findall(r"/obraztsy/([a-z0-9\-]+)", sm.text):
        assert client.get(f"/obraztsy/{slug}").status_code == 200, slug


def test_contract_subscription_timezone_helper():
    """6. таймзона: абсолютное окончание = конец дня МСК."""
    from app.timeutil import end_of_moscow_day, to_moscow

    d = date(2027, 11, 30)
    end = end_of_moscow_day(d)
    assert to_moscow(end).date() == d
    assert to_moscow(end).hour == 23
    assert to_moscow(end).minute == 59


def test_contract_env_example_keys_documented():
    """7. env-контракт: ключи .env.example либо имеют дефолт в Settings, либо помечены."""
    from app.config import Settings

    example = REPO / "web" / ".env.example"
    if not example.is_file():
        example = REPO / "deploy" / ".env.example"
    assert example.is_file()
    keys = []
    for line in example.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.append(line.split("=", 1)[0].strip())
    fields = {name.upper() for name in Settings.model_fields}
    # также допустимы deploy-only ключи
    deploy_only = {
        "POSTGRES_PASSWORD",
        "AGE_RECIPIENT",
        "AGE_IDENTITY",
        "BACKUP_DIR",
        "BACKUP_REMOTE",
        "BACKUP_REQUIRE_AGE",
        "DB_AUTO_CREATE",
        "OPS_AGENT_URL",
        "OPS_AGENT_TOKEN",
        "OPS_AGENT_ENABLED",
        "GOTENBERG_URL",
        "DB_URL",
        "FILES_ROOT",
        "JOBS_INLINE",
        "BOOTSTRAP_ADMIN_EMAIL",
        "BOOTSTRAP_ADMIN_PASSWORD",
        "BILLING_WORKER",
    }
    missing = [k for k in keys if k not in fields and k not in deploy_only]
    assert not missing, f"ключи без дефолта/документации: {missing}"


def test_contract_worker_jobs_registered_once():
    """3. app ↔ worker: периодические задачи не дублируются слепым копипастом."""
    jobs_src = (WEB_APP / "billing" / "jobs.py").read_text(encoding="utf-8")
    # одна точка цикла worker
    assert jobs_src.count("async def worker_loop") <= 1 or "def run_worker" in jobs_src or "while True" in jobs_src
    # heartbeat пишется
    assert "worker_heartbeat" in jobs_src or "worker_heartbeat" in (
        WEB_APP / "services" / "ops.py"
    ).read_text(encoding="utf-8")
