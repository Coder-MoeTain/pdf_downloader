from __future__ import annotations

import pytest

from app.database.connection import init_db, reset_engine
from app.config import load_config


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "research.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    load_config.cache_clear()
    reset_engine()
    init_db(f"sqlite:///{db_path.as_posix()}")
    yield db_path
    reset_engine()
    load_config.cache_clear()
