"""Проверка домена SMTP From и маркер mail_auth_ok (W-50 A.6)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.models import utcnow

log = logging.getLogger("dok.ops.mail_auth")

_EXPECTED_DOMAIN = "dok.moscow"


def data_ops_dir(settings: Settings | None = None) -> Path:
    """Каталог /srv/dok/data/ops (рядом с files/), не FILES_ROOT/.ops."""
    s = settings or get_settings()
    d = Path(s.files_root).resolve().parent / "ops"
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_smtp_from_email(smtp_from: str) -> str:
    _, addr = parseaddr((smtp_from or "").strip())
    return (addr or "").strip().lower()


def smtp_from_domain(smtp_from: str) -> str:
    email = parse_smtp_from_email(smtp_from)
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1]


def smtp_from_ok(settings: Settings | None = None) -> tuple[bool, str]:
    """True, если From на dok.moscow. detail — человекочитаемо."""
    s = settings or get_settings()
    raw = (s.smtp_from or "").strip()
    if not raw:
        return False, "SMTP_FROM не задан"
    domain = smtp_from_domain(raw)
    if domain == _EXPECTED_DOMAIN:
        return True, f"From @{domain}"
    if not domain:
        return False, f"не разобрать From: {raw[:80]}"
    return False, f"From @{domain} (ожидается @{_EXPECTED_DOMAIN})"


def mail_auth_marker_path(settings: Settings | None = None) -> Path:
    return data_ops_dir(settings) / "mail_auth_ok.json"


def read_mail_auth_ok(settings: Settings | None = None) -> dict[str, Any] | None:
    path = mail_auth_marker_path(settings)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def write_mail_auth_ok(*, checked_by: str, settings: Settings | None = None) -> Path:
    path = mail_auth_marker_path(settings)
    payload = {
        "date": utcnow().date().isoformat(),
        "at": utcnow().isoformat(),
        "checked_by": checked_by,
        "spf": "PASS",
        "dkim": "PASS",
        "dmarc": "PASS",
        "note": "подтверждено вручную по «Показать оригинал» в почтовом клиенте",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def mail_auth_age_days(settings: Settings | None = None) -> float | None:
    data = read_mail_auth_ok(settings)
    if not data:
        return None
    raw = data.get("at") or data.get("date")
    if not raw:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(raw)):
            at = datetime.fromisoformat(str(raw) + "T00:00:00+00:00")
        else:
            at = datetime.fromisoformat(str(raw))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return max(0.0, (utcnow() - at).total_seconds() / 86400.0)
    except ValueError:
        return None


def restore_drill_path(settings: Settings | None = None) -> Path:
    return data_ops_dir(settings) / "restore_drill.json"


def read_restore_drill(settings: Settings | None = None) -> dict[str, Any] | None:
    path = restore_drill_path(settings)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def restore_drill_age_days(settings: Settings | None = None) -> float | None:
    data = read_restore_drill(settings)
    if not data:
        return None
    raw = data.get("date") or data.get("at")
    if not raw:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(raw)):
            at = datetime.fromisoformat(str(raw) + "T00:00:00+00:00")
        else:
            at = datetime.fromisoformat(str(raw))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return max(0.0, (utcnow() - at).total_seconds() / 86400.0)
    except ValueError:
        return None
