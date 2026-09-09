"""Счётчики номеров документов с блокировкой строки."""

from __future__ import annotations

import threading
from contextlib import nullcontext
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Counter
from app.services.numbering import render_number, template_has_year

# RLock: тест параллельной выдачи держит lock через allocate+commit.
_sqlite_lock = threading.RLock()


def _calendar_day(on: date | None) -> date:
    return on or datetime.now(timezone.utc).date()


def _format_number(
    counter: Counter,
    num: int,
    width: int,
    *,
    on: date | None = None,
    call_suffix: str = "",
) -> str:
    """Собрать номер: шаблон W-49 или legacy prefix+n+suffix.

    call_suffix — суффикс с вызова (например «/26» из counter_year_suffix).
    Если в шаблоне уже есть {гг}/{гггг}, call_suffix не дублируем.
    """
    tmpl = counter.number_template
    pref = counter.prefix or ""
    stored_suf = counter.suffix or ""
    if tmpl and template_has_year(tmpl):
        suf = stored_suf
    else:
        suf = call_suffix if call_suffix else stored_suf
    return render_number(
        tmpl,
        n=num,
        prefix=pref,
        suffix=suf,
        width=width,
        on=on,
    )


def _maybe_yearly_reset(counter: Counter, *, on: date | None = None) -> None:
    if not counter.reset_yearly:
        return
    year = _calendar_day(on).year
    if counter.cycle_year is None:
        counter.cycle_year = year
        return
    if int(counter.cycle_year) < year:
        counter.value = 0
        counter.cycle_year = year


def _allocate_in_session(
    db: Session,
    org_id: int,
    key: str,
    *,
    prefix: str,
    suffix: str,
    width: int,
    for_update: bool,
    on: date | None = None,
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
                    number_template=None,
                    reset_yearly=False,
                    cycle_year=_calendar_day(on).year,
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
    if (
        suffix
        and not counter.suffix
        and not (counter.number_template and template_has_year(counter.number_template))
    ):
        counter.suffix = suffix

    _maybe_yearly_reset(counter, on=on)
    if counter.cycle_year is None:
        counter.cycle_year = _calendar_day(on).year

    counter.value = int(counter.value) + 1
    db.flush()
    num = counter.value
    return num, _format_number(counter, num, width, on=on, call_suffix=suffix)


def allocation_section(db: Session):
    """Контекст для SQLite: держать lock на участке allocate … commit.

    На PostgreSQL — no-op (достаточно SELECT FOR UPDATE до commit вызывающего).
    """
    dialect = db.bind.dialect.name if db.bind is not None else ""
    return _sqlite_lock if dialect == "sqlite" else nullcontext()


def peek_number(
    db: Session,
    org_id: int,
    key: str,
    *,
    prefix: str = "",
    suffix: str = "",
    width: int = 0,
    on: date | None = None,
) -> str:
    """Следующий номер без инкремента (для placeholder в форме, T8)."""
    counter = db.scalar(
        select(Counter).where(Counter.org_id == org_id, Counter.key == key)
    )
    day = _calendar_day(on)
    if counter is None:
        nxt = 1
        pref, suf = prefix, suffix
        tmpl = None
        # синтетический Counter для форматтера не нужен
        from app.services.numbering import render_number

        return render_number(tmpl, n=nxt, prefix=pref, suffix=suf, width=width, on=day)

    # Учесть годовой сброс без записи
    value = int(counter.value)
    if counter.reset_yearly and counter.cycle_year is not None and int(counter.cycle_year) < day.year:
        value = 0
    nxt = value + 1
    return _format_number(counter, nxt, width, on=day, call_suffix=suffix)


def allocate_number(
    db: Session,
    org_id: int,
    key: str,
    *,
    prefix: str = "",
    suffix: str = "",
    width: int = 0,
    on: date | None = None,
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
            on=on,
        )
