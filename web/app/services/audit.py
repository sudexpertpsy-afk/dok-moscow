"""Запись событий аудита (входы, сброс пароля и т.п.)."""

from __future__ import annotations

import ipaddress
from typing import Any

from sqlalchemy.orm import Session

from app.models import Event


def mask_ip(ip: str | None) -> str:
    """Скрыть хвост IP для хранения в events (ASVS V7.1 / T10).

    IPv4 → последний октет = 0; IPv6 → /64; прочее — как есть / unknown.
    """
    raw = (ip or "").strip()
    if not raw or raw == "unknown":
        return "unknown"
    try:
        addr = ipaddress.ip_address(raw.split("%")[0])
    except ValueError:
        return "unknown"
    if isinstance(addr, ipaddress.IPv4Address):
        parts = str(addr).split(".")
        return ".".join(parts[:3] + ["0"])
    # IPv6: обнулить последние 64 бита (сеть /64)
    net = ipaddress.IPv6Network((addr, 64), strict=False)
    return str(net.network_address)


def sanitize_event_details(details: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(details or {})
    if "ip" in out:
        out["ip"] = mask_ip(str(out.get("ip") or ""))
    return out


def record_event(
    db: Session,
    *,
    type: str,
    org_id: int | None = None,
    user_id: int | None = None,
    details: dict[str, Any] | None = None,
    commit: bool = True,
) -> Event:
    event = Event(
        org_id=org_id,
        user_id=user_id,
        type=type,
        details=sanitize_event_details(details),
    )
    db.add(event)
    if commit:
        db.commit()
        db.refresh(event)
    return event
