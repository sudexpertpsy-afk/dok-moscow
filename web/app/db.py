"""Подключение к БД (SQLAlchemy 2)."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

log = logging.getLogger("dok.sql")


class Base(DeclarativeBase):
    pass


def _make_engine(db_url: str | None = None):
    settings = get_settings()
    url = db_url or settings.db_url
    connect_args = {}
    kwargs: dict = {"pool_pre_ping": True, "connect_args": connect_args}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    else:
        kwargs["pool_size"] = int(settings.db_pool_size)
        kwargs["max_overflow"] = int(settings.db_max_overflow)
    eng = create_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _sqlite_pragma(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
            dbapi_conn.execute("PRAGMA busy_timeout=5000")
            try:
                dbapi_conn.execute("PRAGMA journal_mode=WAL")
            except Exception:
                pass

    # W-30: медленные SQL в dev (SQL_SLOW_MS, по умолчанию 200 при DOK_ENV=dev)
    slow_ms = os.environ.get("SQL_SLOW_MS")
    if slow_ms is None and os.environ.get("DOK_ENV", "").lower() in {"dev", "development"}:
        slow_ms = "200"
    if slow_ms:

        threshold = float(slow_ms) / 1000.0

        @event.listens_for(eng, "before_cursor_execute")
        def _before_cursor(conn, _cursor, _statement, _parameters, _context, _executemany):
            conn.info["query_start_time"] = time.perf_counter()

        @event.listens_for(eng, "after_cursor_execute")
        def _after_cursor(conn, _cursor, statement, _parameters, _context, _executemany):
            started = conn.info.get("query_start_time")
            if started is None:
                return
            elapsed = time.perf_counter() - started
            if elapsed >= threshold:
                log.warning("slow SQL %.0fms: %s", elapsed * 1000, statement[:500])

    return eng


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def reset_engine(db_url: str) -> None:
    """Пересоздать engine (для тестов)."""
    global engine, SessionLocal
    engine.dispose()
    engine = _make_engine(db_url)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
