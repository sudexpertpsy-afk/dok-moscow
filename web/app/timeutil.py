"""Часовые пояса: хранение UTC, календарные границы Europe/Moscow."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")


def as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_moscow(dt: datetime) -> datetime:
    return as_utc(dt).astimezone(MSK)


def format_moscow(dt: datetime | None, *, with_time: bool = True) -> str:
    if dt is None:
        return "—"
    local = to_moscow(dt)
    if with_time:
        return local.strftime("%d.%m.%Y %H:%M (МСК)")
    return local.strftime("%d.%m.%Y (МСК)")


def moscow_calendar_days_left(ends_at: datetime, *, now: datetime | None = None) -> int:
    """Целые календарные дни до даты окончания по Москве (может быть отрицательным)."""
    now = now or datetime.now(timezone.utc)
    end_d = to_moscow(ends_at).date()
    now_d = to_moscow(now).date()
    return (end_d - now_d).days


def moscow_day_bounds_utc(
    days_from_today: int,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """UTC-границы московского календарного дня «сегодня МСК + days_from_today»."""
    now = now or datetime.now(timezone.utc)
    target: date = to_moscow(now).date() + timedelta(days=days_from_today)
    start_msk = datetime(target.year, target.month, target.day, tzinfo=MSK)
    end_msk = start_msk + timedelta(days=1)
    return start_msk.astimezone(timezone.utc), end_msk.astimezone(timezone.utc)


def end_of_moscow_day(d: date) -> datetime:
    """23:59:59 Europe/Moscow указанной даты → UTC."""
    return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=MSK).astimezone(
        timezone.utc
    )
