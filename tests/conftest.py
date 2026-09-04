from __future__ import annotations

import pytest

from app.database.connection import init_db, reset_engine
from app.database.settings_store import reset_settings_engine
from app.config import load_config


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "research.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("MYSQL_HOST", "")
    monkeypatch.setenv("LMS_ROOT", "")
    monkeypatch.setenv("LMS_SYNC_ENABLED", "true")
    monkeypatch.setenv("SETTINGS_SQLITE_PATH", str(tmp_path / "settings.db"))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")
    monkeypatch.setenv("GOOGLE_ADMIN_EMAILS", "")
    load_config.cache_clear()
    reset_engine()
    reset_settings_engine()
    init_db(f"sqlite:///{db_path.as_posix()}")
    yield db_path
    reset_engine()
    reset_settings_engine()
    load_config.cache_clear()
