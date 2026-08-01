"""Счётчики номеров документов с блокировкой строки."""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Counter


def allocate_number(
    db: Session,
    org_id: int,
    key: str,
    *,
    prefix: str = "",
    suffix: str = "",
    width: int = 0,
) -> tuple[int, str]:
    """Атомарно увеличить счётчик и вернуть (value, formatted).

    PostgreSQL — SELECT … FOR UPDATE.
    SQLite — BEGIN IMMEDIATE (блокировка записи БД).
    """
    dialect = db.bind.dialect.name if db.bind is not None else ""
    if dialect == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))

    stmt = select(Counter).where(Counter.org_id == org_id, Counter.key == key)
    if dialect == "postgresql":
        stmt = stmt.with_for_update()

    counter = db.scalar(stmt)
    if counter is None:
        counter = Counter(
            org_id=org_id,
            key=key,
            prefix=prefix,
            value=0,
            suffix=suffix,
        )
        db.add(counter)
        db.flush()
        if dialect == "postgresql":
            counter = db.scalar(
                select(Counter)
                .where(Counter.org_id == org_id, Counter.key == key)
                .with_for_update()
            )

    assert counter is not None
    if prefix and not counter.prefix:
        counter.prefix = prefix
    if suffix and not counter.suffix:
        counter.suffix = suffix

    counter.value = int(counter.value) + 1
    db.flush()
    num = counter.value
    body = str(num).zfill(width) if width > 0 else str(num)
    formatted = f"{counter.prefix}{body}{counter.suffix}"
    return num, formatted
