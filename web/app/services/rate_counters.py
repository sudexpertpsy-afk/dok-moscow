"""Атомарные счётчики окон (W-46 §5) — общие для uvicorn-воркеров."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import RateCounter, utcnow


def get_count(db: Session, key: str) -> int:
    row = db.get(RateCounter, key)
    return int(row.count) if row else 0


def incr_counter(db: Session, key: str, *, window_start: datetime, by: int = 1) -> int:
    """Увеличить счётчик; вернуть новое значение. PG — UPSERT, иначе read-modify."""
    now = utcnow()
    bind = db.get_bind()
    dialect = bind.dialect.name if bind is not None else ""

    if dialect == "postgresql":
        table = RateCounter.__table__
        stmt = (
            pg_insert(RateCounter)
            .values(key=key, window_start=window_start, count=by, updated_at=now)
            .on_conflict_do_update(
                index_elements=[table.c.key],
                set_={
                    "count": table.c.count + by,
                    "updated_at": now,
                },
            )
            .returning(table.c.count)
        )
        return int(db.execute(stmt).scalar_one())

    row = db.get(RateCounter, key)
    if row is None:
        row = RateCounter(key=key, window_start=window_start, count=by, updated_at=now)
        db.add(row)
        db.flush()
        return by
    row.count = int(row.count) + by
    row.updated_at = now
    db.flush()
    return int(row.count)


def set_if_absent(db: Session, key: str, *, window_start: datetime, count: int = 0) -> None:
    if db.get(RateCounter, key) is None:
        db.add(RateCounter(key=key, window_start=window_start, count=count, updated_at=utcnow()))
        db.flush()
