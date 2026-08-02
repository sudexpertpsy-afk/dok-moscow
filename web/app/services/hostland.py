"""Мосты Hostland и напоминания об оплате VDS (W-34)."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.mail import send_email
from app.services.ops import ops_dir, read_marker, write_marker

log = logging.getLogger("dok.hostland")


def hostland_path() -> Path:
    return ops_dir() / "hostland.json"


def load_hostland() -> dict[str, Any]:
    settings = get_settings()
    data = {
        "panel_url": settings.hostland_panel_url,
        "pay_url": settings.hostland_pay_url,
        "console_url": settings.hostland_console_url,
        "vds_paid_until": None,
    }
    path = hostland_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update({k: raw.get(k, data.get(k)) for k in data})
                if raw.get("vds_paid_until"):
                    data["vds_paid_until"] = raw["vds_paid_until"]
        except Exception:
            log.exception("hostland.json read failed")
    return data


def save_hostland(
    *,
    panel_url: str | None = None,
    pay_url: str | None = None,
    console_url: str | None = None,
    vds_paid_until: date | str | None = None,
) -> dict[str, Any]:
    data = load_hostland()
    if panel_url is not None:
        data["panel_url"] = panel_url.strip()
    if pay_url is not None:
        data["pay_url"] = pay_url.strip()
    if console_url is not None:
        data["console_url"] = console_url.strip()
    if vds_paid_until is not None:
        if isinstance(vds_paid_until, date):
            data["vds_paid_until"] = vds_paid_until.isoformat()
        else:
            data["vds_paid_until"] = str(vds_paid_until).strip() or None
    hostland_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data


def _parse_until(raw: Any) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def days_until_vds_expiry() -> int | None:
    until = _parse_until(load_hostland().get("vds_paid_until"))
    if until is None:
        return None
    return (until - date.today()).days


def maybe_send_vds_reminders(db: Session | None = None) -> list[str]:
    """Письма за 14 и 3 дня до окончания оплаты VDS (раз в день на порог)."""
    del db  # интерфейс совместим с ops_loop
    settings = get_settings()
    to_addr = (settings.admin_notify_email or settings.bootstrap_admin_email or "").strip()
    if not to_addr:
        return []
    days = days_until_vds_expiry()
    if days is None:
        return []
    fired: list[str] = []
    hl = load_hostland()
    for threshold in (14, 3):
        if days != threshold:
            continue
        key = f"vds_remind_{threshold}"
        day = date.today().isoformat()
        prev = read_marker(key)
        if prev and str(prev.get("day")) == day:
            continue
        subject = f"[{settings.app_name}] Оплата VDS через {threshold} дн."
        body = (
            f"Оплаченный период VDS заканчивается {hl.get('vds_paid_until')} "
            f"(осталось {days} дн.).\n\n"
            f"Оплата: {hl.get('pay_url')}\n"
            f"Панель: {hl.get('panel_url')}\n"
            f"Веб-консоль: {hl.get('console_url')}\n\n"
            "Продление делается в Hostland — публичного API нет.\n"
        )
        if send_email(settings, to_addr=to_addr, subject=subject, body=body):
            write_marker(key, day=day, until=hl.get("vds_paid_until"))
            fired.append(key)
            log.info("VDS reminder sent threshold=%s", threshold)
    return fired
