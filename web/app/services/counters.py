"""Счётчики номеров документов с блокировкой строки."""

from __future__ import annotations

import threading
from contextlib import nullcontext

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Counter

# RLock: тест параллельной выдачи держит lock через allocate+commit.
_sqlite_lock = threading.RLock()


def _format_number(counter: Counter, num: int, width: int) -> str:
    body = str(num).zfill(width) if width > 0 else str(num)
    return f"{counter.prefix}{body}{counter.suffix}"


def _allocate_in_session(
    db: Session,
    org_id: int,
    key: str,
    *,
    prefix: str,
    suffix: str,
    width: int,
    for_update: bool,
) -> tuple[int, str]:
    stmt = select(Counter).where(Counter.org_id == org_id, Counter.key == key)
    if for_update:
        stmt = stmt.with_for_update()

    counter = db.scalar(stmt)
    if counter is None:
        try:
            with db.begin_nested():
                counter = Counter(
                    org_id=org_id,
                    key=key,
                    prefix=prefix,
                    value=0,
                    suffix=suffix,
                )
                db.add(counter)
                db.flush()
        except IntegrityError:
            # Параллельная вставка той же строки (PostgreSQL).
            counter = db.scalar(stmt)
            assert counter is not None

        if for_update:
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
    return num, _format_number(counter, num, width)


def allocation_section(db: Session):
    """Контекст для SQLite: держать lock на участке allocate … commit.

    На PostgreSQL — no-op (достаточно SELECT FOR UPDATE до commit вызывающего).
    """
    dialect = db.bind.dialect.name if db.bind is not None else ""
    return _sqlite_lock if dialect == "sqlite" else nullcontext()


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

    PostgreSQL — SELECT … FOR UPDATE в транзакции вызывающего.
    SQLite — процессный RLock (прод использует PostgreSQL). Для параллельных
    потоков на SQLite оборачивайте allocate+commit в ``allocation_section``.
    """
    dialect = db.bind.dialect.name if db.bind is not None else ""
    for_update = dialect == "postgresql"

    with allocation_section(db):
        return _allocate_in_session(
            db,
            org_id,
            key,
            prefix=prefix,
            suffix=suffix,
            width=width,
            for_update=for_update,
        )
