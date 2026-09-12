"""Пороги чек-листа /admin/status (W-50 F.1).

Каждая метрика: зелёный / жёлтый / красный. Алерт по операционному каналу —
только на красный (не чаще раза в сутки на ключ).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Tone = Literal["ok", "warn", "danger", "info"]


@dataclass(frozen=True)
class ThresholdResult:
    key: str
    label: str
    fact: str
    tone: Tone
    how_to: str  # якорь docs/эксплуатация.md#


# Диск: ≤35 зелёный, ≤60 жёлтый, >60 красный (алерт).
DISK_OK_PCT = 35.0
DISK_WARN_PCT = 60.0
# Совместимость: письмо при красном.
DISK_ALERT_PCT = DISK_WARN_PCT

# TLS days-left: >30 ok, >21 warn, ≤21 danger.
TLS_OK_DAYS = 30
TLS_WARN_DAYS = 21

# Бэкап маркер: <26 ч ok, <50 ч warn, иначе danger.
BACKUP_OK_HOURS = 26.0
BACKUP_WARN_HOURS = 50.0

# Restore drill: <90 дн ok, <120 warn, иначе/нет — danger.
RESTORE_OK_DAYS = 90
RESTORE_WARN_DAYS = 120

# Worker heartbeat.
WORKER_OK_SEC = 10 * 60
WORKER_WARN_SEC = 60 * 60

# Почта: маркер mail_auth_ok не старше 180 дней.
MAIL_AUTH_OK_DAYS = 180

# Тренд диска ГБ/нед: <0.5 ok, <1 warn, ≥1 danger.
DISK_TREND_OK_GB_WEEK = 0.5
DISK_TREND_WARN_GB_WEEK = 1.0


def tone_disk_pct(used_pct: float | None) -> Tone:
    if used_pct is None:
        return "info"
    if used_pct <= DISK_OK_PCT:
        return "ok"
    if used_pct <= DISK_WARN_PCT:
        return "warn"
    return "danger"


def tone_tls_days(days: int | None) -> Tone:
    if days is None:
        return "info"
    if days > TLS_OK_DAYS:
        return "ok"
    if days > TLS_WARN_DAYS:
        return "warn"
    return "danger"


def tone_backup_hours(age_h: float | None) -> Tone:
    if age_h is None:
        return "danger"
    if age_h < BACKUP_OK_HOURS:
        return "ok"
    if age_h < BACKUP_WARN_HOURS:
        return "warn"
    return "danger"


def tone_restore_days(age_d: float | None) -> Tone:
    if age_d is None:
        return "danger"
    if age_d < RESTORE_OK_DAYS:
        return "ok"
    if age_d < RESTORE_WARN_DAYS:
        return "warn"
    return "danger"


def tone_worker_sec(age_s: float | None) -> Tone:
    if age_s is None:
        return "info"
    if age_s < WORKER_OK_SEC:
        return "ok"
    if age_s < WORKER_WARN_SEC:
        return "warn"
    return "danger"


def tone_disk_trend(gb_per_week: float | None) -> Tone:
    if gb_per_week is None:
        return "info"
    if gb_per_week < DISK_TREND_OK_GB_WEEK:
        return "ok"
    if gb_per_week < DISK_TREND_WARN_GB_WEEK:
        return "warn"
    return "danger"


def tone_mail_auth(
    *,
    from_ok: bool,
    marker_age_days: float | None,
    marker_domain: str | None = None,
    current_domain: str | None = None,
) -> Tone:
    """Зелёный только если From сейчас @dok.moscow, маркер для того же домена и не старше 180 дн."""
    expected = "dok.moscow"
    if not from_ok:
        return "danger"
    if (marker_domain or "").strip().lower() != expected:
        return "danger"
    if (current_domain or "").strip().lower() != expected:
        return "danger"
    if marker_age_days is None or marker_age_days >= MAIL_AUTH_OK_DAYS:
        return "danger"
    return "ok"
