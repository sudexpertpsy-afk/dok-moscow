"""W-13: админка платёжной системы и учёт платежей."""

from __future__ import annotations

from sqlalchemy import select

from app.billing.crypto import decrypt_secret
from app.defaults import empty_requisites
from app.models import Organization, Payment, PaymentSettings, PaymentStatus, utcnow
from app.services.billing import ensure_beta_subscriptions, ensure_tariffs
from conftest import csrf_from, login


def test_payment_settings_save_encrypts_password(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    token = csrf_from(client, "/admin/payment-settings")
    r = client.post(
        "/admin/payment-settings",
        data={
            "csrf_token": token,
            "terminal_key": "DemoTerminal",
            "password": "super-secret-pass",
            "mode": "test",
            "taxation": "usn_income",
            "vat_rate": "none",
            "default_receipt_email": "buh@use.moscow",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    db = dbmod.SessionLocal()
    try:
        row = db.get(PaymentSettings, 1)
        assert row.terminal_key == "DemoTerminal"
        assert row.password_encrypted
        assert decrypt_secret(row.password_encrypted) == "super-secret-pass"
        assert "super-secret-pass" not in (row.password_encrypted or "")
    finally:
        db.close()
    page = client.get("/admin/payment-settings")
    assert page.status_code == 200
    assert "•••• задан" in page.text
    assert "super-secret-pass" not in page.text


def test_payments_summary_and_manual_extend(app):
    client, dbmod = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        org = Organization(name="Орг Оплата", requisites=empty_requisites())
        db.add(org)
        db.flush()
        ensure_beta_subscriptions(db)
        db.commit()
        oid = org.id
    finally:
        db.close()

    r = client.get("/admin/payments")
    assert r.status_code == 200
    assert "MRR" in r.text

    token = csrf_from(client, "/admin/payments")
    r = client.post(
        "/admin/subscriptions/manual-extend",
        data={
            "csrf_token": token,
            "org_id": oid,
            "tariff_code": "specialist",
            "period": "month",
            "basis": "пп 1 от 01.08.2026",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/admin/payments/" in r.headers["location"]

    # идемпотентность по основанию
    token = csrf_from(client, "/admin/payments")
    r2 = client.post(
        "/admin/subscriptions/manual-extend",
        data={
            "csrf_token": token,
            "org_id": oid,
            "tariff_code": "specialist",
            "period": "month",
            "basis": "пп 1 от 01.08.2026",
        },
        follow_redirects=False,
    )
    assert r2.headers["location"] == r.headers["location"]

    db = dbmod.SessionLocal()
    try:
        pays = db.scalars(select(Payment).where(Payment.org_id == oid)).all()
        assert len(pays) == 1
        assert pays[0].status == PaymentStatus.confirmed
        assert pays[0].amount_kop == 99_000
    finally:
        db.close()

    page = client.get("/admin/payments")
    assert "990" in page.text


def test_payments_export_xlsx(app):
    client, _ = app
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    r = client.get("/admin/payments/export")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers.get("content-type", "")
