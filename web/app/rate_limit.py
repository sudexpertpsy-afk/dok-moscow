"""Ограничение частоты запросов (БД, multi-replica). T2."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

log = logging.getLogger("dok.rate_limit")


class LoginRateLimiter:
    """Скользящее окно по таблице rate_limit_hits (общее для всех реплик)."""

    def __init__(self, limit: int, window_sec: int, *, name: str = "default") -> None:
        self.limit = limit
        self.window_sec = window_sec
        self.name = name

    def _cutoff(self) -> datetime:
        return datetime.now(timezone.utc) - timedelta(seconds=self.window_sec)

    def is_blocked(self, key: str) -> bool:
        from app.db import SessionLocal
        from app.models import RateLimitHit

        cutoff = self._cutoff()
        try:
            with SessionLocal() as db:
                n = db.scalar(
                    select(func.count())
                    .select_from(RateLimitHit)
                    .where(
                        RateLimitHit.bucket == self.name,
                        RateLimitHit.key == key,
                        RateLimitHit.created_at >= cutoff,
                    )
                )
                return int(n or 0) >= self.limit
        except Exception:
            log.exception("rate_limit is_blocked failed bucket=%s", self.name)
            return False

    def register_failure(self, key: str) -> None:
        from app.db import SessionLocal
        from app.models import RateLimitHit

        now = datetime.now(timezone.utc)
        cutoff = self._cutoff()
        try:
            with SessionLocal() as db:
                db.add(
                    RateLimitHit(
                        bucket=self.name,
                        key=key,
                        created_at=now,
                    )
                )
                db.execute(
                    delete(RateLimitHit).where(
                        RateLimitHit.bucket == self.name,
                        RateLimitHit.key == key,
                        RateLimitHit.created_at < cutoff,
                    )
                )
                db.commit()
        except Exception:
            log.exception("rate_limit register_failure failed bucket=%s", self.name)

    def reset(self, key: str) -> None:
        from app.db import SessionLocal
        from app.models import RateLimitHit

        try:
            with SessionLocal() as db:
                db.execute(
                    delete(RateLimitHit).where(
                        RateLimitHit.bucket == self.name,
                        RateLimitHit.key == key,
                    )
                )
                db.commit()
        except Exception:
            log.exception("rate_limit reset failed bucket=%s", self.name)

    def clear(self) -> None:
        from app.db import SessionLocal
        from app.models import RateLimitHit

        try:
            with SessionLocal() as db:
                db.execute(delete(RateLimitHit).where(RateLimitHit.bucket == self.name))
                db.commit()
        except Exception:
            log.exception("rate_limit clear failed bucket=%s", self.name)
