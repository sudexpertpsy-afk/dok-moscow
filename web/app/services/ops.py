"""Наблюдаемость: тайминги, маркеры фоновых задач, алерты (W-32)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Document, Event, Organization, Payment, PaymentStatus, User, utcnow
from app.ops.thresholds import DISK_ALERT_PCT, tone_disk_pct
from app.services.mail import send_email

log = logging.getLogger("dok.ops")

_RING_MAX = 2000
_lock = threading.Lock()
_samples: deque[tuple[float, str, float]] = deque(maxlen=_RING_MAX)  # (ts, group, ms)


@dataclass
class LatencyStats:
    group: str
    count: int
    p50_ms: float
    p95_ms: float


def route_group(path: str) -> str:
    if path.startswith("/static"):
        return "static"
    if path.startswith("/api/"):
        return "api"
    if path.startswith("/admin"):
        return "admin"
    if path.startswith("/cabinet"):
        return "cabinet"
    if path.startswith("/zakon"):
        return "zakon"
    if path.startswith("/billing"):
        return "billing"
    if path.startswith("/login") or path.startswith("/auth") or path.startswith("/invite"):
        return "auth"
    if path in {"/", "/apply", "/privacy", "/offer", "/requisites", "/tariffs", "/contacts"}:
        return "landing"
    return "other"


def record_timing(path: str, duration_ms: float) -> None:
    with _lock:
        _samples.append((time.time(), route_group(path), float(duration_ms)))


def latency_stats(*, window_sec: int = 3600) -> list[LatencyStats]:
    cutoff = time.time() - window_sec
    by_group: dict[str, list[float]] = {}
    with _lock:
        for ts, group, ms in _samples:
            if ts >= cutoff:
                by_group.setdefault(group, []).append(ms)
    out: list[LatencyStats] = []
    for group, values in sorted(by_group.items()):
        values_sorted = sorted(values)
        n = len(values_sorted)
        if n == 0:
            continue
        p50 = statistics.median(values_sorted)
        idx95 = min(n - 1, max(0, int(round(0.95 * (n - 1)))))
        p95 = values_sorted[idx95]
        out.append(LatencyStats(group=group, count=n, p50_ms=round(p50, 1), p95_ms=round(p95, 1)))
    return out


def clear_timings() -> None:
    with _lock:
        _samples.clear()


def ops_dir() -> Path:
    root = Path(get_settings().files_root)
    d = root / ".ops"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_marker(name: str, **extra: Any) -> None:
    payload = {"at": utcnow().isoformat(), **extra}
    path = ops_dir() / f"{name}.json"
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        log.exception("ops marker write failed: %s", name)


def read_marker(name: str) -> dict[str, Any] | None:
    path = ops_dir() / f"{name}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def marker_age_sec(name: str) -> float | None:
    data = read_marker(name)
    if not data or not data.get("at"):
        return None
    try:
        at = datetime.fromisoformat(str(data["at"]))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return max(0.0, (utcnow() - at).total_seconds())
    except Exception:
        return None


def disk_usage() -> dict[str, Any]:
    root = Path(get_settings().files_root)
    try:
        usage = shutil.disk_usage(root)
        pct = round(100.0 * usage.used / usage.total, 1) if usage.total else 0.0
        tone = tone_disk_pct(pct)
        return {
            "path": str(root),
            "total_gb": round(usage.total / (1024**3), 2),
            "used_gb": round(usage.used / (1024**3), 2),
            "free_gb": round(usage.free / (1024**3), 2),
            "used_pct": pct,
            "tone": tone,
            # ok = не красный (алерт только на danger); жёлтый 35–60% — предупреждение в UI
            "ok": tone != "danger",
        }
    except Exception as exc:
        return {
            "path": str(root),
            "error": str(exc),
            "ok": False,
            "used_pct": 100.0,
            "tone": "danger",
        }


def db_size_bytes(db: Session) -> int | None:
    """Размер БД: PostgreSQL pg_database_size, иначе размер файла SQLite."""
    url = get_settings().db_url
    try:
        if url.startswith("postgresql"):
            size = db.scalar(select(func.pg_database_size(func.current_database())))
            return int(size) if size is not None else None
        if url.startswith("sqlite"):
            # sqlite+pysqlite:////path
            path = url.split("///")[-1]
            p = Path(path)
            return p.stat().st_size if p.is_file() else None
    except Exception:
        log.exception("db size probe failed")
    return None


def pending_jobs_count(db: Session) -> int:
    try:
        from app.models import Job, JobStatus

        return int(
            db.scalar(select(func.count()).select_from(Job).where(Job.status == JobStatus.pending))
            or 0
        )
    except Exception:
        return 0


def _fmt_age(sec: float | None) -> str:
    if sec is None:
        return "нет маркера"
    if sec < 90:
        return f"{int(sec)} с назад"
    if sec < 3600:
        return f"{int(sec // 60)} мин назад"
    if sec < 86400:
        return f"{sec / 3600:.1f} ч назад"
    return f"{sec / 86400:.1f} сут назад"


# HOTFIX: operational-причины вебхука не шлём письмом (только счётчик на /admin/status).
WEBHOOK_OPERATIONAL_REASONS = frozenset(
    {
        "Неизвестный OrderId",
        "Платёж не найден",
    }
)

_DEFAULT_WEBHOOK_ALERT = {
    "enabled": True,
    "threshold": 3,
    "quiet_hours": 6.0,
}


def webhook_fail_category(reason: str) -> str:
    """security — угроза (подпись/флуд); operational — шум банка по осиротевшим OrderId."""
    r = (reason or "").strip()
    if r in WEBHOOK_OPERATIONAL_REASONS:
        return "operational"
    return "security"


def get_webhook_alert_settings() -> dict[str, Any]:
    data = read_marker("webhook_alert_settings") or {}
    try:
        threshold = int(data.get("threshold", _DEFAULT_WEBHOOK_ALERT["threshold"]))
    except (TypeError, ValueError):
        threshold = int(_DEFAULT_WEBHOOK_ALERT["threshold"])
    try:
        quiet = float(data.get("quiet_hours", _DEFAULT_WEBHOOK_ALERT["quiet_hours"]))
    except (TypeError, ValueError):
        quiet = float(_DEFAULT_WEBHOOK_ALERT["quiet_hours"])
    enabled = data.get("enabled")
    if enabled is None:
        enabled = _DEFAULT_WEBHOOK_ALERT["enabled"]
    return {
        "enabled": bool(enabled),
        "threshold": max(1, min(threshold, 1000)),
        "quiet_hours": max(0.25, min(quiet, 168.0)),
    }


def save_webhook_alert_settings(
    *,
    enabled: bool,
    threshold: int = 3,
    quiet_hours: float = 6.0,
) -> dict[str, Any]:
    cfg = {
        "enabled": bool(enabled),
        "threshold": max(1, min(int(threshold), 1000)),
        "quiet_hours": max(0.25, min(float(quiet_hours), 168.0)),
    }
    write_marker("webhook_alert_settings", **cfg)
    return cfg


def status_snapshot(db: Session) -> dict[str, Any]:
    disk = disk_usage()
    db_bytes = db_size_bytes(db)
    worker_age = marker_age_sec("worker_heartbeat")
    reconcile_age = marker_age_sec("billing_reconcile")
    daily_age = marker_age_sec("billing_daily")
    legal_age = marker_age_sec("legal_watch")
    webhook_ok_age = marker_age_sec("webhook_ok")
    webhook_fail = read_marker("webhook_fail") or {}
    backup = read_marker("backup_ok")
    backup_age = marker_age_sec("backup_ok")
    alert_cfg = get_webhook_alert_settings()

    # Интервалы: worker heartbeat ~2 с; reconcile 30 мин; daily/legal 24 ч; backup ~24 ч
    worker_ok = worker_age is not None and worker_age < 120
    reconcile_ok = reconcile_age is None or reconcile_age < 2 * 30 * 60
    daily_ok = daily_age is None or daily_age < 2 * 24 * 3600
    legal_ok = legal_age is None or legal_age < 2 * 24 * 3600
    backup_ok = backup_age is not None and backup_age < 2 * 24 * 3600
    fail_streak = int(webhook_fail.get("streak") or 0)
    ops_count = int(webhook_fail.get("ops_count") or 0)

    settings = get_settings()
    app_version = (settings.app_version or "dev").strip()
    expected = ""
    expected_path = Path(settings.files_root).parent / "ops" / "deployed_version"
    try:
        if expected_path.is_file():
            expected = expected_path.read_text(encoding="utf-8").strip()
    except OSError:
        expected = ""
    version_ok = (not expected) or (app_version == expected) or app_version.startswith(expected[:7])
    # прод не должен светить cursor/* как версию
    on_cursor_branch = app_version.startswith("cursor/")

    from app.ops.certs import host_from_url
    from app.ops.mail_auth import (
        mail_auth_checklist,
        read_mail_auth_ok,
        read_restore_drill,
        restore_drill_age_days,
        smtp_from_domain,
        smtp_from_ok,
    )
    from app.ops.metrics import disk_trend_gb_per_week
    from app.ops.thresholds import (
        ThresholdResult,
        tone_backup_hours,
        tone_disk_trend,
        tone_mail_auth,
        tone_restore_days,
        tone_tls_days,
        tone_worker_sec,
    )

    hosts: list[str] = []
    for url in (settings.public_base_url, settings.app_base_url):
        h = host_from_url(url)
        if h and h not in hosts:
            hosts.append(h)

    # TLS только из маркера ops_daily (live-probe — в worker, не на каждый GET /admin/status).
    ops_daily = read_marker("ops_daily") or {}
    tls_cached = ops_daily.get("tls") if isinstance(ops_daily.get("tls"), dict) else {}
    tls_rows: list[dict[str, Any]] = []
    for h in hosts:
        days = tls_cached.get(h)
        days_i = int(days) if isinstance(days, (int, float)) else None
        tls_rows.append(
            {
                "host": h,
                "days_left": days_i,
                "tone": tone_tls_days(days_i),
            }
        )

    trend = disk_trend_gb_per_week()
    backup_age_h = (backup_age / 3600.0) if backup_age is not None else None
    restore_age_d = restore_drill_age_days(settings)
    restore_detail = read_restore_drill(settings)
    from_ok, from_detail = smtp_from_ok(settings)
    mail_ok, mail_fact, mail_marker_domain, mail_age_d = mail_auth_checklist(settings)
    mail_marker = read_mail_auth_ok(settings)
    current_mail_domain = smtp_from_domain(settings.smtp_from or "")

    checklist: list[ThresholdResult] = [
        ThresholdResult(
            key="disk",
            label="Диск /",
            fact=f"{disk.get('used_pct')}% · свободно {disk.get('free_gb')} ГБ",
            tone=disk.get("tone") or tone_disk_pct(
                float(disk["used_pct"]) if isinstance(disk.get("used_pct"), (int, float)) else None
            ),
            how_to="prune",
        ),
        ThresholdResult(
            key="disk_trend",
            label="Тренд диска",
            fact=(f"{trend} ГБ/нед" if trend is not None else "н/д (мало точек)"),
            tone=tone_disk_trend(trend),
            how_to="prune",
        ),
    ]
    for row in tls_rows:
        days = row["days_left"]
        checklist.append(
            ThresholdResult(
                key=f"tls_{row['host']}",
                label=f"TLS {row['host']}",
                fact=(f"{days} дн. до notAfter" if days is not None else "н/д"),
                tone=row["tone"],
                how_to="tls",
            )
        )
    checklist.extend(
        [
            ThresholdResult(
                key="backup",
                label="Бэкап (маркер)",
                fact=_fmt_age(backup_age),
                tone=tone_backup_hours(backup_age_h),
                how_to="бэкапы",
            ),
            ThresholdResult(
                key="restore_drill",
                label="Restore drill",
                fact=(
                    f"{restore_age_d:.0f} дн. назад · {restore_detail.get('backup')}"
                    if restore_age_d is not None and restore_detail
                    else "нет маркера data/ops/restore_drill.json"
                ),
                tone=tone_restore_days(restore_age_d),
                how_to="restore-drill",
            ),
            ThresholdResult(
                key="worker",
                label="Worker heartbeat",
                fact=_fmt_age(worker_age),
                tone=tone_worker_sec(worker_age),
                how_to="аварии",
            ),
            ThresholdResult(
                key="mail",
                label="Почта (From / SPF)",
                fact=mail_fact,
                tone=tone_mail_auth(
                    from_ok=from_ok,
                    marker_age_days=mail_age_d,
                    marker_domain=mail_marker_domain,
                    current_domain=current_mail_domain,
                ),
                how_to="почта",
            ),
            ThresholdResult(
                key="version",
                label="Прод = тег деплоя",
                fact=f"{app_version}"
                + (f" · ожидается {expected}" if expected else ""),
                tone="ok" if (version_ok and not on_cursor_branch) else "danger",
                how_to="обновление",
            ),
        ]
    )

    # W-50.1 §3: строка Purge в чек-листе
    from app.services.billing import ensure_payment_settings
    from app.services.org_purge import get_purge_mode, list_purge_candidates

    purge_mode = get_purge_mode(db)
    purge_n = len(list_purge_candidates(db))
    ps = ensure_payment_settings(db)
    purge_changed = ps.purge_mode_changed_at
    purge_age_d: float | None = None
    if purge_changed is not None:
        pc = purge_changed
        if pc.tzinfo is None:
            from datetime import timezone

            pc = pc.replace(tzinfo=timezone.utc)
        from app.models import utcnow as _utcnow

        purge_age_d = max(0.0, (_utcnow() - pc).total_seconds() / 86400.0)
    purge_tone = "ok"
    if purge_mode == "dry":
        purge_tone = "warn" if (purge_age_d is not None and purge_age_d > 7) else "ok"
    checklist.append(
        ThresholdResult(
            key="purge",
            label="Purge",
            fact=f"режим {purge_mode}, кандидатов {purge_n}",
            tone=purge_tone,
            how_to="prune",
        )
    )

    return {
        "latency": latency_stats(),
        "disk": disk,
        "db_size_mb": round(db_bytes / (1024 * 1024), 2) if db_bytes is not None else None,
        "pending_jobs": pending_jobs_count(db),
        "app_version": app_version,
        "expected_version": expected or None,
        "version_ok": version_ok and not on_cursor_branch,
        "background": [
            ("Worker heartbeat", _fmt_age(worker_age), worker_ok),
            ("Сверка Т-Кассы", _fmt_age(reconcile_age), reconcile_ok),
            ("Суточные задачи", _fmt_age(daily_age), daily_ok),
            ("Мониторинг НПА", _fmt_age(legal_age), legal_ok),
            ("Бэкап (маркер)", _fmt_age(backup_age), backup_ok if backup else False),
        ],
        "webhook": {
            "last_ok": _fmt_age(webhook_ok_age),
            "fail_streak": fail_streak,
            "ops_count": ops_count,
            "ops_reason": str(webhook_fail.get("ops_reason") or "")[:200],
            "last_security_reason": str(webhook_fail.get("reason") or "")[:200],
            "ok": fail_streak < int(alert_cfg["threshold"]),
            "alerts_enabled": bool(alert_cfg["enabled"]),
            "alert_threshold": int(alert_cfg["threshold"]),
            "quiet_hours": float(alert_cfg["quiet_hours"]),
        },
        "backup_detail": backup,
        "checklist": [
            {
                "key": c.key,
                "label": c.label,
                "fact": c.fact,
                "tone": c.tone,
                "how_to": c.how_to,
            }
            for c in checklist
        ],
        "tls": tls_rows,
        "mail_from_ok": from_ok,
        "mail_from_detail": from_detail,
        "mail_auth_ok": mail_ok,
        "mail_auth": mail_marker,
        "restore_drill": restore_detail,
        "disk_trend_gb_week": trend,
        "purge_mode": purge_mode,
        "purge_candidates": purge_n,
    }


def _admin_addr() -> str:
    s = get_settings()
    return (s.admin_notify_email or s.bootstrap_admin_email or "").strip()


def _alert_cooldown_ok(key: str, *, hours: float = 1.0) -> bool:
    data = read_marker(f"alert_{key}")
    if not data or not data.get("at"):
        return True
    age = marker_age_sec(f"alert_{key}")
    return age is None or age >= hours * 3600


def _send_alert(key: str, subject: str, body: str, *, hours: float = 1.0) -> bool:
    """Отправить алерт с тихим периодом. True если письмо ушло."""
    if not _alert_cooldown_ok(key, hours=hours):
        return False
    settings = get_settings()
    to_addr = _admin_addr()
    if not to_addr:
        log.warning("Алерт без получателя: %s", subject)
        return False
    if send_email(settings, to_addr=to_addr, subject=subject, body=body):
        write_marker(f"alert_{key}", subject=subject)
        return True
    return False


def send_ops_alert(key: str, subject: str, body: str) -> None:
    """Публичная обёртка для алертов из биллинга/воркера (W-45)."""
    _send_alert(key, subject, body)


def check_alerts(db: Session) -> list[str]:
    """Проверить пороги и при необходимости отправить письма. Возвращает ключи алертов."""
    fired: list[str] = []
    snap = status_snapshot(db)
    app_name = get_settings().app_name

    worker_age = marker_age_sec("worker_heartbeat")
    # Только если маркер уже был (прод с worker); иначе не шумим в тестах/dev
    if worker_age is not None and worker_age > 2 * 60:
        key = "worker_stale"
        _send_alert(
            key,
            f"[{app_name}] Worker не отвечает",
            f"Heartbeat worker старше {int(worker_age)} с (порог 2×60 с).\n",
        )
        fired.append(key)

    reconcile_age = marker_age_sec("billing_reconcile")
    if reconcile_age is not None and reconcile_age > 2 * 30 * 60:
        key = "reconcile_stale"
        _send_alert(
            key,
            f"[{app_name}] Сверка платежей просрочена",
            f"Последняя сверка Т-Кассы: {_fmt_age(reconcile_age)} (порог 2×30 мин).\n",
        )
        fired.append(key)

    legal_age = marker_age_sec("legal_watch")
    if legal_age is not None and legal_age > 2 * 24 * 3600:
        key = "legal_stale"
        _send_alert(
            key,
            f"[{app_name}] Мониторинг НПА просрочен",
            f"Последний запуск: {_fmt_age(legal_age)} (порог 2×24 ч).\n",
        )
        fired.append(key)

    # HOTFIX: письмом только security-отказы; operational — только счётчик на статусе.
    wh_cfg = get_webhook_alert_settings()
    fail = read_marker("webhook_fail") or {}
    streak = int(fail.get("streak") or 0)
    quiet = float(wh_cfg["quiet_hours"])
    if wh_cfg["enabled"] and streak >= int(wh_cfg["threshold"]):
        key = "webhook_fail"
        period = int(fail.get("security_since_alert") or streak)
        if _send_alert(
            key,
            f"[{app_name}] Ошибки вебхука Т-Кассы",
            f"Подряд security-отказов: {streak} (порог {wh_cfg['threshold']}).\n"
            f"За тихий период: {period}.\n"
            f"Последняя причина: {fail.get('reason', '—')}\n"
            f"IP: {fail.get('last_ip') or '—'}\n"
            f"Operational (без письма): {int(fail.get('ops_count') or 0)}"
            f" — {fail.get('ops_reason') or '—'}\n",
            hours=quiet,
        ):
            write_marker(
                "webhook_fail",
                streak=streak,
                reason=fail.get("reason") or "",
                hour_window_start=fail.get("hour_window_start"),
                hour_count=int(fail.get("hour_count") or 0),
                last_ip=fail.get("last_ip") or "",
                ops_count=int(fail.get("ops_count") or 0),
                ops_reason=fail.get("ops_reason") or "",
                security_since_alert=0,
            )
        fired.append(key)

    hour_count = int(fail.get("hour_count") or 0)
    window_start = float(fail.get("hour_window_start") or 0)
    if (
        wh_cfg["enabled"]
        and hour_count > 10
        and (time.time() - window_start) < 3600
    ):
        key = "webhook_fail_rate"
        if _send_alert(
            key,
            f"[{app_name}] Вебхук Т-Кассы: много отказов за час",
            f"Security-отказов за текущий час: {hour_count} (порог >10).\n"
            f"Последняя причина: {fail.get('reason', '—')}\n"
            f"IP: {fail.get('last_ip') or '—'}\n",
            hours=quiet,
        ):
            pass
        fired.append(key)

    disk = snap["disk"]
    if not disk.get("ok", True):
        key = "disk_high"
        _send_alert(
            key,
            f"[{app_name}] Диск заполнен > {int(DISK_ALERT_PCT)}%",
            f"Использовано {disk.get('used_pct')}% на {disk.get('path')}.\n",
        )
        fired.append(key)

    # W-46: дрейф APP_VERSION vs data/ops/deployed_version (бейдж + письмо)
    if not snap.get("version_ok", True):
        key = "app_version_drift"
        _send_alert(
            key,
            f"[{app_name}] Дрейф APP_VERSION",
            f"Работает: {snap.get('app_version')}\n"
            f"Ожидается (deployed_version): {snap.get('expected_version')}\n"
            "Прод должен совпадать с тегом деплоя. "
            "Проверьте: ./deploy.sh --tag … и DOK_IMAGE в deploy/.env.\n",
            hours=6.0,
        )
        fired.append(key)

    backup_age = marker_age_sec("backup_ok")
    # W-45/G-01 (закрыто W-46): маркер в FILES_ROOT/.ops; host-путь == контейнер
    # (/srv/dok/data/files). Раньше host ≠ named volume dok_files → слепой статус.
    if backup_age is None:
        key = "backup_missing"
        _send_alert(
            key,
            f"[{app_name}] Нет маркера бэкапа",
            "Маркер backup_ok отсутствует в FILES_ROOT/.ops/.\n"
            "Проверьте deploy/backup.sh (путь $FILES_ROOT/.ops/backup_ok.json).\n",
        )
        fired.append(key)
    elif backup_age > 26 * 3600:
        key = "backup_stale"
        _send_alert(
            key,
            f"[{app_name}] Бэкап не обновлялся",
            f"Маркер backup_ok: {_fmt_age(backup_age)} (порог 26 ч). Проверьте deploy/backup.sh.\n"
            f"Ожидаемый путь маркера: FILES_ROOT/.ops/backup_ok.json\n",
        )
        fired.append(key)

    return fired


def record_webhook_ok() -> None:
    write_marker("webhook_ok")
    # Сбрасываем security-streak; operational-счётчик и часовое окно сохраняем.
    prev = read_marker("webhook_fail") or {}
    write_marker(
        "webhook_fail",
        streak=0,
        reason=prev.get("reason") or "",
        hour_window_start=float(prev.get("hour_window_start") or 0) or None,
        hour_count=int(prev.get("hour_count") or 0),
        last_ip=prev.get("last_ip") or "",
        ops_count=int(prev.get("ops_count") or 0),
        ops_reason=prev.get("ops_reason") or "",
        security_since_alert=0,
    )


def record_webhook_fail(reason: str, *, ip: str | None = None) -> None:
    """Учёт отказа вебхука: security → streak/hour; operational → ops_count (без писем)."""
    prev = read_marker("webhook_fail") or {}
    cat = webhook_fail_category(reason)
    now = time.time()
    streak = int(prev.get("streak") or 0)
    hour_count = int(prev.get("hour_count") or 0)
    window_start = float(prev.get("hour_window_start") or 0)
    ops_count = int(prev.get("ops_count") or 0)
    ops_reason = str(prev.get("ops_reason") or "")
    security_since = int(prev.get("security_since_alert") or 0)
    last_ip = (ip or prev.get("last_ip") or "")[:64]
    sec_reason = str(prev.get("reason") or "")

    if cat == "operational":
        ops_count += 1
        ops_reason = reason[:500]
    else:
        streak += 1
        security_since += 1
        sec_reason = reason[:500]
        if not window_start or (now - window_start) >= 3600:
            window_start = now
            hour_count = 1
        else:
            hour_count += 1

    write_marker(
        "webhook_fail",
        streak=streak,
        reason=sec_reason,
        hour_window_start=window_start or None,
        hour_count=hour_count,
        last_ip=last_ip,
        ops_count=ops_count,
        ops_reason=ops_reason,
        security_since_alert=security_since,
    )


def weekly_digest(db: Session) -> bool:
    """Еженедельный дайджест владельцу. Возвращает True если отправлено."""
    settings = get_settings()
    to_addr = _admin_addr()
    if not to_addr:
        return False
    since = utcnow() - timedelta(days=7)
    orgs = int(
        db.scalar(select(func.count()).select_from(Organization).where(Organization.created_at >= since))
        or 0
    )
    docs = int(
        db.scalar(select(func.count()).select_from(Document).where(Document.created_at >= since)) or 0
    )
    pays = int(
        db.scalar(
            select(func.count())
            .select_from(Payment)
            .where(Payment.created_at >= since, Payment.status == PaymentStatus.confirmed)
        )
        or 0
    )
    users = int(db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True))) or 0)
    err_events = list(
        db.scalars(
            select(Event)
            .where(
                Event.created_at >= since,
                Event.type.in_(
                    [
                        "billing_webhook_rejected",
                        "billing_autorenew_failed",
                    ]
                ),
            )
            .order_by(Event.id.desc())
            .limit(10)
        ).all()
    )
    legal_age = marker_age_sec("legal_watch")
    latency = latency_stats(window_sec=7 * 24 * 3600)
    lat_lines = "\n".join(
        f"  {s.group}: n={s.count} P50={s.p50_ms}ms P95={s.p95_ms}ms" for s in latency
    ) or "  нет данных"
    err_lines = "\n".join(f"  {e.type} #{e.id}" for e in err_events) or "  нет"

    body = (
        f"Дайджест {settings.app_name} за 7 дней\n\n"
        f"Новых организаций: {orgs}\n"
        f"Документов создано: {docs}\n"
        f"Подтверждённых платежей: {pays}\n"
        f"Активных пользователей (всего): {users}\n"
        f"Мониторинг НПА: {_fmt_age(legal_age)}\n"
        f"Ожидающие jobs: {pending_jobs_count(db)}\n\n"
        f"Латентность (окно 7 сут):\n{lat_lines}\n\n"
        f"Топ событий-ошибок:\n{err_lines}\n"
    )
    ok = send_email(
        settings,
        to_addr=to_addr,
        subject=f"[{settings.app_name}] Еженедельный дайджест",
        body=body,
    )
    if ok:
        write_marker("weekly_digest", orgs=orgs, docs=docs, pays=pays)
    return ok


def maybe_send_weekly_digest(db: Session) -> None:
    """Раз в неделю (понедельник UTC) — один дайджест."""
    if utcnow().weekday() != 0:  # Monday
        return
    day = utcnow().strftime("%Y-%m-%d")
    prev = read_marker("weekly_digest")
    if prev and str(prev.get("at", "")).startswith(day):
        return
    weekly_digest(db)


def ops_loop_tick(db: Session) -> None:
    """Тик из worker: heartbeat + алерты + дайджест + напоминания VDS (W-34)."""
    write_marker("worker_heartbeat", pid=os.getpid())
    try:
        check_alerts(db)
    except Exception:
        log.exception("check_alerts failed")
    try:
        maybe_send_weekly_digest(db)
    except Exception:
        log.exception("weekly digest failed")
    try:
        from app.services.hostland import maybe_send_vds_reminders

        maybe_send_vds_reminders(db)
    except Exception:
        log.exception("vds reminders failed")
    try:
        from app.services.dadata_cache_store import purge_expired_dadata_cache
        from app.services.package_wizard_store import purge_expired_wizard_sessions

        n_w = purge_expired_wizard_sessions(db)
        n_c = purge_expired_dadata_cache(db)
        if n_w or n_c:
            db.commit()
            log.info("purged wizard=%s dadata_cache=%s", n_w, n_c)
    except Exception:
        log.exception("wizard/dadata purge failed")
        db.rollback()
