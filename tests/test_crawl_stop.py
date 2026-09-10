"""Tests for cooperative crawl stop while the job runs in a worker thread."""

from __future__ import annotations

import pytest

from app.config import AppConfig
from app.services.crawl_service import CrawlCancelled, CrawlService
from app.services.progress import ProgressTracker


@pytest.mark.asyncio
async def test_crawl_checkpoint_raises_when_cancelled():
    progress = ProgressTracker()
    progress.queue_crawl("arxiv")
    progress.mark_crawl_started("arxiv")
    service = CrawlService(config=AppConfig(), progress=progress)
    progress.request_cancel("Stopping crawl…")
    with pytest.raises(CrawlCancelled):
        await service._checkpoint()


def test_cancel_crawl_signals_progress_without_killing_waiter(tmp_db):
    from app.database.connection import session_scope
    from app.database.models import User
    from app.database.repository import enqueue_crawl_job, get_crawl_job
    from app.services import crawl_queue
    from app.services.progress import crawl_job_registry

    with session_scope() as session:
        user = User(
            google_id="local:stop@test.local",
            email="stop@test.local",
            name="Stop",
            role="admin",
            is_admin=True,
        )
        session.add(user)
        session.flush()
        job = enqueue_crawl_job(
            session,
            user_id=user.id,
            source="arxiv",
            filters={"source": "arxiv", "query": "", "skip_existing": True, "download": False},
        )
        job.status = "running"
        job_id = job.id
        user_id = user.id

    progress = crawl_job_registry.register_queued_crawl(job_id, "arxiv")
    progress.mark_crawl_started("arxiv")
    assert progress.is_cancelled() is False

    was = crawl_queue.cancel_crawl(job_id, user_id=user_id, is_admin=True)
    assert was == "running"
    assert progress.is_cancelled() is True
    assert "Stopping" in (progress.snapshot().get("message") or "")

    with session_scope() as session:
        row = get_crawl_job(session, job_id)
        assert row is not None
        assert row.status == "cancelled"
