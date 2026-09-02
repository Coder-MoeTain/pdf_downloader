"""SQLite engine, session factory, and schema initialization."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import load_config
from app.database.models import Base
from app.utils.logger import get_logger

logger = get_logger("app.db")

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None
_lock = threading.Lock()


def _set_sqlite_pragma(dbapi_connection: object, _connection_record: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def get_engine(url: str | None = None) -> Engine:
    global _engine, _SessionFactory
    with _lock:
        if _engine is None:
            cfg = load_config()
            engine_url = url or cfg.database_url
            _engine = create_engine(
                engine_url,
                echo=False,
                future=True,
                connect_args={"check_same_thread": False, "timeout": 30},
            )
            event.listen(_engine, "connect", _set_sqlite_pragma)
            _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
        return _engine


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionFactory is not None
    return _SessionFactory


@contextmanager
def session_scope() -> Iterator[Session]:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(url: str | None = None) -> None:
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    _ensure_columns(engine)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        conn.commit()
    logger.info("Database initialized")


def _ensure_columns(engine: Engine) -> None:
    """Add columns introduced after the first create_all, for existing SQLite files."""
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(papers)")).all()
        names = {row[1] for row in rows}
        if "user_rating" not in names:
            conn.execute(text("ALTER TABLE papers ADD COLUMN user_rating INTEGER"))
        conn.commit()


def reset_engine() -> None:
    """Used by tests to swap databases."""
    global _engine, _SessionFactory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _SessionFactory = None
