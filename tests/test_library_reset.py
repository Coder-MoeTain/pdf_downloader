"""Tests for library reset."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from app.database.connection import session_scope
from app.database.models import Paper, SearchJob, SearchQuery
from app.database.repository import enqueue_search_job, save_paper
from app.models.paper import PaperRecord, PaperStatus
from app.services.library_reset import reset_library_repository


def test_reset_clears_searches_papers_and_pdfs(tmp_db, monkeypatch, tmp_path):
    from app.config import get_runtime_config

    cfg = get_runtime_config()
    library = tmp_path / "library"
    topic = library / "topic" / "2024"
    topic.mkdir(parents=True)
    pdf_path = topic / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    monkeypatch.setattr(cfg, "library_dir", library)

    with session_scope() as session:
        paper = save_paper(
            session,
            PaperRecord(
                title="Reset me",
                doi="10.1000/reset",
                status=PaperStatus.DOWNLOADED,
                source_provider="test",
            ),
        )
        from app.database.repository import upsert_download

        upsert_download(
            session,
            paper.id,
            pdf_url="https://example.com/paper.pdf",
            status=PaperStatus.DOWNLOADED.value,
            local_path=str(pdf_path),
        )
        enqueue_search_job(session, user_id=None, query="reset test", filters={"query": "reset test"})

    stats = reset_library_repository(cfg)
    assert stats.papers == 1
    assert stats.search_jobs == 1
    assert stats.pdf_files_removed >= 1
    assert not pdf_path.exists()

    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Paper)) == 0
        assert session.scalar(select(func.count()).select_from(SearchJob)) == 0
        assert session.scalar(select(func.count()).select_from(SearchQuery)) == 0
