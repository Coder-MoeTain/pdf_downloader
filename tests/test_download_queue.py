"""Tests for background OA download queue."""

from __future__ import annotations

import pytest

from app.services.download_queue import DownloadJob, enqueue_oa_download, oa_download_active
from app.services.progress import download_tracker


def test_enqueue_oa_download_rejects_when_active():
    download_tracker.start_batch(3, "Test batch")
    try:
        assert oa_download_active()
        assert enqueue_oa_download(search_id=None, user_id=1) is False
    finally:
        download_tracker.finish_batch()


def test_enqueue_oa_download_accepts_when_idle():
    from app.services.download_queue import _queue

    download_tracker.reset()
    while not _queue.empty():
        _queue.get_nowait()
        _queue.task_done()
    assert enqueue_oa_download(search_id=5, user_id=2) is True
    while not _queue.empty():
        _queue.get_nowait()
        _queue.task_done()


@pytest.mark.asyncio
async def test_download_worker_processes_queue(monkeypatch):
    from app.services import download_queue

    download_tracker.reset()
    while not download_queue._queue.empty():
        download_queue._queue.get_nowait()
        download_queue._queue.task_done()
    download_queue._running = False
    calls: list[DownloadJob] = []

    def fake_run(search_id, user_id):
        calls.append(DownloadJob(search_id=search_id, user_id=user_id))
        return {"downloaded": 1, "failed": 0, "skipped": 0}

    monkeypatch.setattr(download_queue, "_run_batch_sync", fake_run)
    await download_queue.start_download_worker()
    assert enqueue_oa_download(search_id=9, user_id=3) is True
    await download_queue._queue.join()
    assert calls == [DownloadJob(search_id=9, user_id=3)]
    await download_queue.stop_download_worker()
