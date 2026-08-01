"""Подключение к БД (SQLAlchemy 2)."""

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


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
