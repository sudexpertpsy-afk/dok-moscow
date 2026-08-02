"""Транш Б аудита: rate limits F-06, TZ МСК, алерт backup_ok, beta_trial MSK."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.services.admin_subscription import subscription_badge
from app.services.billing import beta_trial_ends_at
from app.services.ops import check_alerts, ops_dir
from app.timeutil import (
    end_of_moscow_day,
    moscow_calendar_days_left,
    moscow_day_bounds_utc,
)
from conftest import login

MSK = ZoneInfo("Europe/Moscow")


def test_beta_trial_ends_at_moscow_eod(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("BETA_TRIAL_UNTIL", "2026-10-01")
    get_settings.cache_clear()
    end = beta_trial_ends_at()
    assert end == end_of_moscow_day(date(2026, 10, 1))
    # Не 23:59 UTC (= 02:59 МСК следующего дня)
    assert end.astimezone(MSK).hour == 23
    assert end.astimezone(MSK).day == 1
    get_settings.cache_clear()


def test_moscow_day_bounds_cross_utc_midnight():
    # 02:30 UTC 2 авг = 05:30 МСК 2 авг → «+1 день» = 3 авг МСК
    now = datetime(2026, 8, 2, 2, 30, tzinfo=timezone.utc)
    start, end = moscow_day_bounds_utc(1, now=now)
    assert start.astimezone(MSK).day == 3
    assert (end - start) == timedelta(days=1)


def test_badge_expiring_uses_moscow_calendar_days():
    from types import SimpleNamespace

    from app.models import SubscriptionStatus

    # ends_at: 23:30 UTC 15 авг = 02:30 МСК 16 авг → календарный день 16 МСК
    ends = datetime(2026, 8, 15, 23, 30, tzinfo=timezone.utc)
    now = datetime(2026, 8, 2, 10, 0, tzinfo=timezone.utc)  # 13:00 МСК 2 авг
    assert moscow_calendar_days_left(ends, now=now) == 14

    sub = SimpleNamespace(
        tariff=SimpleNamespace(name="Специалист"),
        status=SubscriptionStatus.active,
        ends_at=ends,
        is_complimentary=False,
        is_beta=False,
        is_current=lambda n=None: True,
    )
    badge = subscription_badge(sub, now=now)
    assert badge["badge"] == "expiring"


def test_backup_stale_alert_26h(app):
    import json

    _, dbmod = app
    for p in ops_dir().glob("*.json"):
        p.unlink()
    old = (datetime.now(timezone.utc) - timedelta(hours=27)).isoformat()
    path = ops_dir() / "backup_ok.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"at": old, "file": "/tmp/x.tar.age", "stamp": "old"}, ensure_ascii=False),
        encoding="utf-8",
    )
    with patch("app.services.ops.send_email", return_value=True):
        db = dbmod.SessionLocal()
        try:
            fired = check_alerts(db)
            assert "backup_stale" in fired
        finally:
            db.close()


def test_global_search_rate_limit(app):
    from app.defaults import empty_requisites
    from app.models import Organization, User, UserRole
    from app.routers.global_search import search_limiter
    from app.security import hash_password

    client, dbmod = app
    db = dbmod.SessionLocal()
    try:
        org = Organization(name="RL", requisites=empty_requisites())
        db.add(org)
        db.flush()
        db.add(
            User(
                org_id=org.id,
                email="rl@example.com",
                password_hash=hash_password("Passw0rd!"),
                role=UserRole.user,
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()
    assert login(client, "rl@example.com", "Passw0rd!").status_code == 303
    search_limiter.clear()
    # искусственно исчерпать лимит
    key = "user:1"
    # узнать реальный user id
    db = dbmod.SessionLocal()
    try:
        from sqlalchemy import select

        from app.models import User

        uid = db.scalar(select(User.id).where(User.email == "rl@example.com"))
        key = f"user:{uid}"
    finally:
        db.close()
    for _ in range(60):
        search_limiter.register_failure(key)
    r = client.get("/api/global-search?q=тест")
    assert r.status_code == 429
