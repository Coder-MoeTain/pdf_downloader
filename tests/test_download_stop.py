"""Stop / resume helpers for stuck DOWNLOADING rows."""

from __future__ import annotations

from sqlalchemy import select

from app.database.connection import session_scope
from app.database.models import Download, Paper
from app.database.repository import mark_downloading_stopped, upsert_download
from app.models.paper import PaperStatus
from app.services.download_service import stop_downloads
from app.services.progress import (
    clear_download_batch_owner,
    download_tracker,
    release_download_batch,
    try_claim_download_batch,
)


def test_mark_downloading_stopped(tmp_db):
    with session_scope() as session:
        paper = Paper(
            title="Stuck PDF",
            status=PaperStatus.OA_AVAILABLE.value,
            pdf_url="https://example.com/a.pdf",
        )
        session.add(paper)
        session.flush()
        upsert_download(session, paper.id, pdf_url=paper.pdf_url, status=PaperStatus.DOWNLOADING.value)
        paper_id = paper.id
    with session_scope() as session:
        assert mark_downloading_stopped(session, paper_id=paper_id) == 1
        row = session.scalar(select(Download).where(Download.paper_id == paper_id))
        paper = session.get(Paper, paper_id)
        assert row is not None and row.status == PaperStatus.FAILED.value
        assert row.error_message == "Stopped by user"
        assert paper is not None and paper.status == PaperStatus.FAILED.value


def test_stop_downloads_clears_orphans_when_idle(tmp_db):
    clear_download_batch_owner()
    download_tracker.reset()
    with session_scope() as session:
        paper = Paper(
            title="Orphan",
            status=PaperStatus.FOUND.value,
            pdf_url="https://example.com/b.pdf",
        )
        session.add(paper)
        session.flush()
        upsert_download(session, paper.id, pdf_url=paper.pdf_url, status=PaperStatus.DOWNLOADING.value)
    result = stop_downloads(clear_stuck=True)
    assert result["was_active"] is False
    assert result["cleared"] == 1


def test_stop_downloads_cancels_active_batch(tmp_db):
    clear_download_batch_owner()
    download_tracker.reset()
    token = try_claim_download_batch(2, "Active")
    assert token is not None
    result = stop_downloads(clear_stuck=True)
    assert result["was_active"] is True
    assert download_tracker.is_cancelled() is True
    release_download_batch(token)
    clear_download_batch_owner()
    download_tracker.reset()
