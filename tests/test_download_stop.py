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
    clear_download_halt,
    download_stop_requested,
    download_tracker,
    downloads_are_halted,
    release_download_batch,
    try_claim_download_batch,
)


def _reset_download_state():
    clear_download_halt()
    clear_download_batch_owner()
    download_tracker.reset()


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
    _reset_download_state()
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
    assert downloads_are_halted() is True
    clear_download_halt()


def test_stop_downloads_cancels_active_batch_and_clears_rows(tmp_db):
    _reset_download_state()
    with session_scope() as session:
        paper = Paper(
            title="Active PDF",
            status=PaperStatus.OA_AVAILABLE.value,
            pdf_url="https://example.com/c.pdf",
        )
        session.add(paper)
        session.flush()
        upsert_download(session, paper.id, pdf_url=paper.pdf_url, status=PaperStatus.DOWNLOADING.value)
    token = try_claim_download_batch(2, "Active")
    assert token is not None
    result = stop_downloads(clear_stuck=True)
    assert result["was_active"] is True
    assert result["cleared"] == 1
    assert result.get("forced") is False
    assert download_tracker.is_cancelled() is True
    assert download_stop_requested() is True
    # Next claim must still see the halt (do not auto-resume).
    release_download_batch(token)
    clear_download_batch_owner()
    next_token = try_claim_download_batch(1, "Should stay halted")
    assert next_token is not None
    assert download_stop_requested() is True
    release_download_batch(next_token)
    _reset_download_state()


def test_clear_download_halt_allows_downloads_again(tmp_db):
    _reset_download_state()
    stop_downloads(clear_stuck=False)
    assert downloads_are_halted() is True
    clear_download_halt()
    assert downloads_are_halted() is False
    assert download_stop_requested() is False


def test_second_stop_force_clears_stuck_batch(tmp_db):
    from app.services.download_queue import enqueue_oa_download, force_reset_download_queue
    from app.services.progress import force_clear_download_batch

    _reset_download_state()
    force_reset_download_queue()
    token = try_claim_download_batch(3, "Hung batch")
    assert token is not None
    first = stop_downloads(clear_stuck=True)
    assert first["was_active"] is True
    assert first.get("forced") is False
    assert download_tracker.snapshot()["active"] is True
    assert enqueue_oa_download(search_id=None, user_id=1, work_type="ebook") is False

    second = stop_downloads(clear_stuck=True)
    assert second.get("forced") is True
    assert download_tracker.snapshot()["active"] is False
    assert download_tracker.is_cancelled() is False
    assert enqueue_oa_download(search_id=None, user_id=1, work_type="ebook") is True
    force_reset_download_queue()
    force_clear_download_batch()
    _reset_download_state()
