"""Кэш DaData в PostgreSQL (W-46 §5.2), TTL по умолчанию 10 мин."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import DadataCache, utcnow

DEFAULT_TTL = timedelta(minutes=10)


def cache_get(db: Session, key: str) -> dict[str, Any] | None:
    row = db.get(DadataCache, key)
    if row is None:
        return None
    exp = row.expires_at
    if exp.tzinfo is None:
        from datetime import timezone

        exp = exp.replace(tzinfo=timezone.utc)
    if exp < utcnow():
        db.delete(row)
        db.flush()
        return None
    payload = row.payload
    return dict(payload) if isinstance(payload, dict) else None


def cache_set(
    db: Session,
    key: str,
    payload: dict[str, Any],
    *,
    ttl: timedelta = DEFAULT_TTL,
) -> None:
    now = utcnow()
    row = db.get(DadataCache, key)
    if row is None:
        db.add(
            DadataCache(
                cache_key=key,
                payload=payload,
                expires_at=now + ttl,
                created_at=now,
            )
        )
    else:
        row.payload = payload
        row.expires_at = now + ttl
    db.flush()


def purge_expired_dadata_cache(db: Session) -> int:
    result = db.execute(delete(DadataCache).where(DadataCache.expires_at < utcnow()))
    return int(result.rowcount or 0)
