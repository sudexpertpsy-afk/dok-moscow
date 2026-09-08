"""Яндекс ID OAuth 2.0 (Authorization Code + state + PKCE), W-25.

Эндпойнты по документации Яндекс ID:
  authorize — https://oauth.yandex.ru/authorize
  token     — https://oauth.yandex.ru/token
  info      — https://login.yandex.ru/info
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from authlib.common.security import generate_token
from authlib.integrations.requests_client import OAuth2Session
from itsdangerous import BadSignature, BadTimeSignature, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.defaults import empty_requisites
from app.models import (
    OAuthIdentity,
    Organization,
    PaymentSettings,
    Subscription,
    SubscriptionPeriod,
    SubscriptionStatus,
    Tariff,
    TariffCode,
    OrgRole,
    User,
    UserRole,
    utcnow,
)
from app.services.billing import ensure_tariffs

log = logging.getLogger("dok.yandex_oauth")

PROVIDER = "yandex"
AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"
TOKEN_URL = "https://oauth.yandex.ru/token"
INFO_URL = "https://login.yandex.ru/info"
# login:info — профиль; login:email — default_email
DEFAULT_SCOPE = "login:info login:email"
OAUTH_STATE_SALT = "dok-yandex-oauth-state"
OAUTH_STATE_MAX_AGE = 10 * 60  # 10 минут — срок кода Яндекса


@dataclass(frozen=True)
class YandexProfile:
    sub: str
    email: str
    login: str | None = None


@dataclass(frozen=True)
class OAuthStatePayload:
    verifier: str
    intent: str = "login"
    link_user_id: int | None = None
    tariff: str | None = None
    period: str | None = None


def yandex_credentials_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool((settings.yandex_client_id or "").strip() and (settings.yandex_client_secret or "").strip())


def yandex_login_enabled_in_db(db: Session) -> bool:
    row = db.get(PaymentSettings, 1)
    if row is None:
        return True
    return bool(row.yandex_login_enabled)


def yandex_button_visible(db: Session, settings: Settings | None = None) -> bool:
    """Кнопка на /login: секрет задан и админ не выключил провайдер."""
    if not yandex_credentials_configured(settings):
        return False
    return yandex_login_enabled_in_db(db)


def redirect_uri(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    configured = (settings.yandex_redirect_uri or "").strip()
    if configured:
        return configured
    # По умолчанию — хост кабинета (тот же, что /login), иначе ломается session cookie.
    base = (settings.app_base_url or "https://app.dok.moscow").rstrip("/")
    return f"{base}/auth/yandex/callback"


def _state_serializer(settings: Settings | None = None) -> URLSafeTimedSerializer:
    settings = settings or get_settings()
    return URLSafeTimedSerializer(settings.secret_key, salt=OAUTH_STATE_SALT)


def issue_oauth_state(
    *,
    code_verifier: str,
    intent: str = "login",
    link_user_id: int | None = None,
    tariff: str | None = None,
    period: str | None = None,
    settings: Settings | None = None,
) -> str:
    """Подписанный state: PKCE verifier + intent (не зависит от cookie хоста)."""
    payload = {
        "v": code_verifier,
        "i": intent,
        "u": link_user_id,
        "t": tariff,
        "p": period,
        "n": generate_token(8),
    }
    return _state_serializer(settings).dumps(payload)


def load_oauth_state(state: str, settings: Settings | None = None) -> OAuthStatePayload | None:
    try:
        data = _state_serializer(settings).loads(state, max_age=OAUTH_STATE_MAX_AGE)
    except (BadSignature, BadTimeSignature):
        return None
    if not isinstance(data, dict):
        return None
    verifier = str(data.get("v") or "")
    if not verifier:
        return None
    uid = data.get("u")
    tariff = data.get("t")
    period = data.get("p")
    return OAuthStatePayload(
        verifier=verifier,
        intent=str(data.get("i") or "login"),
        link_user_id=int(uid) if uid is not None else None,
        tariff=str(tariff) if tariff else None,
        period=str(period) if period else None,
    )


def build_authorize_url(
    *,
    state: str,
    code_verifier: str,
    settings: Settings | None = None,
    force_confirm: bool = False,
) -> str:
    settings = settings or get_settings()
    client = OAuth2Session(
        settings.yandex_client_id,
        settings.yandex_client_secret,
        scope=DEFAULT_SCOPE,
        redirect_uri=redirect_uri(settings),
        code_challenge_method="S256",
    )
    kwargs: dict[str, Any] = {"state": state, "code_verifier": code_verifier}
    if force_confirm:
        kwargs["force_confirm"] = "yes"
    url, _ = client.create_authorization_url(AUTHORIZE_URL, **kwargs)
    return url


def new_pkce_pair() -> tuple[str, str]:
    """Вернуть (nonce, code_verifier). State формируйте через issue_oauth_state."""
    return generate_token(16), generate_token(64)


def exchange_code_for_profile(
    *,
    code: str,
    code_verifier: str,
    settings: Settings | None = None,
) -> YandexProfile:
    settings = settings or get_settings()
    client = OAuth2Session(
        settings.yandex_client_id,
        settings.yandex_client_secret,
        scope=DEFAULT_SCOPE,
        redirect_uri=redirect_uri(settings),
    )
    token = client.fetch_token(
        TOKEN_URL,
        code=code,
        code_verifier=code_verifier,
        client_id=settings.yandex_client_id,
        client_secret=settings.yandex_client_secret,
        grant_type="authorization_code",
    )
    access = token.get("access_token")
    if not access:
        raise ValueError("Яндекс не вернул access_token")
    resp = client.get(INFO_URL, params={"format": "json"})
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    sub = str(data.get("id") or "").strip()
    email = (data.get("default_email") or "").strip().lower()
    if not email:
        emails = data.get("emails") or []
        if isinstance(emails, list) and emails:
            email = str(emails[0]).strip().lower()
    login = (data.get("login") or "").strip() or None
    if not sub:
        raise ValueError("Яндекс не вернул id пользователя")
    if not email:
        raise ValueError("Яндекс не вернул e-mail (нужно право login:email)")
    return YandexProfile(sub=sub, email=email, login=login)


def find_identity(db: Session, *, sub: str) -> OAuthIdentity | None:
    return db.scalar(
        select(OAuthIdentity).where(
            OAuthIdentity.provider == PROVIDER,
            OAuthIdentity.sub == sub,
        )
    )


def user_has_password(user: User) -> bool:
    return bool(user.password_hash)


def user_yandex_identity(db: Session, user_id: int) -> OAuthIdentity | None:
    return db.scalar(
        select(OAuthIdentity).where(
            OAuthIdentity.user_id == user_id,
            OAuthIdentity.provider == PROVIDER,
        )
    )


def count_oauth_identities(db: Session, user_id: int) -> int:
    return len(
        db.scalars(select(OAuthIdentity).where(OAuthIdentity.user_id == user_id)).all()
    )


def can_unlink_yandex(db: Session, user: User) -> tuple[bool, str]:
    """Нельзя отвязать последний способ входа."""
    ident = user_yandex_identity(db, user.id)
    if ident is None:
        return False, "Яндекс ID не привязан."
    if not user_has_password(user) and count_oauth_identities(db, user.id) <= 1:
        return False, "Нельзя отвязать единственный способ входа. Сначала задайте пароль."
    return True, ""


def link_identity(db: Session, user: User, profile: YandexProfile) -> OAuthIdentity:
    existing = find_identity(db, sub=profile.sub)
    if existing and existing.user_id != user.id:
        raise ValueError("Этот Яндекс ID уже привязан к другому аккаунту.")
    mine = user_yandex_identity(db, user.id)
    if mine:
        mine.sub = profile.sub
        mine.provider_email = profile.email
        mine.linked_at = utcnow()
        return mine
    if existing:
        return existing
    row = OAuthIdentity(
        user_id=user.id,
        provider=PROVIDER,
        sub=profile.sub,
        provider_email=profile.email,
        linked_at=utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def unlink_yandex(db: Session, user: User) -> None:
    ok, err = can_unlink_yandex(db, user)
    if not ok:
        raise ValueError(err)
    ident = user_yandex_identity(db, user.id)
    if ident:
        db.delete(ident)


def register_guest_user(db: Session, profile: YandexProfile) -> User:
    """Новый пользователь + организация на тарифе «Гость»."""
    ensure_tariffs(db)
    guest = db.scalar(select(Tariff).where(Tariff.code == TariffCode.guest))
    assert guest is not None
    org = Organization(
        name=f"Кабинет {profile.email}",
        requisites=empty_requisites(),
    )
    db.add(org)
    db.flush()
    user = User(
        org_id=org.id,
        email=profile.email,
        password_hash=None,
        role=UserRole.user,
        org_role=OrgRole.org_admin,
        is_active=True,
        email_verified=True,
        last_login_at=utcnow(),
    )
    db.add(user)
    db.flush()
    db.add(
        Subscription(
            org_id=org.id,
            tariff_id=guest.id,
            period=SubscriptionPeriod.month,
            starts_at=utcnow(),
            ends_at=utcnow() + timedelta(days=365 * 100),
            status=SubscriptionStatus.active,
            auto_renew=False,
            is_beta=False,
        )
    )
    link_identity(db, user, profile)
    db.flush()
    return user


def resolve_login_user(db: Session, profile: YandexProfile) -> tuple[User | None, str]:
    """
    Сопоставление аккаунтов (п.3 ТЗ).
    Возвращает (user, outcome) где outcome:
      identity | email_exists | registered | error
    """
    ident = find_identity(db, sub=profile.sub)
    if ident is not None:
        user = db.get(User, ident.user_id)
        if user is None or not user.is_active:
            return None, "error"
        if ident.provider_email != profile.email:
            ident.provider_email = profile.email
        return user, "identity"

    existing = db.scalar(select(User).where(User.email == profile.email))
    if existing is not None:
        return None, "email_exists"

    user = register_guest_user(db, profile)
    return user, "registered"
