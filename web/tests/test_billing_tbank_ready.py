"""Кабинет видит настройки Т-Кассы; оплата в тестовом режиме; Receipt-коды."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.billing.crypto import encrypt_secret
from app.billing.settings_access import normalize_taxation, normalize_vat, terminal_status
from app.defaults import empty_requisites
from app.models import (
    Organization,
    PaymentMode,
    PaymentSettings,
    SubscriptionPeriod,
    TariffCode,
    User,
    UserRole,
)
from app.security import hash_password
from app.services.billing import ensure_beta_subscriptions, ensure_payment_settings, ensure_tariffs
from conftest import csrf_from, login


def _org_user(dbmod, email: str = "payready@example.com"):
    db = dbmod.SessionLocal()
    try:
        ensure_tariffs(db)
        ensure_payment_settings(db)
        org = Organization(name="ООО Оплата Ready", requisites=empty_requisites())
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id,
            email=email,
            password_hash=hash_password("Passw0rd!"),
            role=UserRole.user,
            is_active=True,
        )
        db.add(user)
        ensure_beta_subscriptions(db)
        db.commit()
        return org.id, email
    finally:
        db.close()


def _configure_test_terminal(dbmod, *, taxation="usn_income", vat="none", mode=PaymentMode.test):
    db = dbmod.SessionLocal()
    try:
        row = ensure_payment_settings(db)
        row.terminal_key = "TestTerminalKeyDemo"
        row.password_encrypted = encrypt_secret("terminal-password")
        row.mode = mode
        row.taxation = taxation
        row.vat_rate = vat
        db.commit()
    finally:
        db.close()


def test_normalize_human_receipt_labels():
    assert normalize_taxation("УСН 6%") == "usn_income"
    assert normalize_vat("Без НДС") == "none"
    assert normalize_taxation("usn_income") == "usn_income"


def test_billing_page_test_terminal_shows_form_and_banner(app):
    client, dbmod = app
    _org_user(dbmod)
    _configure_test_terminal(dbmod, taxation="УСН 6%", vat="Без НДС")
    assert login(client, "payready@example.com", "Passw0rd!").status_code == 303
    r = client.get("/cabinet/billing/")
    assert r.status_code == 200
    assert "Приём карт ещё не настроен" not in r.text
    assert 'id="pay-form"' in r.text
    assert "Тестовый режим: реальные деньги не списываются" in r.text
    assert 'action="/cabinet/billing/pay"' in r.text
    assert 'type="submit" disabled' not in r.text
    assert ">Перейти к оплате</button>" in r.text


def test_billing_pay_mocked_returns_payment_url(app):
    client, dbmod = app
    _org_user(dbmod, "payurl@example.com")
    _configure_test_terminal(dbmod, taxation="УСН 6%", vat="Без НДС")
    assert login(client, "payurl@example.com", "Passw0rd!").status_code == 303
    token = csrf_from(client, "/cabinet/billing/")

    fake = MagicMock()
    fake.init.return_value = {
        "Success": True,
        "PaymentId": "888",
        "PaymentURL": "https://securepay.tinkoff.ru/html/payForm/1.0/?PaymentId=888",
        "Status": "NEW",
        "ErrorCode": "0",
    }

    with patch("app.billing.payments.load_tbank_client", return_value=fake):
        r = client.post(
            "/cabinet/billing/pay",
            data={
                "csrf_token": token,
                "tariff_code": TariffCode.specialist.value,
                "period": SubscriptionPeriod.month.value,
                "receipt_email": "payurl@example.com",
            },
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert "PaymentId=888" in r.headers["location"]
    fake.init.assert_called_once()
    receipt = fake.init.call_args.kwargs["receipt"]
    assert receipt["Taxation"] == "usn_income"
    assert receipt["Items"][0]["Tax"] == "none"


def test_admin_settings_change_visible_without_restart(app):
    client, dbmod = app
    _org_user(dbmod, "seechange@example.com")
    # сначала пусто
    assert login(client, "seechange@example.com", "Passw0rd!").status_code == 303
    page = client.get("/cabinet/billing/")
    assert "Приём карт ещё не настроен" in page.text

    # админ сохраняет терминал — кабинет видит без рестарта процесса
    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    token = csrf_from(client, "/admin/payment-settings")
    save = client.post(
        "/admin/payment-settings",
        data={
            "csrf_token": token,
            "terminal_key": "FreshTerminal",
            "password": "fresh-pass-123",
            "mode": "test",
            "taxation": "УСН 6%",
            "vat_rate": "Без НДС",
            "default_receipt_email": "buh@example.com",
        },
        follow_redirects=False,
    )
    assert save.status_code == 303

    db = dbmod.SessionLocal()
    try:
        row = db.get(PaymentSettings, 1)
        assert row.terminal_key == "FreshTerminal"
        # сохранение нормализует коды
        assert row.taxation == "usn_income"
        assert row.vat_rate == "none"
        st = terminal_status(db)
        assert st.ready is True
        assert st.test_mode is True
    finally:
        db.close()

    assert login(client, "seechange@example.com", "Passw0rd!").status_code == 303
    page2 = client.get("/cabinet/billing/")
    assert "Приём карт ещё не настроен" not in page2.text
    assert "Тестовый режим: реальные деньги не списываются" in page2.text


def test_pay_error_keeps_terminal_ready_and_shows_details(app):
    client, dbmod = app
    _org_user(dbmod, "errdetail@example.com")
    _configure_test_terminal(dbmod)
    assert login(client, "errdetail@example.com", "Passw0rd!").status_code == 303
    token = csrf_from(client, "/cabinet/billing/")

    from app.billing.tbank import TBankError

    fake = MagicMock()
    fake.init.side_effect = TBankError(
        "Неверные параметры. — Поле taxation не должно быть пустым. (код 9999)",
        code="9999",
        payload={"Details": "Поле taxation не должно быть пустым.", "Message": "Неверные параметры."},
    )

    with patch("app.billing.payments.load_tbank_client", return_value=fake):
        r = client.post(
            "/cabinet/billing/pay",
            data={
                "csrf_token": token,
                "tariff_code": "specialist",
                "period": "month",
                "receipt_email": "errdetail@example.com",
            },
            follow_redirects=False,
        )
    assert r.status_code == 400
    assert "taxation" in r.text.lower() or "Неверные параметры" in r.text
    # после ошибки форма оплаты не должна притворяться «не настроена»
    assert "Приём карт ещё не настроен" not in r.text
    assert "Тестовый режим: реальные деньги не списываются" in r.text
