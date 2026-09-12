"""Ежедневный сбор метрик ops_daily (W-50 A.5 / F.1)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.ops.certs import cert_days_left, host_from_url
from app.ops.mail_auth import smtp_from_ok
from app.ops.metrics import disk_trend_gb_per_week, record_metric
from app.ops.thresholds import (
    DISK_WARN_PCT,
    TLS_WARN_DAYS,
    tone_disk_pct,
    tone_tls_days,
)
from app.services.ops import disk_usage, send_ops_alert, write_marker

log = logging.getLogger("dok.ops.daily")


def run_ops_daily(db: Session | None = None) -> dict[str, Any]:
    """Собрать метрики диска/TLS/почты, записать markers, при красном — алерт."""
    del db  # зарезервировано для KPI из БД в F.2
    settings = get_settings()
    disk = disk_usage()
    used_pct = disk.get("used_pct")
    used_gb = disk.get("used_gb")
    if isinstance(used_pct, (int, float)):
        record_metric("disk_used_pct", value_num=float(used_pct))
    if isinstance(used_gb, (int, float)):
        record_metric("disk_used_gb", value_num=float(used_gb))

    hosts = []
    for url in (settings.public_base_url, settings.app_base_url):
        h = host_from_url(url)
        if h and h not in hosts:
            hosts.append(h)
    tls: dict[str, int | None] = {}
    for h in hosts:
        days = cert_days_left(h)
        tls[h] = days
        if days is not None:
            record_metric(f"tls_days_{h}", value_num=float(days), value_text=h)

    trend = disk_trend_gb_per_week()
    if trend is not None:
        record_metric("disk_trend_gb_week", value_num=float(trend))

    from_ok, from_detail = smtp_from_ok(settings)
    record_metric("smtp_from_ok", value_num=1.0 if from_ok else 0.0, value_text=from_detail)

    summary = {
        "disk_used_pct": used_pct,
        "disk_trend_gb_week": trend,
        "tls": tls,
        "smtp_from_ok": from_ok,
        "smtp_from_detail": from_detail,
    }
    write_marker("ops_daily", **summary)

    app_name = settings.app_name
    if tone_disk_pct(float(used_pct) if isinstance(used_pct, (int, float)) else None) == "danger":
        send_ops_alert(
            "disk_high",
            f"[{app_name}] Диск > {int(DISK_WARN_PCT)}%",
            f"Использовано {used_pct}% (порог красный {DISK_WARN_PCT}%, цель ≤35%).\n"
            f"Путь: {disk.get('path')}\n"
            "См. docs/эксплуатация.md#prune\n",
        )

    for h, days in tls.items():
        if tone_tls_days(days) == "danger" and days is not None:
            send_ops_alert(
                f"tls_expiring_{h}",
                f"[{app_name}] TLS {h}: осталось {days} дн.",
                f"До notAfter ≤ {TLS_WARN_DAYS} дней. Проверьте Caddy ACME "
                f"(docker logs caddy), порт 80 и DNS.\n"
                "См. docs/эксплуатация.md#tls\n",
            )

    if not from_ok:
        send_ops_alert(
            "smtp_from_domain",
            f"[{app_name}] SMTP From не на dok.moscow",
            f"{from_detail}\n"
            "Пока From не на своём домене, инвайты и алерты рискуют попасть в спам.\n"
            "См. docs/эксплуатация.md#почта и docs/почта_spf_dkim_dmarc.md\n",
        )

    log.info("ops_daily: %s", summary)
    return summary
