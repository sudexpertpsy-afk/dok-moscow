"""Запись/чтение суточных метрик без отдельной таблицы (W-50 A; F.2 → ops_metrics).

Хранилище: FILES_ROOT/.ops/metrics.jsonl — одна JSON-строка на (день, key).
Повторный запуск за тот же день обновляет значение (идемпотентно для тестов).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import utcnow
from app.services.ops import ops_dir

log = logging.getLogger("dok.ops.metrics")

_METRICS_FILE = "metrics.jsonl"


def _path() -> Path:
    return ops_dir() / _METRICS_FILE


def record_metric(key: str, *, value_num: float | None = None, value_text: str | None = None) -> None:
    day = utcnow().strftime("%Y-%m-%d")
    row = {
        "day": day,
        "ts": utcnow().isoformat(),
        "key": key,
        "value_num": value_num,
        "value_text": value_text,
    }
    path = _path()
    try:
        existing: list[dict[str, Any]] = []
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict) and not (
                    item.get("day") == day and item.get("key") == key
                ):
                    existing.append(item)
        existing.append(row)
        # Храним не больше ~90 дней × ~20 ключей
        if len(existing) > 2000:
            existing = existing[-2000:]
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in existing),
            encoding="utf-8",
        )
    except OSError:
        log.exception("record_metric failed key=%s", key)


def load_metrics(*, key: str | None = None, days: int = 14) -> list[dict[str, Any]]:
    path = _path()
    if not path.is_file():
        return []
    cutoff = utcnow().timestamp() - days * 86400
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            if key and item.get("key") != key:
                continue
            ts_raw = item.get("ts") or ""
            try:
                ts = datetime.fromisoformat(str(ts_raw))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts.timestamp() < cutoff:
                    continue
            except ValueError:
                continue
            out.append(item)
    except OSError:
        log.exception("load_metrics failed")
    return out


def disk_trend_gb_per_week(*, days: int = 14) -> float | None:
    """Линейная оценка роста used_gb за неделю по суточным точкам disk_used_gb."""
    points = load_metrics(key="disk_used_gb", days=days)
    series: list[tuple[float, float]] = []
    for item in points:
        try:
            ts = datetime.fromisoformat(str(item["ts"]))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            val = item.get("value_num")
            if val is None:
                continue
            series.append((ts.timestamp(), float(val)))
        except (ValueError, TypeError, KeyError):
            continue
    if len(series) < 2:
        return None
    series.sort()
    # Ordinary least squares: y = a + b*t → b * 7d
    n = len(series)
    mean_t = sum(t for t, _ in series) / n
    mean_y = sum(y for _, y in series) / n
    var_t = sum((t - mean_t) ** 2 for t, _ in series)
    if var_t <= 0:
        return None
    cov = sum((t - mean_t) * (y - mean_y) for t, y in series)
    slope_per_sec = cov / var_t
    return round(slope_per_sec * 7 * 86400, 3)
