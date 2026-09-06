"""Download tracker ownership must not finish another batch mid-flight."""

from __future__ import annotations

import asyncio

import pytest

from app.models.paper import PaperRecord, PaperStatus
from app.services.download_service import DownloadService, download_papers_parallel
from app.services.progress import (
    clear_download_batch_owner,
    download_tracker,
    release_download_batch,
    try_claim_download_batch,
)


@pytest.fixture(autouse=True)
def _reset_download_tracker():
    clear_download_batch_owner()
    download_tracker.reset()
    yield
    clear_download_batch_owner()
    download_tracker.reset()


def test_try_claim_rejects_second_owner():
    first = try_claim_download_batch(2, "first")
    assert first is not None
    assert try_claim_download_batch(1, "second") is None
    release_download_batch(first)
    assert download_tracker.snapshot()["active"] is False
    second = try_claim_download_batch(1, "second")
    assert second is not None
    release_download_batch(second)


def test_release_ignores_foreign_token():
    token = try_claim_download_batch(1, "owned")
    release_download_batch(object())
    assert download_tracker.snapshot()["active"] is True
    release_download_batch(token)
    assert download_tracker.snapshot()["active"] is False


@pytest.mark.asyncio
async def test_parallel_batches_serialize_finish(monkeypatch):
    """A second Downloads-page batch waits; finish_batch only runs once the owner ends."""

    class _Client:
        pass

    provider = DownloadService(_Client())  # type: ignore[arg-type]
    provider.config.env.max_concurrent_downloads = 2
    events: list[str] = []

    async def fake_download(paper_id, paper, topic_slug, **kwargs):
        events.append(f"start-{paper_id}")
        await asyncio.sleep(0.05)
        paper.status = PaperStatus.DOWNLOADED
        events.append(f"done-{paper_id}")
        return paper

    monkeypatch.setattr(provider, "download_paper", fake_download)

    async def batch_a():
        jobs = [(1, PaperRecord(title="A1", source_provider="test"))]
        await download_papers_parallel(provider, jobs, topic_slug="t", use_download_tracker=True)
        events.append("a-finished")

    async def batch_b():
        await asyncio.sleep(0.01)
        jobs = [
            (2, PaperRecord(title="B1", source_provider="test")),
            (3, PaperRecord(title="B2", source_provider="test")),
        ]
        await download_papers_parallel(provider, jobs, topic_slug="t", use_download_tracker=True)
        events.append("b-finished")

    await asyncio.gather(batch_a(), batch_b())
    assert events.index("a-finished") < events.index("start-2")
    assert events.index("a-finished") < events.index("b-finished")
    assert download_tracker.snapshot()["active"] is False
    messages = [entry["message"] for entry in download_tracker.snapshot()["logs"]]
    assert any(m.startswith("Finished:") for m in messages)
    assert download_tracker.snapshot()["downloaded"] == 2


def test_robots_allowed_uses_runtime_overlay(monkeypatch):
    from app.config import load_config
    from app.utils import security

    security._robots_cache.clear()
    cfg = load_config().model_copy(deep=True)
    cfg.check_robots_txt = False
    monkeypatch.setattr("app.utils.security.get_runtime_config", lambda: cfg)
    assert security.robots_allowed("https://www.mdpi.com/foo.pdf", "CyberScholar/1.0") is True
