"""MySQL engine for application settings. Falls back to SQLite if MySQL is unavailable."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import ROOT_DIR, load_config
from app.database.settings_models import SettingsBase
from app.utils.logger import get_logger

logger = get_logger("app.settings_db")

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None
_lock = threading.Lock()
_status: dict[str, str | bool] = {
    "backend": "sqlite",
    "connected": False,
    "label": "Not initialized",
    "host": "",
    "database": "",
    "error": "",
}


@dataclass
class SettingsStoreStatus:
    backend: str
    connected: bool
    label: str
    host: str
    database: str
    error: str = ""

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "backend": self.backend,
            "connected": self.connected,
            "label": self.label,
            "host": self.host,
            "database": self.database,
            "error": self.error,
        }


def store_status() -> SettingsStoreStatus:
    return SettingsStoreStatus(
        backend=str(_status["backend"]),
        connected=bool(_status["connected"]),
        label=str(_status["label"]),
        host=str(_status["host"]),
        database=str(_status["database"]),
        error=str(_status.get("error") or ""),
    )


def _mysql_url(host: str, port: int, user: str, password: str, database: str | None) -> str:
    auth = quote_plus(user)
    if password:
        auth += ":" + quote_plus(password)
    db = f"/{database}" if database else "/"
    return f"mysql+pymysql://{auth}@{host}:{port}{db}?charset=utf8mb4"


def _ensure_mysql_database(host: str, port: int, user: str, password: str, database: str) -> None:
    engine = create_engine(_mysql_url(host, port, user, password, None), echo=False, future=True, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{database}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
            conn.commit()
    finally:
        engine.dispose()


def _sqlite_url() -> str:
    cfg = load_config()
    path = ROOT_DIR / (cfg.env.settings_sqlite_path or "data/settings.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path.as_posix()}"


def _try_mysql() -> Engine | None:
    cfg = load_config().env
    host = (cfg.mysql_host or "").strip()
    if not host:
        return None
    database = (cfg.mysql_database or "research_collector").strip()
    try:
        _ensure_mysql_database(host, cfg.mysql_port, cfg.mysql_user, cfg.mysql_password, database)
        engine = create_engine(
            _mysql_url(host, cfg.mysql_port, cfg.mysql_user, cfg.mysql_password, database),
            echo=False,
            future=True,
            pool_pre_ping=True,
            pool_recycle=280,
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        _status.update(
            {
                "backend": "mysql",
                "connected": True,
                "label": "MySQL / MariaDB",
                "host": f"{host}:{cfg.mysql_port}",
                "database": database,
                "error": "",
            }
        )
        logger.info("Settings store connected to MySQL %s/%s", host, database)
        return engine
    except Exception as exc:
        logger.warning("MySQL settings store unavailable (%s); falling back to SQLite", exc)
        _status["error"] = str(exc)
        return None


def get_settings_engine() -> Engine:
    global _engine, _SessionFactory
    with _lock:
        if _engine is None:
            engine = _try_mysql()
            if engine is None:
                engine = create_engine(
                    _sqlite_url(),
                    echo=False,
                    future=True,
                connect_args={"check_same_thread": False, "timeout": 60},
                )
                if not _status.get("error"):
                    _status["error"] = ""
                _status.update(
                    {
                        "backend": "sqlite",
                        "connected": True,
                        "label": "SQLite (MySQL not configured or unreachable)",
                        "host": "local file",
                        "database": load_config().env.settings_sqlite_path,
                    }
                )
                logger.info("Settings store using SQLite fallback")
            _engine = engine
            _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
        return _engine


def get_settings_session_factory() -> sessionmaker[Session]:
    get_settings_engine()
    assert _SessionFactory is not None
    return _SessionFactory


@contextmanager
def settings_session() -> Iterator[Session]:
    factory = get_settings_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_settings_store() -> SettingsStoreStatus:
    engine = get_settings_engine()
    SettingsBase.metadata.create_all(engine)
    from app.database.settings_repository import seed_academic_sources, seed_default_settings

    seed_default_settings()
    seed_academic_sources()
    return store_status()


def reset_settings_engine() -> None:
    global _engine, _SessionFactory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _SessionFactory = None
        _status.update(
            {
                "backend": "sqlite",
                "connected": False,
                "label": "Not initialized",
                "host": "",
                "database": "",
                "error": "",
            }
        )
