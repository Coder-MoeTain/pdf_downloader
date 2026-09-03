from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app.database.connection import (
    SQLITE_BUSY_TIMEOUT_MS,
    get_engine,
    is_sqlite_lock_error,
    retry_on_sqlite_lock,
    session_scope,
)
from app.database.models import Paper
from app.database.repository import save_paper
from app.models.paper import AuthorRecord, PaperRecord, PaperStatus


def test_save_and_find_by_doi(tmp_db):
    record = PaperRecord(
        title="Test Paper",
        doi="10.1000/save-me",
        authors=[AuthorRecord(name="Grace Hopper")],
        publication_year=2024,
        status=PaperStatus.FOUND,
        source_provider="crossref",
    )
    with session_scope() as session:
        paper = save_paper(session, record)
        assert paper.id is not None
        found = session.scalar(select(Paper).where(Paper.doi == "10.1000/save-me"))
        assert found is not None
        assert found.title == "Test Paper"
        assert found.normalized_title
        assert found.authors


def test_sqlite_uses_wal_and_busy_timeout(tmp_db):
    engine = get_engine()
    with engine.connect() as conn:
        journal = conn.execute(text("PRAGMA journal_mode")).scalar()
        busy = conn.execute(text("PRAGMA busy_timeout")).scalar()
    assert str(journal).lower() == "wal"
    assert int(busy) >= SQLITE_BUSY_TIMEOUT_MS


def test_retry_on_sqlite_lock():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise OperationalError("INSERT", {}, Exception("database is locked"))
        return "ok"

    assert retry_on_sqlite_lock(flaky, attempts=5) == "ok"
    assert calls["n"] == 3
    assert is_sqlite_lock_error(OperationalError("INSERT", {}, Exception("database is locked")))
