"""Tests for parallel PDF downloads."""

from __future__ import annotations

import asyncio

import pytest

from app.models.paper import PaperRecord, PaperStatus
from app.services.download_service import DownloadService, download_papers_parallel


@pytest.mark.asyncio
async def test_download_papers_parallel_respects_concurrency(monkeypatch):
    active = 0
    peak = 0
    lock = asyncio.Lock()

    class _Client:
        pass

    provider = DownloadService(_Client())  # type: ignore[arg-type]
    provider.config.env.max_concurrent_downloads = 2

    async def fake_download(session, paper_id, paper, topic_slug, **kwargs):
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.05)
        async with lock:
            active -= 1
        paper.status = PaperStatus.DOWNLOADED
        return paper

    monkeypatch.setattr(provider, "download_paper", fake_download)

    from contextlib import contextmanager

    @contextmanager
    def fake_session():
        yield object()

    monkeypatch.setattr("app.database.connection.session_scope", fake_session)
    monkeypatch.setattr("app.database.repository.save_paper", lambda _session, paper: paper)

    jobs = [
        (index, PaperRecord(title=f"Paper {index}", source_provider="test"))
        for index in range(1, 6)
    ]
    results = await download_papers_parallel(
        provider,
        jobs,
        topic_slug="parallel-test",
        concurrency=2,
        use_download_tracker=False,
    )
    assert len(results) == 5
    assert peak <= 2
    assert all(record.status == PaperStatus.DOWNLOADED for _, record in results)
