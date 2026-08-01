"""W-24: двухфакторная аутентификация (TOTP)."""

from __future__ import annotations

import pyotp
from sqlalchemy import select

from app.billing.crypto import decrypt_secret
from app.models import Event, Organization, PaymentSettings, User, UserRole
from app.routers import auth as auth_router
from app.security import hash_password
from app.totp_2fa import (
    DEVICE_COOKIE,
    enable_totp,
    generate_backup_codes,
    generate_totp_secret,
    hash_backup_code,
    issue_device_token,
    provisioning_uri,
    validate_device_token,
    verify_totp_code,
)
from conftest import csrf_from, login


def _seed_org_user(dbmod, email: str = "2fa@example.com", password: str = "UserPass123!"):
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="Орг 2FA", requisites={})
        db.add(org)
        db.flush()
        user = User(
            org_id=org.id,
            email=email,
            password_hash=hash_password(password),
            role=UserRole.user,
            is_active=True,
        )
        db.add(user)
        db.commit()
        return org.id, user.id, email, password
    finally:
        db.close()


def _enable_via_ui(client, password: str):
    token = csrf_from(client, "/cabinet/settings/security")
    r = client.post(
        "/cabinet/settings/security/2fa/start",
        data={"password": password, "csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "Отсканируйте в приложении Яндекс Ключ" in r.text
    assert "totp_setup" in r.text or "otpauth" in r.text or "Секрет для ручного ввода" in r.text

    # секрет из HTML
    marker = 'class="mono" style="word-break:break-all">'
    assert marker in r.text
    secret = r.text.split(marker, 1)[1].split("<", 1)[0].strip()
    code = pyotp.TOTP(secret).now()
    token = csrf_from(client, "/cabinet/settings/security")
    r = client.post(
        "/cabinet/settings/security/2fa/confirm",
        data={"code": code, "csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "включена" in r.text.lower() or "Резервные коды" in r.text
    assert "показываются один раз" in r.text
    # вытащим коды
    codes = []
    for line in r.text.split("<li>"):
        if "</li>" in line and "-" in line[:12]:
            codes.append(line.split("</li>", 1)[0].strip())
    assert len(codes) == 10
    return secret, codes


def test_full_enable_cycle_and_login(app):
    client, dbmod = app
    _org_id, user_id, email, password = _seed_org_user(dbmod)
    assert login(client, email, password).status_code == 303

    secret, codes = _enable_via_ui(client, password)

    db = dbmod.SessionLocal()
    try:
        user = db.get(User, user_id)
        assert user.totp_enabled is True
        assert user.totp_secret_encrypted
        plain = decrypt_secret(user.totp_secret_encrypted)
        assert plain == secret
        assert plain != user.totp_secret_encrypted
        assert len(user.backup_codes_hashes) == 10
        assert all(len(h) == 64 for h in user.backup_codes_hashes)
        assert hash_backup_code(codes[0]) in user.backup_codes_hashes
        ev = db.scalars(select(Event).where(Event.type == "totp_enabled")).first()
        assert ev is not None
        from urllib.parse import unquote

        uri = provisioning_uri(email=email, secret=secret)
        assert uri.startswith("otpauth://totp/")
        decoded = unquote(uri)
        assert "Док.Москва" in decoded
        assert email in decoded
        assert "issuer=Док.Москва" in decoded or "issuer=%D0%94%D0%BE%D0%BA" in uri
    finally:
        db.close()

    token = csrf_from(client, "/cabinet/")
    client.post("/logout", data={"csrf_token": token}, follow_redirects=False)

    r = login(client, email, password)
    assert r.status_code == 303
    assert r.headers["location"] == "/login/2fa"

    # неверный код
    token = csrf_from(client, "/login/2fa")
    r = client.post(
        "/login/2fa",
        data={"code": "000000", "csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 401

    code = pyotp.TOTP(secret).now()
    token = csrf_from(client, "/login/2fa")
    r = client.post(
        "/login/2fa",
        data={"code": code, "csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cabinet/"


def test_valid_window_and_backup_login(app):
    client, dbmod = app
    _org_id, user_id, email, password = _seed_org_user(dbmod, "backup@example.com")
    secret = generate_totp_secret()
    codes = generate_backup_codes()
    db = dbmod.SessionLocal()
    try:
        user = db.get(User, user_id)
        enable_totp(user, secret=secret, backup_codes=codes)
        db.commit()
    finally:
        db.close()

    import time

    totp = pyotp.TOTP(secret)
    assert verify_totp_code(secret, totp.now(), valid_window=1)
    # соседнее окно (±30 с) при valid_window=1
    prev = totp.at(int(time.time()) - 30)
    assert verify_totp_code(secret, prev, valid_window=1)
    assert not verify_totp_code(secret, prev, valid_window=0)

    r = login(client, email, password)
    assert r.headers["location"] == "/login/2fa"
    token = csrf_from(client, "/login/2fa")
    r = client.post(
        "/login/2fa",
        data={"code": codes[0], "csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cabinet/"

    db = dbmod.SessionLocal()
    try:
        user = db.get(User, user_id)
        assert any(h.startswith("used:") for h in user.backup_codes_hashes)
        ev = db.scalars(select(Event).where(Event.type == "totp_backup_login")).first()
        assert ev is not None
    finally:
        db.close()

    # повторное использование того же кода — отказ
    token = csrf_from(client, "/cabinet/")
    client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    login(client, email, password)
    token = csrf_from(client, "/login/2fa")
    r = client.post(
        "/login/2fa",
        data={"code": codes[0], "csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 401


def test_totp_rate_limit(app):
    client, dbmod = app
    _org_id, user_id, email, password = _seed_org_user(dbmod, "rl@example.com")
    secret = generate_totp_secret()
    db = dbmod.SessionLocal()
    try:
        user = db.get(User, user_id)
        enable_totp(user, secret=secret, backup_codes=generate_backup_codes())
        db.commit()
    finally:
        db.close()

    login(client, email, password)
    for _ in range(5):
        token = csrf_from(client, "/login/2fa")
        r = client.post(
            "/login/2fa",
            data={"code": "000000", "csrf_token": token},
            follow_redirects=False,
        )
        assert r.status_code == 401
    token = csrf_from(client, "/login/2fa")
    r = client.post(
        "/login/2fa",
        data={"code": "000000", "csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 429
    auth_router.totp_limiter.clear()


def test_remember_device_and_password_change_invalidates(app):
    client, dbmod = app
    _org_id, user_id, email, password = _seed_org_user(dbmod, "device@example.com")
    secret = generate_totp_secret()
    db = dbmod.SessionLocal()
    try:
        user = db.get(User, user_id)
        enable_totp(user, secret=secret, backup_codes=generate_backup_codes())
        db.commit()
        token_ok = issue_device_token(user)
        assert validate_device_token(token_ok, user)
        user.password_hash = hash_password("NewPass123!")
        db.commit()
        db.refresh(user)
        assert not validate_device_token(token_ok, user)
    finally:
        db.close()

    # вход с remember
    db = dbmod.SessionLocal()
    try:
        user = db.get(User, user_id)
        user.password_hash = hash_password(password)
        db.commit()
    finally:
        db.close()

    login(client, email, password)
    code = pyotp.TOTP(secret).now()
    token = csrf_from(client, "/login/2fa")
    r = client.post(
        "/login/2fa",
        data={"code": code, "csrf_token": token, "remember_device": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert DEVICE_COOKIE in r.cookies or DEVICE_COOKIE in client.cookies

    token = csrf_from(client, "/cabinet/")
    client.post("/logout", data={"csrf_token": token}, follow_redirects=False)

    r = login(client, email, password)
    assert r.status_code == 303
    assert r.headers["location"] == "/cabinet/"


def test_admin_reset_2fa(app):
    client, dbmod = app
    _org_id, user_id, email, password = _seed_org_user(dbmod, "resetme@example.com")
    secret = generate_totp_secret()
    db = dbmod.SessionLocal()
    try:
        user = db.get(User, user_id)
        enable_totp(user, secret=secret, backup_codes=generate_backup_codes())
        db.commit()
    finally:
        db.close()

    assert login(client, "admin@dok.moscow", "AdminPass123!").status_code == 303
    r = client.get(f"/admin/users/{user_id}")
    assert r.status_code == 200
    assert "Сбросить 2FA" in r.text
    token = csrf_from(client, f"/admin/users/{user_id}")
    r = client.post(
        f"/admin/users/{user_id}/reset-2fa",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 303

    db = dbmod.SessionLocal()
    try:
        user = db.get(User, user_id)
        assert user.totp_enabled is False
        assert user.totp_secret_encrypted is None
        ev = db.scalars(select(Event).where(Event.type == "totp_admin_reset")).first()
        assert ev is not None
    finally:
        db.close()

    token = csrf_from(client, "/admin/")
    client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    r = login(client, email, password)
    assert r.status_code == 303
    assert r.headers["location"] == "/cabinet/"


def test_require_2fa_policy_forces_setup(app):
    client, dbmod = app
    _org_id, _user_id, email, password = _seed_org_user(dbmod, "policy@example.com")
    db = dbmod.SessionLocal()
    try:
        row = db.get(PaymentSettings, 1)
        if row is None:
            row = PaymentSettings(id=1)
            db.add(row)
        row.require_2fa_for_org_admins = True
        db.commit()
    finally:
        db.close()

    r = login(client, email, password)
    assert r.status_code == 303
    assert r.headers["location"] == "/cabinet/settings/security"

    r = client.get("/cabinet/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/cabinet/settings/security"
    r = client.get("/cabinet/settings/security")
    assert r.status_code == 200
    assert "требует включить 2FA" in r.text


def test_disable_2fa(app):
    client, dbmod = app
    _org_id, user_id, email, password = _seed_org_user(dbmod, "off@example.com")
    secret = generate_totp_secret()
    db = dbmod.SessionLocal()
    try:
        user = db.get(User, user_id)
        enable_totp(user, secret=secret, backup_codes=generate_backup_codes())
        db.commit()
    finally:
        db.close()

    login(client, email, password)
    code = pyotp.TOTP(secret).now()
    token = csrf_from(client, "/login/2fa")
    client.post(
        "/login/2fa",
        data={"code": code, "csrf_token": token},
        follow_redirects=False,
    )

    token = csrf_from(client, "/cabinet/settings/security")
    code = pyotp.TOTP(secret).now()
    r = client.post(
        "/cabinet/settings/security/2fa/disable",
        data={"password": password, "code": code, "csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "отключена" in r.text.lower()

    db = dbmod.SessionLocal()
    try:
        user = db.get(User, user_id)
        assert user.totp_enabled is False
        ev = db.scalars(select(Event).where(Event.type == "totp_disabled")).first()
        assert ev is not None
    finally:
        db.close()
