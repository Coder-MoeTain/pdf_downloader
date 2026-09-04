"""SQLite engine, session factory, and schema initialization."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import load_config
from app.database.models import Base
from app.utils.logger import get_logger

logger = get_logger("app.db")

SQLITE_BUSY_TIMEOUT_SECONDS = 60
SQLITE_BUSY_TIMEOUT_MS = SQLITE_BUSY_TIMEOUT_SECONDS * 1000

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None
_lock = threading.Lock()
T = TypeVar("T")


def _set_sqlite_pragma(dbapi_connection: object, _connection_record: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def is_sqlite_lock_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message


def retry_on_sqlite_lock(fn: Callable[[], T], *, attempts: int = 8) -> T:
    """Retry a short SQLite write if another request is holding the library file."""
    delay = 0.12
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except OperationalError as exc:
            if not is_sqlite_lock_error(exc) or attempt >= attempts:
                raise
            logger.warning("SQLite locked; retrying %s/%s", attempt, attempts)
            time.sleep(delay)
            delay = min(delay * 2, 2.0)
    raise RuntimeError("SQLite lock retries exhausted")


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
                pool_pre_ping=True,
                connect_args={"check_same_thread": False, "timeout": SQLITE_BUSY_TIMEOUT_SECONDS},
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
    try:
        from app.database.settings_store import init_settings_store

        init_settings_store()
    except Exception as exc:
        logger.warning("Settings store failed to initialize: %s", exc)


def _ensure_columns(engine: Engine) -> None:
    """Add columns introduced after the first create_all, for existing SQLite files."""
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(papers)")).all()
        names = {row[1] for row in rows}
        if "user_rating" not in names:
            conn.execute(text("ALTER TABLE papers ADD COLUMN user_rating INTEGER"))
        user_rows = conn.execute(text("PRAGMA table_info(users)")).all()
        user_names = {row[1] for row in user_rows}
        if user_names:
            if "password_hash" not in user_names:
                conn.execute(text("ALTER TABLE users ADD COLUMN password_hash TEXT"))
            if "role" not in user_names:
                conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(16) DEFAULT 'user'"))
                conn.execute(text("UPDATE users SET role = 'admin' WHERE is_admin = 1 AND (role IS NULL OR role = '')"))
                conn.execute(text("UPDATE users SET role = 'user' WHERE role IS NULL OR role = ''"))
            if "last_seen_at" not in user_names:
                conn.execute(text("ALTER TABLE users ADD COLUMN last_seen_at DATETIME"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_downloads_sha256 ON downloads (sha256)"))
        download_rows = conn.execute(text("PRAGMA table_info(downloads)")).all()
        download_names = {row[1] for row in download_rows}
        if "downloaded_by_user_id" not in download_names:
            conn.execute(text("ALTER TABLE downloads ADD COLUMN downloaded_by_user_id INTEGER"))
        search_rows = conn.execute(text("PRAGMA table_info(search_queries)")).all()
        search_names = {row[1] for row in search_rows}
        if search_names and "user_id" not in search_names:
            conn.execute(text("ALTER TABLE search_queries ADD COLUMN user_id INTEGER"))
        conn.commit()


def reset_engine() -> None:
    """Used by tests to swap databases."""
    global _engine, _SessionFactory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _SessionFactory = None
