"""W-25: вход через Яндекс ID (OAuth 2.0 + PKCE)."""

from __future__ import annotations

from unittest.mock import patch

import pyotp
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    Event,
    OAuthIdentity,
    Organization,
    PaymentSettings,
    Subscription,
    Tariff,
    TariffCode,
    User,
    UserRole,
)
from app.security import hash_password
from app.totp_2fa import enable_totp, generate_backup_codes, generate_totp_secret
from app.yandex_oauth import (
    YandexProfile,
    build_authorize_url,
    can_unlink_yandex,
    new_pkce_pair,
    resolve_login_user,
    yandex_button_visible,
)
from conftest import csrf_from, login


def _set_yandex_env(secret: str = "test-yandex-secret"):
    import os

    os.environ["YANDEX_CLIENT_SECRET"] = secret
    os.environ["YANDEX_CLIENT_ID"] = "453c829f555c4e94a1f77c16313854a9"
    get_settings.cache_clear()


def _seed_password_user(dbmod, email: str = "pwd@example.com", password: str = "UserPass123!"):
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="Орг Yandex", requisites={})
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
        return user.id, email, password
    finally:
        db.close()


def test_button_hidden_without_secret(app):
    client, dbmod = app
    import os

    os.environ["YANDEX_CLIENT_SECRET"] = ""
    get_settings.cache_clear()
    r = client.get("/login")
    assert r.status_code == 200
    assert "Войти с Яндекс ID" not in r.text
    db = dbmod.SessionLocal()
    try:
        assert yandex_button_visible(db) is False
    finally:
        db.close()


def test_button_shown_when_configured(app):
    client, dbmod = app
    _set_yandex_env()
    db = dbmod.SessionLocal()
    try:
        row = db.get(PaymentSettings, 1)
        if row is None:
            row = PaymentSettings(id=1)
            db.add(row)
        row.yandex_login_enabled = True
        db.commit()
    finally:
        db.close()
    r = client.get("/login")
    assert "Войти с Яндекс ID" in r.text
    assert 'class="btn-yandex"' in r.text


def test_provider_toggle_hides_button(app):
    client, dbmod = app
    _set_yandex_env()
    db = dbmod.SessionLocal()
    try:
        row = db.get(PaymentSettings, 1)
        if row is None:
            row = PaymentSettings(id=1)
            db.add(row)
        row.yandex_login_enabled = False
        db.commit()
    finally:
        db.close()
    r = client.get("/login")
    assert "Войти с Яндекс ID" not in r.text


def test_authorize_url_has_state_and_pkce(app):
    _set_yandex_env()
    state, verifier = new_pkce_pair()
    url = build_authorize_url(state=state, code_verifier=verifier)
    assert url.startswith("https://oauth.yandex.ru/authorize?")
    assert f"state={state}" in url or "state=" in url
    assert "code_challenge=" in url
    assert "code_challenge_method=S256" in url
    assert "client_id=453c829f555c4e94a1f77c16313854a9" in url


def test_branch_identity_login(app):
    client, dbmod = app
    _set_yandex_env()
    user_id, email, password = _seed_password_user(dbmod, "ident@example.com")
    db = dbmod.SessionLocal()
    try:
        db.add(
            OAuthIdentity(
                user_id=user_id,
                provider="yandex",
                sub="ya-sub-1",
                provider_email=email,
            )
        )
        db.commit()
    finally:
        db.close()

    profile = YandexProfile(sub="ya-sub-1", email=email, login="ident")
    with patch(
        "app.routers.yandex_auth.build_authorize_url",
        side_effect=lambda **kw: f"https://oauth.yandex.ru/authorize?state={kw['state']}",
    ):
        r = client.get("/auth/yandex/start", follow_redirects=False)
        assert r.status_code == 303
        assert "oauth.yandex.ru" in r.headers["location"]
        state = r.headers["location"].split("state=", 1)[1].split("&", 1)[0]

    with patch(
        "app.routers.yandex_auth.exchange_code_for_profile",
        return_value=profile,
    ) as mocked:
        r = client.get(
            f"/auth/yandex/callback?code=test-code&state={state}",
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/cabinet/"
        mocked.assert_called_once()
        assert mocked.call_args.kwargs["code"] == "test-code"
        assert mocked.call_args.kwargs["code_verifier"]

    db = dbmod.SessionLocal()
    try:
        ev = db.scalars(select(Event).where(Event.type == "login_success")).all()
        assert any((e.details or {}).get("via") == "yandex" for e in ev)
    finally:
        db.close()


def test_branch_email_exists_no_auto_link(app):
    client, dbmod = app
    _set_yandex_env()
    _seed_password_user(dbmod, "exists@example.com")
    profile = YandexProfile(sub="ya-new", email="exists@example.com", login="x")

    with patch(
        "app.routers.yandex_auth.build_authorize_url",
        side_effect=lambda **kw: f"https://oauth.yandex.ru/authorize?state={kw['state']}",
    ):
        r = client.get("/auth/yandex/start", follow_redirects=False)
        state = r.headers["location"].split("state=", 1)[1].split("&", 1)[0]

    with patch("app.routers.yandex_auth.exchange_code_for_profile", return_value=profile):
        r = client.get(
            f"/auth/yandex/callback?code=c&state={state}",
            follow_redirects=False,
        )
    assert r.status_code == 409
    assert "Аккаунт с этим e-mail уже есть" in r.text
    assert "привяжите Яндекс ID в профиле" in r.text

    db = dbmod.SessionLocal()
    try:
        assert db.scalar(select(OAuthIdentity).where(OAuthIdentity.sub == "ya-new")) is None
    finally:
        db.close()


def test_branch_register_guest(app):
    client, dbmod = app
    _set_yandex_env()
    profile = YandexProfile(sub="ya-guest-1", email="newguest@example.com", login="guest1")

    with patch(
        "app.routers.yandex_auth.build_authorize_url",
        side_effect=lambda **kw: f"https://oauth.yandex.ru/authorize?state={kw['state']}",
    ):
        state = client.get("/auth/yandex/start", follow_redirects=False).headers["location"].split(
            "state=", 1
        )[1].split("&", 1)[0]

    with patch("app.routers.yandex_auth.exchange_code_for_profile", return_value=profile):
        r = client.get(
            f"/auth/yandex/callback?code=c&state={state}",
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert r.headers["location"] == "/cabinet/"

    db = dbmod.SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "newguest@example.com"))
        assert user is not None
        assert user.password_hash is None
        ident = db.scalar(select(OAuthIdentity).where(OAuthIdentity.sub == "ya-guest-1"))
        assert ident is not None
        assert ident.user_id == user.id
        sub = db.scalar(select(Subscription).where(Subscription.org_id == user.org_id))
        assert sub is not None
        tariff = db.get(Tariff, sub.tariff_id)
        assert tariff.code == TariffCode.guest
        ev = db.scalars(select(Event).where(Event.type == "oauth_registered")).first()
        assert ev is not None
    finally:
        db.close()


def test_invalid_state_rejected(app):
    client, dbmod = app
    _set_yandex_env()
    with patch(
        "app.routers.yandex_auth.build_authorize_url",
        side_effect=lambda **kw: f"https://oauth.yandex.ru/authorize?state={kw['state']}",
    ):
        client.get("/auth/yandex/start", follow_redirects=False)
    with patch(
        "app.routers.yandex_auth.exchange_code_for_profile",
        return_value=YandexProfile(sub="x", email="x@example.com"),
    ):
        r = client.get(
            "/auth/yandex/callback?code=c&state=wrong-state",
            follow_redirects=False,
        )
    assert r.status_code == 400
    assert "state/PKCE" in r.text


def test_signed_state_works_without_session(app):
    """Callback не зависит от cookie хоста: PKCE verifier внутри signed state."""
    client, dbmod = app
    _set_yandex_env()
    from app.yandex_oauth import issue_oauth_state, new_pkce_pair

    _nonce, verifier = new_pkce_pair()
    state = issue_oauth_state(code_verifier=verifier, intent="login")
    profile = YandexProfile(sub="ya-signed", email="signed@example.com")
    # Новая «сессия» без oauth-ключей: очищаем cookies клиента
    client.cookies.clear()
    with patch(
        "app.routers.yandex_auth.exchange_code_for_profile",
        return_value=profile,
    ) as mocked:
        r = client.get(
            f"/auth/yandex/callback?code=c&state={state}",
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert r.headers["location"] == "/cabinet/"
    assert mocked.call_args.kwargs["code_verifier"] == verifier


def test_unlink_last_method_forbidden(app):
    client, dbmod = app
    _set_yandex_env()
    db = dbmod.SessionLocal()
    try:
        profile = YandexProfile(sub="ya-only", email="only@example.com", login="only")
        user = resolve_login_user(db, profile)[0]
        db.commit()
        assert user is not None
        ok, msg = can_unlink_yandex(db, user)
        assert ok is False
        assert "задайте пароль" in msg.lower() or "пароль" in msg.lower()
        user_id = user.id
        email = user.email
    finally:
        db.close()

    # войти через oauth mock
    with patch(
        "app.routers.yandex_auth.build_authorize_url",
        side_effect=lambda **kw: f"https://oauth.yandex.ru/authorize?state={kw['state']}",
    ):
        state = client.get("/auth/yandex/start", follow_redirects=False).headers["location"].split(
            "state=", 1
        )[1].split("&", 1)[0]
    with patch(
        "app.routers.yandex_auth.exchange_code_for_profile",
        return_value=YandexProfile(sub="ya-only", email=email),
    ):
        assert client.get(
            f"/auth/yandex/callback?code=c&state={state}", follow_redirects=False
        ).status_code == 303

    token = csrf_from(client, "/cabinet/settings/security")
    r = client.post(
        "/cabinet/settings/security/yandex/unlink",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "пароль" in r.text.lower()

    db = dbmod.SessionLocal()
    try:
        assert db.scalar(select(OAuthIdentity).where(OAuthIdentity.user_id == user_id)) is not None
    finally:
        db.close()


def test_2fa_after_yandex_oauth(app):
    client, dbmod = app
    _set_yandex_env()
    user_id, email, password = _seed_password_user(dbmod, "twofa@example.com")
    secret = generate_totp_secret()
    db = dbmod.SessionLocal()
    try:
        user = db.get(User, user_id)
        enable_totp(user, secret=secret, backup_codes=generate_backup_codes())
        db.add(
            OAuthIdentity(
                user_id=user_id, provider="yandex", sub="ya-2fa", provider_email=email
            )
        )
        db.commit()
    finally:
        db.close()

    profile = YandexProfile(sub="ya-2fa", email=email)
    with patch(
        "app.routers.yandex_auth.build_authorize_url",
        side_effect=lambda **kw: f"https://oauth.yandex.ru/authorize?state={kw['state']}",
    ):
        state = client.get("/auth/yandex/start", follow_redirects=False).headers["location"].split(
            "state=", 1
        )[1].split("&", 1)[0]
    with patch("app.routers.yandex_auth.exchange_code_for_profile", return_value=profile):
        r = client.get(
            f"/auth/yandex/callback?code=c&state={state}",
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert r.headers["location"] == "/login/2fa"

    token = csrf_from(client, "/login/2fa")
    code = pyotp.TOTP(secret).now()
    r = client.post(
        "/login/2fa",
        data={"code": code, "csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/cabinet/"


def test_link_requires_password_then_binds(app):
    client, dbmod = app
    _set_yandex_env()
    user_id, email, password = _seed_password_user(dbmod, "linkme@example.com")
    assert login(client, email, password).status_code == 303

    token = csrf_from(client, "/cabinet/settings/security")
    with patch(
        "app.routers.yandex_auth.build_authorize_url",
        side_effect=lambda **kw: f"https://oauth.yandex.ru/authorize?state={kw['state']}",
    ):
        r = client.post(
            "/cabinet/settings/security/yandex/link",
            data={"csrf_token": token, "password": password},
            follow_redirects=False,
        )
        # redirect to start then to yandex
        assert r.status_code == 303
        assert r.headers["location"] == "/auth/yandex/start"
        r = client.get("/auth/yandex/start", follow_redirects=False)
        state = r.headers["location"].split("state=", 1)[1].split("&", 1)[0]

    profile = YandexProfile(sub="ya-link-1", email=email)
    with patch("app.routers.yandex_auth.exchange_code_for_profile", return_value=profile):
        r = client.get(
            f"/auth/yandex/callback?code=c&state={state}",
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert "/cabinet/settings/security" in r.headers["location"]

    db = dbmod.SessionLocal()
    try:
        ident = db.scalar(select(OAuthIdentity).where(OAuthIdentity.user_id == user_id))
        assert ident is not None
        assert ident.sub == "ya-link-1"
        ev = db.scalars(select(Event).where(Event.type == "oauth_linked")).first()
        assert ev is not None
    finally:
        db.close()
