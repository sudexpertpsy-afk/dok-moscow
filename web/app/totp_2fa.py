"""Двухфакторная аутентификация TOTP (W-24)."""

from __future__ import annotations

import base64
import hashlib
import io
import secrets
from typing import Any

import pyotp
import qrcode
from itsdangerous import BadSignature, BadTimeSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.billing.crypto import decrypt_secret, encrypt_secret
from app.config import Settings, get_settings
from app.models import PaymentSettings, User

ISSUER = "Док.Москва"
TOTP_VALID_WINDOW = 1
BACKUP_CODES_COUNT = 10
DEVICE_COOKIE = "dok_2fa_device"
DEVICE_MAX_AGE = 30 * 24 * 60 * 60  # 30 дней
DEVICE_SALT = "dok-2fa-device"


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def encrypt_totp_secret(plain: str) -> str:
    return encrypt_secret(plain)


def decrypt_totp_secret(token: str) -> str:
    return decrypt_secret(token)


def provisioning_uri(*, email: str, secret: str) -> str:
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name=ISSUER)


def qr_data_url(uri: str) -> str:
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def verify_totp_code(secret: str, code: str, *, valid_window: int = TOTP_VALID_WINDOW) -> bool:
    cleaned = (code or "").strip().replace(" ", "")
    if not cleaned.isdigit():
        return False
    return bool(pyotp.TOTP(secret).verify(cleaned, valid_window=valid_window))


def generate_backup_codes(count: int = BACKUP_CODES_COUNT) -> list[str]:
    codes: list[str] = []
    for _ in range(count):
        raw = secrets.token_hex(4).upper()
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes


def hash_backup_code(code: str) -> str:
    normalized = (code or "").strip().upper().replace(" ", "")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def verify_backup_code(user: User, code: str) -> bool:
    """Проверить резервный код и пометить использованным (мутирует user)."""
    hashes = list(user.backup_codes_hashes or [])
    target = hash_backup_code(code)
    try:
        idx = hashes.index(target)
    except ValueError:
        return False
    hashes[idx] = f"used:{target}"
    user.backup_codes_hashes = hashes
    return True


def is_backup_code_unused(hashes: list | None, code: str) -> bool:
    if not hashes:
        return False
    target = hash_backup_code(code)
    return target in hashes


def clear_totp(user: User) -> None:
    user.totp_secret_encrypted = None
    user.totp_enabled = False
    user.backup_codes_hashes = None


def enable_totp(user: User, *, secret: str, backup_codes: list[str]) -> None:
    user.totp_secret_encrypted = encrypt_totp_secret(secret)
    user.totp_enabled = True
    user.backup_codes_hashes = [hash_backup_code(c) for c in backup_codes]


def user_totp_secret(user: User) -> str | None:
    if not user.totp_secret_encrypted:
        return None
    return decrypt_totp_secret(user.totp_secret_encrypted)


def verify_user_totp_or_backup(user: User, code: str) -> str | None:
    """
    Вернуть 'totp' | 'backup' при успехе, иначе None.
    Резервный код при успехе помечается использованным.
    """
    secret = user_totp_secret(user)
    if secret and verify_totp_code(secret, code):
        return "totp"
    if verify_backup_code(user, code):
        return "backup"
    return None


def _device_serializer(settings: Settings | None = None) -> URLSafeTimedSerializer:
    settings = settings or get_settings()
    return URLSafeTimedSerializer(settings.secret_key, salt=DEVICE_SALT)


def _password_fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:32]


def issue_device_token(user: User, settings: Settings | None = None) -> str:
    return _device_serializer(settings).dumps(
        {"uid": user.id, "fp": _password_fingerprint(user.password_hash)}
    )


def validate_device_token(token: str | None, user: User, settings: Settings | None = None) -> bool:
    if not token:
        return False
    settings = settings or get_settings()
    try:
        data = _device_serializer(settings).loads(token, max_age=DEVICE_MAX_AGE)
    except (BadSignature, BadTimeSignature):
        return False
    if not isinstance(data, dict):
        return False
    if int(data.get("uid") or 0) != user.id:
        return False
    return data.get("fp") == _password_fingerprint(user.password_hash)


def set_device_cookie(response: Response, user: User, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    cookie_kw: dict = {
        "key": DEVICE_COOKIE,
        "value": issue_device_token(user, settings),
        "max_age": DEVICE_MAX_AGE,
        "httponly": True,
        "samesite": "lax",
        "secure": bool(settings.session_https_only),
        "path": "/",
    }
    domain = (settings.session_cookie_domain or "").strip()
    if domain:
        cookie_kw["domain"] = domain
    response.set_cookie(**cookie_kw)


def clear_device_cookie(response: Response, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    cookie_kw: dict = {"key": DEVICE_COOKIE, "path": "/"}
    domain = (settings.session_cookie_domain or "").strip()
    if domain:
        cookie_kw["domain"] = domain
    response.delete_cookie(**cookie_kw)


def require_2fa_for_org_admins(db: Session) -> bool:
    row = db.get(PaymentSettings, 1)
    return bool(row and row.require_2fa_for_org_admins)


def is_org_admin_subject(user: User) -> bool:
    """Администратор организации (W-27: users.org_role)."""
    from app.org_roles import is_org_admin

    role = user.role.value if hasattr(user.role, "value") else str(user.role)
    return bool(user.org_id is not None and role == "user" and is_org_admin(user))


def should_force_2fa_setup(db: Session, user: User) -> bool:
    """Жёсткий мастер только по флагу require_2fa_for_org_admins (не soft-политика)."""
    if user.totp_enabled:
        return False
    if not is_org_admin_subject(user):
        return False
    return require_2fa_for_org_admins(db)


def backup_codes_txt(codes: list[str], *, email: str) -> str:
    lines = [
        f"Резервные коды 2FA — {ISSUER}",
        f"Учётная запись: {email}",
        "",
        "Каждый код можно использовать один раз. Храните в безопасном месте.",
        "Показываются один раз — после закрытия страницы восстановить нельзя.",
        "",
        *codes,
        "",
    ]
    return "\n".join(lines)


def notify_totp_change(*, settings: Settings, email: str, enabled: bool, by_admin: bool = False) -> None:
    from app.services.mail import send_email

    if enabled:
        subject = f"[{ISSUER}] Двухфакторная аутентификация включена"
        body = (
            f"Здравствуйте.\n\n"
            f"Для учётной записи {email} включена двухфакторная аутентификация (TOTP).\n"
            f"При входе потребуется код из приложения-аутентификатора.\n"
        )
    elif by_admin:
        subject = f"[{ISSUER}] Двухфакторная аутентификация сброшена администратором"
        body = (
            f"Здравствуйте.\n\n"
            f"Администратор сервиса сбросил 2FA для учётной записи {email}.\n"
            f"При необходимости включите защиту заново в разделе «Профиль → Безопасность».\n"
        )
    else:
        subject = f"[{ISSUER}] Двухфакторная аутентификация отключена"
        body = (
            f"Здравствуйте.\n\n"
            f"Для учётной записи {email} отключена двухфакторная аутентификация.\n"
            f"Если это были не вы — срочно смените пароль и свяжитесь с поддержкой.\n"
        )
    send_email(settings, to_addr=email, subject=subject, body=body)


def event_details_safe(**extra: Any) -> dict[str, Any]:
    return {k: v for k, v in extra.items() if v is not None}
